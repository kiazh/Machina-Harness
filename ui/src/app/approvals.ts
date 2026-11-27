// Approval and structured-question controller: the exact-review dock, one
// decision per captured session binding, retained evidence across navigation,
// server-side rehydration, and the one-at-a-time question picker.
//
// Interface invariants:
// - A card is bound to the session/turn captured when the pause frame
//   arrived. Every resolve revalidates that binding immediately before
//   dispatch: a wrong-session, retired-turn or post-navigation decision never
//   leaves this flow.
// - Evidence is retained in TurnSessionStore while its session is hidden so a
//   return can rebuild the card; exit and disconnect invalidate without
//   issuing a resolve RPC.
// - The dock shows the first retained dialog, hides competing overlays while
//   it owns Chat, and restores them (or the composer) when it releases.
// - Rehydration is one bounded `approvals.list` read per return, fenced by
//   the visible session and its live run; it never decides anything.
// - Questions are parked for hidden sessions and flushed in order; one picker
//   per request id is open at a time.
import type { SelectItem } from "@earendil-works/pi-tui";
import { ApprovalCard, type ApprovalDecision } from "../components/ApprovalCard.ts";
import { describeError } from "../debug.ts";
import type { GatewayEvent } from "../protocol.ts";
import type { ApprovalHost } from "./contracts.ts";

type ApprovalPromptEvent = Extract<GatewayEvent, { type: "needs_approval" | "permission.required" }>;
type QuestionPromptEvent = Extract<GatewayEvent, { type: "question.asked" }>;

export class ApprovalController {
	private readonly host: ApprovalHost;
	/** Questions waiting for their owning session to become visible. */
	private readonly pendingQuestions = new Map<string, QuestionPromptEvent[]>();
	/** Question request IDs whose picker is already open. */
	private readonly openQuestions = new Set<string>();

	constructor(host: ApprovalHost) {
		this.host = host;
	}

	/**
	 * Exact approval review: capture the immutable event binding, open a
	 * dedicated review card for the bound visible session, and resolve once
	 * through approvals.resolve with the supported session binding. Repeat
	 * events for an open request are ignored; navigation, exit and
	 * disconnect invalidate the local prompt without any resolve RPC.
	 */
	prompt(event: ApprovalPromptEvent): void {
		const binding = {
			requestId: event.requestId,
			sessionId: event.sessionId,
			originTurnId: event.originTurnId,
			transportTurnId: event.turnId,
		};
		if (this.host.turnSession.hasApprovalDialog(binding.requestId)) return;
		// A paused delegated child keeps its run in `delegatedRunning`; a
		// parent keeps a local in-flight turn. Anything else is a stale pause
		// and must not take over the dock.
		if (!this.host.turnSession.isRunning(event.sessionId)) return;
		this.host.log.info("approval.prompt", {
			request: binding.requestId.slice(0, 12),
			session: binding.sessionId,
			operation: event.operation,
			tool: event.tool ?? null,
			target: event.target,
			backend: event.backend,
			timeout: event.timeout ?? null,
		});
		const fileDiff = event.fileDiff && typeof event.fileDiff === "object"
			? event.fileDiff as { diff?: unknown; truncated?: unknown }
			: undefined;
		const card = new ApprovalCard(
			{
				requestId: event.requestId,
				sessionId: event.sessionId,
				originTurnId: event.originTurnId,
				transportTurnId: event.turnId,
				operation: event.operation,
				tool: event.tool,
				target: event.target,
				argv: event.argv ?? [],
				cwd: event.cwd,
				backend: event.backend,
				shell: event.shell,
				timeout: event.timeout,
				capabilities: event.capabilities ?? [],
				network: event.network,
				contentDigest: event.contentDigest,
				contentPreview: event.contentPreview,
				contentLength: event.contentLength,
				fileDiff: fileDiff && typeof fileDiff.diff === "string"
					? { diff: fileDiff.diff, truncated: fileDiff.truncated === true }
					: undefined,
				durable: event.durable,
			},
			{
				tui: this.host.tui,
				docked: true,
				onDecision: (decision) => void this.resolve(binding.requestId, decision),
			},
		);
		this.host.transcript.pauseForApproval();
		let finish: () => void = () => {};
		const dialog = new Promise<void>((done) => { finish = done; });
		let settled = false;
		const close = () => {
			if (settled) return;
			settled = true;
			this.host.turnSession.deleteApprovalDialog(binding.requestId);
			finish();
			this.showDock();
		};
		this.host.turnSession.setApprovalDialog(binding.requestId, { ...binding, card, evidenceAdded: false, close, dialog });
		this.showDock();
		this.host.tui.requestRender();
	}

	showDock(): void {
		const next = this.host.turnSession.firstApprovalDialog();
		this.host.log.debug("approval.dock", {
			waiting: this.host.turnSession.approvalDialogCount(),
			view: this.host.view(),
			request: next?.requestId.slice(0, 12) ?? null,
		});
		if (next && !next.evidenceAdded) {
			this.host.transcript.addComponent(next.card.evidence);
			next.evidenceAdded = true;
		}
		this.host.setDock(next?.card);
		if (next) this.host.footer.status = "awaiting_approval";
		else if (this.host.footer.status === "awaiting_approval") this.host.footer.status = this.host.running() ? "working" : "ready";
		this.host.invalidateChrome();
		// The dock owns the decision. A Quick Open list underneath would keep
		// painting and compete for keys, so close it rather than leave it hidden.
		if (next) this.host.closeQuickOpen();
		const previous = this.host.dialogs.at(-1);
		if (next && this.host.view() === "chat") {
			for (const dialog of this.host.dialogs) { dialog.handle.setHidden(true); dialog.hidden = true; }
			this.host.scrollTranscriptToEnd();
			this.host.tui.setFocus(next.card);
		} else if (!next && previous) {
			// The dock is gone: restore the dialog it hid so the draft is never
			// stranded, and hand it the keys back.
			for (const dialog of this.host.dialogs) {
				if (dialog.hidden) { dialog.handle.setHidden(false); dialog.hidden = false; }
			}
			previous.handle.focus();
		} else if (!next && this.host.view() === "chat" && !this.host.isExiting()) {
			// Releasing the last approval on Chat returns focus to the composer
			// (finding 3) through the region router, so the editor-scoped gates
			// read the region that actually owns keys.
			this.host.focusChat();
		}
		this.host.syncCursorBlink();
		this.host.tui.requestRender();
	}

	/** Dispatch one deliberate decision with the captured session binding. */
	async resolve(requestId: string, decision: ApprovalDecision): Promise<void> {
		const entry = this.host.turnSession.approvalDialog(requestId);
		if (!entry) return;
		// Revalidate immediately before dispatch: wrong-session, retired-turn
		// and post-navigation decisions never leave this UI flow. A delegated
		// child has no local prompt turn; its live `delegatedRunning` marker is
		// the binding, and the resolve still carries the child session id.
		const delegated = this.host.turnSession.delegatedRunning.has(entry.sessionId);
		const record = this.host.turnSession.turnBinding(entry.sessionId);
		const turnBound = record !== undefined
			&& this.host.turnSession.isInFlight(entry.sessionId)
			&& (record.turnId === entry.transportTurnId || (entry.originTurnId !== undefined && record.turnId === entry.originTurnId));
		const bound = entry.sessionId === this.host.current()?.id && (delegated || turnBound);
		if (!bound) {
			this.host.log.warn("approval.unbound", { request: requestId.slice(0, 12), session: entry.sessionId });
			entry.close();
			this.host.turnSession.deletePendingApproval(requestId);
			return;
		}
		entry.card.setResolving(true);
		this.host.log.info("approval.decide", {
			request: requestId.slice(0, 12),
			session: entry.sessionId,
			verdict: decision.verdict,
			grant: decision.grant,
		});
		this.host.tui.requestRender();
		try {
			await this.host.clientRequest("approvals.resolve", {
				requestId: entry.requestId,
				sessionId: entry.sessionId,
				verdict: decision.verdict,
				grant: decision.grant,
			});
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			const terminal = /unknown approval|already consumed|not permitted|expired|does not belong/i.test(message);
			this.host.log.warn("approval.failed", { request: requestId.slice(0, 12), error: describeError(error) });
			if (terminal) {
				// A request that can never resolve is stale (expired, consumed
				// elsewhere, or owned by another session). Drop the dead card so
				// it cannot block every later pending approval behind it.
				this.host.turnSession.deletePendingApproval(requestId);
				entry.close();
				this.host.transcript.addNotice(
					`Approval ${requestId.slice(0, 8)} could not be resolved: ${message}. Reloading pending approvals.`,
					"warning",
				);
				void this.rehydrate(entry.sessionId);
			} else {
				entry.card.setError(message);
				this.host.invalidateChrome(); // the error row changes the dock height
			}
			this.host.tui.requestRender();
			return;
		}
		// Decision recorded; execution is proven only by later tool events.
		this.host.log.info("approval.recorded", { request: requestId.slice(0, 12), verdict: decision.verdict });
		entry.card.setRecorded(`approval ${decision.verdict}`);
		entry.close();
		this.host.turnSession.deletePendingApproval(requestId);
		this.host.tui.requestRender();
	}

	/** Invalidate pending approval cards without resolving (navigation/exit/disconnect). */
	invalidate(sessionId?: string): void {
		for (const entry of this.host.turnSession.approvalDialogEntries()) {
			if (sessionId === undefined || entry.sessionId !== sessionId) {
				entry.close();
				this.host.turnSession.deleteApprovalDialog(entry.requestId);
			}
		}
		// Navigation retains evidence so the card can be rebuilt on return
		// (R15); exit and disconnect drop it: nothing can be decided after.
		if (sessionId === undefined) this.host.turnSession.clearPendingApprovals();
	}

	/** Retire approval evidence and cards for a session whose turn ended. */
	clear(sessionId: string): void {
		for (const [requestId, event] of this.host.turnSession.pendingApprovalEntries()) {
			if (event.sessionId === sessionId) this.host.turnSession.deletePendingApproval(requestId);
		}
		for (const entry of this.host.turnSession.approvalDialogEntries()) {
			if (entry.sessionId === sessionId) {
				entry.card.setRecorded("request ended");
				entry.close();
			}
		}
	}

	/** Rebuild review cards retained while their session was hidden (R15). */
	restore(sessionId: string): void {
		if (!this.host.turnSession.isRunning(sessionId)) return;
		for (const [requestId, event] of this.host.turnSession.pendingApprovalEntries()) {
			if (event.sessionId !== sessionId || this.host.turnSession.hasApprovalDialog(requestId)) continue;
			void this.prompt(event);
		}
	}

	/**
	 * F-30: pending approvals are parked server-side in the permission store;
	 * a session hidden while its approval arrived never received the event, so
	 * returning to it re-reads the pending rows and reopens decision-capable
	 * cards. Rows are the same redacted public view the wire event is shaped
	 * from (`machina/protocol.approval_fields`), and the synthesized event
	 * mirrors that mapping exactly. Best-effort: a failed or stale re-read
	 * keeps whatever retained prompts already show and never resolves
	 * anything by itself.
	 */
	async rehydrate(sessionId: string): Promise<void> {
		if (this.host.isExiting() || this.host.current()?.id !== sessionId || !this.host.turnSession.isRunning(sessionId)) return;
		const result = await this.host
			.clientRequest<{ approvals?: unknown }>("approvals.list", { sessionId })
			.catch(() => undefined);
		// Bind the answer to the session, not the moment the read started: a
		// return that has since navigated away publishes nothing.
		if (result === undefined || this.host.isExiting() || this.host.current()?.id !== sessionId || !this.host.turnSession.isRunning(sessionId)) return;
		const rows = Array.isArray(result.approvals) ? result.approvals : [];
		for (const row of rows) {
			const event = approvalRowEvent(sessionId, row);
			if (!event) continue;
			// Already-live or retained prompts own their card; only rows this
			// frame never saw need synthesis.
			if (this.host.turnSession.hasApprovalDialog(event.requestId) || this.host.turnSession.pendingApproval(event.requestId) !== undefined) continue;
			this.host.turnSession.rememberPendingApproval(event);
			this.prompt(event);
		}
	}

	/** Park a question whose owning session is not visible; flushed on return. */
	park(event: QuestionPromptEvent): void {
		const pending = this.pendingQuestions.get(event.sessionId) ?? [];
		if (!pending.some((item) => item.requestId === event.requestId)) pending.push(event);
		this.pendingQuestions.set(event.sessionId, pending);
	}

	/** Drop every parked question for a deleted child session. */
	discard(sessionId: string): void {
		this.pendingQuestions.delete(sessionId);
	}

	flushQuestions(sessionId: string): void {
		if (this.host.current()?.id !== sessionId) return;
		const pending = this.pendingQuestions.get(sessionId);
		if (!pending?.length) return;
		const next = pending[0]!;
		void this.promptQuestion(next);
	}

	/**
	 * One structured question at a time. The tool call is waiting for this
	 * answer, so a cancelled picker is reported explicitly rather than
	 * silently leaving the turn blocked.
	 */
	async promptQuestion(event: QuestionPromptEvent): Promise<void> {
		if (this.openQuestions.has(event.requestId)) return;
		if (this.host.current()?.id !== event.sessionId) {
			this.park(event);
			return;
		}
		this.openQuestions.add(event.requestId);
		try {
			const options = event.options.map(
				(option): SelectItem => ({
					value: String(option.id),
					label: String(option.label),
					...(option.description ? { description: String(option.description) } : {}),
				}),
			);
			if (event.allowOther) {
				options.push({ value: "__other__", label: "Other…", description: "Type a different answer" });
			}
			if (!options.length) options.push({ value: "__skip__", label: "Skip this question" });
			const choice = await this.host.pick(event.question, options);
			let answer: string | undefined;
			if (choice?.value === "__other__") {
				answer = await this.host.askText({
					title: "Your answer",
					description: event.question,
					maxLength: 4_000,
				});
			} else if (choice && choice.value !== "__skip__") {
				answer = choice.label;
			}
			await this.host.request("question.answer", {
				sessionId: event.sessionId,
				requestId: event.requestId,
				...(answer === undefined ? { cancelled: true } : { answer }),
			});
		} catch (error) {
			this.host.print(`Could not answer the question: ${message(error)}`, "error");
		} finally {
			this.openQuestions.delete(event.requestId);
			const pending = this.pendingQuestions.get(event.sessionId);
			if (pending) {
				const rest = pending.filter((item) => item.requestId !== event.requestId);
				if (rest.length) {
					this.pendingQuestions.set(event.sessionId, rest);
					this.flushQuestions(event.sessionId);
				} else {
					this.pendingQuestions.delete(event.sessionId);
				}
			}
			this.host.tui.requestRender();
		}
	}
}

function message(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

/**
 * Synthesize the wire `needs_approval` event for a stored pending row.
 *
 * `approvals.list` returns `permission_approvals.public_view` rows; the
 * gateway's live event is built from the same record by
 * `machina/protocol.approval_fields`, so the mapping below mirrors it:
 * top-level request/operation/target/status plus the proposal fields
 * (argv/shell/cwd/backend/capabilities/network/content_*). The row's
 * `turn_id` is the reviewed origin turn and doubles as the transport turn for
 * the synthesized frame, which is what makes the restored card resolvable
 * against the live turn binding.
 */
function approvalRowEvent(sessionId: string, row: unknown): ApprovalPromptEvent | undefined {
	if (!row || typeof row !== "object") return undefined;
	const record = row as Record<string, unknown>;
	const text = (value: unknown): string => (typeof value === "string" ? value : "");
	const list = (value: unknown): string[] => (
		Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []
	);
	const requestId = text(record.request_id);
	if (!requestId) return undefined;
	const proposal = record.proposal && typeof record.proposal === "object" && !Array.isArray(record.proposal)
		? record.proposal as Record<string, unknown>
		: {};
	const turnId = text(record.turn_id);
	return {
		protocolVersion: 2,
		type: "needs_approval",
		sessionId,
		turnId,
		requestId,
		operation: text(record.operation) || text(proposal.operation),
		tool: text(proposal.tool),
		target: text(record.target) || list(proposal.targets).join("|"),
		argv: list(proposal.argv),
		shell: text(proposal.shell),
		cwd: text(proposal.cwd),
		backend: text(proposal.backend),
		timeout: typeof proposal.timeout === "number" ? proposal.timeout : 60,
		network: list(proposal.network),
		capabilities: list(proposal.capabilities),
		contentDigest: text(proposal.content_digest),
		contentPreview: text(record.content_preview) || text(proposal.content_preview),
		contentLength: typeof proposal.content_length === "number" ? proposal.content_length : 0,
		fileDiff: record.file_diff && typeof record.file_diff === "object"
			? record.file_diff as Record<string, unknown>
			: {},
		durable: typeof record.durable === "boolean" || record.durable === null ? record.durable : undefined,
		status: text(record.status) || "pending",
		originTurnId: turnId,
	};
}
