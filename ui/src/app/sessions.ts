// Session lifecycle, agent family and steering controller: the visible chat,
// draft/lazy creation, switching and replay, LRU pruning, family rows/cycling,
// and the local steering queue that delivers after a turn boundary.
//
// Interface invariants:
// - App keeps the single visible-session field; the controller reads it via
//   `current()` and writes it only through `setCurrent()`. It also owns the
//   draft title and the shared `sessionCreation` promise. A blank chat stays
//   client-side until its first message; two quick submits can never create
//   two sessions.
// - Switching consumes the hidden-session tail (tail wins over fetched
//   history), re-seeds/refreshes the family, restores tokens, and hands the
//   new session's approvals and questions back to App before any render.
// - The visible session is fenced on every response: a late creation/mode
//   read for a session the user already left publishes nothing.
// - Steering keeps its queue bounds and delivery rules from TurnSessionStore
//   and ActivityStore: local prompts drain one at a time, remote items are
//   acknowledged by id, and a failed read retains the local queue.
// - Family rows are main-first; cycling wraps and drives the same
//   one-resume-at-a-time path as the family picker.
import { agentSessionItems } from "../features/agents.ts";
import type { FamilySession } from "../features/index.ts";
import { FamilyStore, listFamilySessions } from "../family.ts";
import type { SteeringItem } from "../activity.ts";
import type { HistoryEntry, SessionInfo, SessionResult } from "../protocol.ts";
import { tailEntries, trimSupersededHistory, type TailBlock } from "../tail.ts";
import type { SessionHost } from "./contracts.ts";

export class SessionController {
	private readonly host: SessionHost;
	/** Title chosen by /new while the chat is still a client-side draft. */
	private draftSessionTitle: string | undefined;
	/** Shared first-message creation so two quick submits cannot make two chats. */
	private sessionCreation: Promise<void> | undefined;
	private agentMenuOpening = false;
	/** Family (agent family) state and actions; App owns rendering and keys. */
	private readonly family: FamilyStore;

	constructor(host: SessionHost) {
		this.host = host;
		this.family = new FamilyStore({
			current: () => this.host.current(),
			isRunning: (sessionId) => host.turnSession.isRunning(sessionId),
			request: (method, params) => host.request(method, params ?? {}),
			switchTo: (session, history) => this.switchTo(session, history),
			changed: () => {
				host.turnSession.pruneSessionTails();
				this.syncAgentCount();
				host.tui.requestRender();
			},
			pruneSessionState: (sessionId) => this.pruneSessionState(sessionId),
			report: (error) => {
				host.transcript.addNotice(`Could not switch session: ${message(error)}`, "error");
				host.tui.requestRender();
			},
		});
	}

	/**
	 * Render a blank chat without writing a session. The session row is
	 * created by the first submitted message; /new and /clear use this so an
	 * untouched draft never appears in history.
	 */
	showNewChat(title?: string): void {
		this.host.log.debug("chat.new", { hasTitle: Boolean(title?.trim()) });
		const current = this.host.current();
		if (current) this.host.turnSession.saveSessionTokens(current.id);
		this.host.setCurrent(undefined);
		this.host.resetChanges();
		this.draftSessionTitle = title?.trim() || undefined;
		this.host.turnSession.resetSessionTokens();
		this.host.clearImages();
		this.host.composer.setText("");
		this.host.transcript.clear();
		this.host.transcript.addNotice("Describe a task. Machina can read, edit, and run commands in this workspace.", "plain");
		this.host.footer.dispose();
		this.host.footer.tokens = 0;
		this.host.footer.contextTokens = 0;
		this.host.footer.contextPercent = 0;
		this.host.invalidateContextUsage();
		this.host.setRunning(false);
		this.syncAgentCount();
		this.host.tui.requestRender();
	}

	/** Host entry point for /new and post-delete navigation. */
	newSession(title?: string): void {
		this.showNewChat(title);
		this.refreshSessionMode();
	}

	/** Create the durable session for a draft chat at its first message. */
	ensureSession(text: string): Promise<void> {
		if (this.host.current()) return Promise.resolve();
		if (!this.sessionCreation) {
			const agent = this.host.sessionAgent();
			const title = this.draftSessionTitle || text.slice(0, 80);
			this.sessionCreation = this.host
				.clientRequest<SessionResult>("session.create", {
					title,
					...(agent ? { agent } : {}),
				})
				.then(({ session }) => {
					this.host.log.info("session.create", { id: session.id, titleLength: (session.title ?? "").length });
					this.draftSessionTitle = undefined;
					this.host.setCurrent(session);
					this.host.resetChanges();
					this.host.flushPendingQuestions(session.id);
					this.rememberSession(session.id);
					this.host.turnSession.resetSessionTokens();
					this.host.transcript.clear();
					this.host.footer.tokens = 0;
					this.host.footer.contextTokens = 0;
					this.host.footer.contextPercent = 0;
					this.family.reseed();
					this.syncAgentCount();
					this.refreshSessionMode(session.id);
					this.host.tui.requestRender();
				})
				.finally(() => {
					this.sessionCreation = undefined;
				});
		}
		return this.sessionCreation;
	}

	switchTo(session: SessionInfo, history: HistoryEntry[]): void {
		this.host.log.info("session.switch", { id: session.id, history: history.length });
		if (this.host.current()?.id === session.id) {
			// Same-id re-resume of the visible session (`/resume <current>`,
			// picking the current row in a session list): the fetched history
			// cannot contain the in-flight turn, so clearing and replaying here
			// would erase live output and strand running-tool completions
			// (review round 4, R4-094). Refresh the metadata only.
			this.updateSession(session);
			this.host.tui.requestRender();
			return;
		}
		const previous = this.host.current();
		if (previous) this.host.turnSession.saveSessionTokens(previous.id);
		// Consume this session's hidden-session tail (buffered events that
		// arrived while it was not visible). Merge rule (PINNED, tail.ts):
		// the tail is the conversation suffix and WINS over the fetched
		// history; matching trailing history entries are trimmed first so a
		// completed turn or settled tool renders exactly once. Consuming the
		// tail here is the documented "clear when history is fetched"
		// boundary: the buffer only ever holds work from after the last
		// switch, so it cannot duplicate earlier history.
		const tail = this.host.turnSession.takeSessionTail(session.id)?.blocks ?? [];
		this.updateSession(session);
		this.rememberSession(session.id);
		this.host.transcript.clear();
		this.host.transcript.replay(trimSupersededHistory(history, tail));
		this.replayTail(session, tail);
		this.host.turnSession.restoreSessionTokens(session.id);
		// Preserve the active turn's latest cumulative usage across switches
		// without mixing turns: completed total plus this session's turn total.
		this.host.footer.tokens = this.host.turnSession.displayTokens(session.id);
		this.host.footer.contextTokens = 0;
		this.host.footer.contextPercent = 0;
		this.host.refreshContextUsage(session.id);
		// The backend stays paused for this session's approvals; rebuild the
		// review cards retained while it was hidden (R15). Close every other
		// session's card first so a stale child request cannot own the dock.
		this.host.invalidateApprovals(session.id);
		this.host.restoreApprovals(session.id);
		// A question delivered while this session was hidden should reappear
		// when its owning session becomes visible.
		this.host.flushPendingQuestions(session.id);
		// F-30: approvals the hidden session never saw are parked only in the
		// permission store; re-read the target session's pending rows so the
		// turn cannot sit stranded behind a card that was never re-opened.
		void this.host.rehydrateApprovals(session.id);
		this.host.setRunning(this.host.turnSession.isRunning(session.id));
		// Family = current.parentSessionId ?? current.id plus that root's
		// children; refresh after the local re-seed keeps the subagent count
		// honest.
		this.family.reseed();
		this.syncAgentCount();
		this.refreshFamily();
		// Finding 8: the footer must render the newly visible session's
		// authority, never the previous session's (or the global default).
		this.refreshSessionMode(session.id);
		this.host.tui.requestRender();
	}

	/**
	 * Re-query the gateway for a session's effective mode and mirror it
	 * into the footer (via request, which syncs footer.mode on mode.get).
	 * Best-effort: a failed refresh keeps the previous indicator rather
	 * than blanking it, and never rejects into the switch path.
	 */
	private refreshSessionMode(sessionId?: string): void {
		void this.host.request<{ mode: string }>("mode.get", sessionId ? { sessionId } : {}).catch(() => {});
	}

	updateSession(session: SessionInfo): void {
		const switched = session.id !== this.host.current()?.id;
		if (switched) {
			this.host.clearImages();
			this.host.composer.setText("");
			this.host.footer.dispose();
			// A pending request may reopen only when the user returns to its
			// owning session; never carry a selection into the new session.
			this.host.invalidateApprovals(session.id);
		}
		this.host.setCurrent(session);
		if (switched) this.host.resetChanges();
		this.syncAgentCount();
		this.host.tui.requestRender();
	}

	/** Adopt the gateway-pinned route on the visible session; true when it matched. */
	adoptRoute(sessionId: string, provider: string, model: string): boolean {
		const current = this.host.current();
		if (current?.id !== sessionId) return false;
		current.provider = provider;
		current.model = model;
		return true;
	}

	/** Mark a session visited; the store enforces its LRU bound and guards. */
	rememberSession(sessionId: string): void {
		this.host.turnSession.rememberSession(sessionId);
	}

	/** Drop one dormant session from every per-session store. */
	private pruneSessionState(sessionId: string): void {
		if (!this.host.turnSession.pruneSessionIfDormant(sessionId)) return;
		this.pruneSideSessionState(sessionId);
	}

	/** Non-turn session state owned by App (activity/output/family). */
	pruneSideSessionState(sessionId: string): void {
		this.host.activity.pruneSession(sessionId);
		this.host.outputStore.pruneSession(sessionId);
		this.family.pruneSession(sessionId);
	}

	// ─── agent family (root + delegated subagents) ──────────────────────────

	/** Family rows, main first, with run/current state for menus. */
	familyRows(): FamilySession[] {
		return this.family.rows();
	}

	/** FeatureHost: the current family (main first) for /agents and menus. */
	familySessions(): FamilySession[] {
		return this.familyRows();
	}

	/**
	 * Footer segment: the number of subagents = family rows minus the
	 * main session (the root is not a subagent). Zero hides the segment.
	 */
	private syncAgentCount(): void {
		this.host.footer.subagents = Math.max(0, this.familyRows().length - 1);
	}

	/**
	 * Down on an empty composer: the same family picker as /agents sessions.
	 * Selecting switches through FamilyStore.open (the one-resume-at-a-time
	 * path shared with Alt+J/K); choosing the visible session is a
	 * no-op. Listing failures are reported quietly.
	 */
	async openSubagentMenu(): Promise<void> {
		if (this.agentMenuOpening || this.host.hasDialogs()) return;
		const current = this.host.current();
		if (!current) return;
		this.agentMenuOpening = true;
		try {
			const running = new Map(this.familyRows().map((row) => [row.session.id, row.running]));
			let family: SessionInfo[];
			try {
				family = await listFamilySessions(
					{ request: <T>(method: string, params?: Record<string, unknown>) => this.host.request<T>(method, params) },
					current,
				);
			} catch (error) {
				this.host.transcript.addNotice(`Could not list agent sessions: ${message(error)}`, "error");
				this.host.tui.requestRender();
				return;
			}
			// The visible session may have changed while the list was fetched,
			// and a dialog (e.g. help) may have opened: in either case the
			// menu is no longer appropriate.
			if (this.host.isExiting() || this.host.current()?.id !== current.id || this.host.hasDialogs()) return;
			if (family.length <= 1) return;
			const choice = await this.host.pick("Switch to an agent session", agentSessionItems(family, current, running));
			if (!choice || choice.value === current.id) return;
			await this.family.open(choice.value);
		} finally {
			this.agentMenuOpening = false;
		}
	}

	/** Bounded, best-effort family refresh (see family.ts). */
	refreshFamily(): void {
		void this.family.refresh();
	}

	/** Refresh kick for turn-start frames from ids not yet known family members. */
	noteUnknownSession(sessionId: string): void {
		this.family.noteUnknown(sessionId);
	}

	isFamilyMember(sessionId: string): boolean {
		return this.family.isMember(sessionId);
	}

	/** Cycle the family; wraps around. */
	cycleFamily(direction: 1 | -1): Promise<void> {
		return this.family.cycle(direction);
	}

	/** Open a family member through the one-resume-at-a-time path. */
	openFamily(sessionId: string): Promise<void> {
		return this.family.open(sessionId);
	}

	/** Hide the selected idle child after returning to its main session. */
	closeCurrentFamily(): Promise<boolean> {
		return this.family.closeCurrent();
	}

	/** Retire a deleted child from the family strip. */
	dismissFamily(sessionId: string): void {
		this.family.dismiss(sessionId);
	}

	async queueSteering(text: string, fromDraft = true): Promise<boolean> {
		const session = this.host.current();
		if (!session || this.host.turnSession.isSteeringBusy(session.id)) return false;
		this.host.turnSession.setSteeringBusy(session.id, true);
		const version = this.host.activity.queueVersion(session.id);
		try {
			const result = await this.host.request<{ queued: boolean; items?: SteeringItem[] }>("prompt.steer", { sessionId: session.id, text });
			if (result.queued) {
				if (this.host.activity.queueVersion(session.id) === version) this.host.activity.setQueue(session.id, result.items ?? []);
			}
			else this.host.turnSession.pushSteering(session.id, text);
			if (fromDraft && this.host.current()?.id === session.id && this.host.composer.getExpandedText().trim() === text.trim()) this.host.composer.setText("");
			if (!result.queued && !this.host.turnSession.isInFlight(session.id)) await this.flushSteering(session.id);
			return true;
		} catch (error) {
			this.host.print(`Could not queue steering: ${message(error)}.${fromDraft ? " Your draft is retained." : ""}`, "error");
			return false;
		} finally {
			this.host.turnSession.setSteeringBusy(session.id, false);
			this.host.tui.requestRender();
		}
	}

	async flushSteering(sessionId: string): Promise<void> {
		if (this.host.current()?.id !== sessionId || this.host.turnSession.isInFlight(sessionId) || this.host.isExiting()
			|| this.host.turnSession.isSteeringFlushing(sessionId) || this.host.turnSession.isInterrupted(sessionId)) return;
		if (!this.host.activity.queue(sessionId).length && this.host.turnSession.queuedSteering(sessionId).length === 0) return;
		this.host.turnSession.setSteeringFlushing(sessionId, true);
		let accepted = false;
		try {
			const version = this.host.activity.queueVersion(sessionId);
			const result = await this.host.request<{ items: SteeringItem[] }>("prompt.steering", { sessionId });
			if (this.host.activity.queueVersion(sessionId) === version) this.host.activity.setQueue(sessionId, result.items);
			if (this.host.current()?.id !== sessionId || this.host.turnSession.isInFlight(sessionId)) return;
			const local = this.host.turnSession.queuedSteering(sessionId)[0];
			const remote = result.items[0];
			if (local !== undefined) {
				await this.host.submitTurn(local);
				accepted = true;
				this.host.turnSession.shiftSteering(sessionId);
			} else if (remote) {
				await this.host.submitTurn(remote.text, [], remote.id);
				accepted = true;
				this.host.activity.setQueue(sessionId, this.host.activity.queue(sessionId).filter((item) => item.id !== remote.id));
			}
			while (this.host.turnSession.isInFlight(sessionId) && this.host.turnSession.queuedSteering(sessionId).length > 0) {
				const head = this.host.turnSession.queuedSteering(sessionId)[0]!;
				const queueVersion = this.host.activity.queueVersion(sessionId);
				const queued = await this.host.request<{ queued: boolean; items?: SteeringItem[] }>("prompt.steer", { sessionId, text: head });
				if (!queued.queued) break;
				this.host.turnSession.shiftSteering(sessionId);
				if (this.host.activity.queueVersion(sessionId) === queueVersion) this.host.activity.setQueue(sessionId, queued.items ?? []);
			}
		} catch (error) {
			this.host.print(`Steering is retained. Open /activity to retry: ${message(error)}`, "warning");
		} finally {
			this.host.turnSession.setSteeringFlushing(sessionId, false);
			if (accepted && this.host.turnSession.queuedSteering(sessionId).length > 0
				&& !this.host.turnSession.isInFlight(sessionId) && !this.host.turnSession.isInterrupted(sessionId) && !this.host.isExiting()) {
				setTimeout(() => { void this.flushSteering(sessionId); }, 25);
			}
		}
	}

	// ─── hidden-session live tails ──────────────────────────────────────────

	/**
	 * Replay a consumed hidden-session tail in rendered order. Historical
	 * blocks re-enter through the resume path; ACTIVE blocks (streaming text,
	 * a running tool) re-enter through the live event path carrying their
	 * turn/tool identity, so the completion events that follow reconcile them
	 * — instead of a historical copy duplicating the text and a "running…"
	 * notice stranding a tool whose completion can no longer find its card
	 * (review round 3, F12).
	 */
	private replayTail(session: SessionInfo, tail: TailBlock[]): void {
		for (const block of tail) {
			if (block.kind === "assistant" && !block.completed) {
				this.host.transcript.apply({
					protocolVersion: 2,
					sessionId: session.id,
					turnId: block.turnId,
					type: "message.delta",
					text: block.text,
				});
			} else if (block.kind === "tool" && !block.settled) {
				this.host.transcript.apply({
					protocolVersion: 2,
					sessionId: session.id,
					turnId: block.turnId,
					type: "tool.start",
					id: block.id,
					name: block.name,
					args: {},
				});
			} else {
				this.host.transcript.replay(tailEntries([block]));
			}
		}
	}
}

function message(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}
