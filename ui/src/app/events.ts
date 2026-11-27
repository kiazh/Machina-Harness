// Gateway event router: the single path from a gateway frame to turn,
// transcript, activity and approval state.
//
// Interface invariants:
// - `route` is synchronous and never throws on a malformed frame. A frame
//   without a usable session id is logged and dropped before feature
//   subscribers or turn bookkeeping see it — except a login completion
//   frame, which has no session by design and settles the command flow.
// - Feature subscribers observe every valid frame first, before any turn
//   bookkeeping; they own their own filtering and never mutate turn state.
// - Turn frames are bound through `TurnSessionStore`. Stale and retired turns
//   are inert, and a session without a local in-flight turn only feeds its
//   hidden-session tail. Approval frames pass that gate because a paused
//   delegated child has no local prompt turn.
// - Hidden sessions buffer a bounded live tail; they never render, open
//   dialogs or disturb the visible session.
// - Workspace notifications carry no session/turn identity: they go to the
//   workspace sink and never enter turn bookkeeping (plan §19).
// - `onChildEvent` stays on App; the router forwards `agent.child` frames
//   through the host so all observed child progress is owned in one place.
import type { TUI } from "@earendil-works/pi-tui";
import type { ActivityStore, ShellJob, SteeringItem } from "../activity.ts";
import type { Footer } from "../components/Footer.ts";
import type { OutputStore } from "../components/OutputViewer.ts";
import type { GatewayEvent, SessionInfo } from "../protocol.ts";
import type { Transcript } from "../transcript.ts";
import type { ApprovalPromptEvent, TurnSessionStore } from "../turnSessionStore.ts";
import { isWorkspaceEvent, type WorkspaceEvent } from "../workspace/protocol.ts";
import type { HarnessJobView } from "./status.ts";
import type { LogSink } from "./contracts.ts";

/** A `question.asked` frame; the picker itself stays in App. */
export type QuestionPromptEvent = Extract<GatewayEvent, { type: "question.asked" }>;

/** An `agent.child` frame; observed child progress is owned by App. */
export type AgentChildEvent = Extract<GatewayEvent, { type: "agent.child" }>;

/**
 * Login frames are emitted outside any chat turn, so they carry empty
 * session/turn ids. They settle the command flow that started the browser or
 * device authorization and must bypass the session gate.
 */
const SESSIONLESS_FEATURE_FRAMES = new Set<string>([
	"auth.chatgpt.completed",
	"auth.chatgpt.failed",
	"auth.grok.completed",
	"auth.grok.failed",
	"auth.copilot.completed",
	"auth.copilot.failed",
	"auth.opencode.completed",
	"auth.opencode.failed",
]);

/**
 * The composition-root calls one event router makes.
 *
 * Every member has a caller in this module. The host is deliberately narrower
 * than App: the router can reach the transcript sink, the turn store, the
 * activity/output stores and a handful of facade methods, but nothing else.
 */
export interface EventHost {
	readonly tui: Pick<TUI, "requestRender">;
	readonly log: LogSink;
	readonly activity: Pick<ActivityStore, "setJob" | "setQueue" | "noteToolStart" | "noteToolComplete">;
	readonly turnSession: TurnSessionStore;
	readonly footer: Pick<Footer, "status" | "tokens" | "contextTokens" | "contextPercent" | "pulseGrowth">;
	/** Chrome invalidation after footer/status changes (the AppShell). */
	readonly shell: { invalidateChrome(): void };
	readonly transcript: Pick<Transcript, "apply" | "addNotice" | "addUser" | "suspend">;
	readonly outputStore: Pick<OutputStore, "put">;

	/** True once exit/gateway loss has begun; no frame may mutate state. */
	isExiting(): boolean;
	/** The visible chat, or undefined before the first session exists. */
	current(): SessionInfo | undefined;
	/** Snapshot of feature-level subscribers (they run before bookkeeping). */
	featureEventHandlers(): Iterable<(event: GatewayEvent) => void>;

	/** Forward a workspace notification to its own sink (never turn state). */
	workspaceEvent(event: WorkspaceEvent): void;
	/** Retain a `harness.job` row for the Activity page. */
	setHarnessJob(job: HarnessJobView): void;

	refreshFamily(): void;
	isFamilyMember(sessionId: string): boolean;
	noteUnknownSession(sessionId: string): void;

	/** Queue a question for a hidden session; App owns the pending map. */
	queueQuestion(event: QuestionPromptEvent): void;
	promptQuestion(event: QuestionPromptEvent): void;
	promptApproval(event: ApprovalPromptEvent): void;
	clearApprovals(sessionId: string): void;

	switchKiroFallback(sessionId: string): void;
	invalidateContextUsage(): void;
	refreshContextUsage(sessionId: string): void;

	setRunning(running: boolean): void;
	flushSteering(sessionId: string): void;
	refreshChangesBadge(sessionId: string): void;

	/** App-owned child progress reducer (deliberately not duplicated here). */
	onChildEvent(event: AgentChildEvent): void;
}

/**
 * Routes gateway frames by session/turn and keeps the visible and hidden
 * session state consistent. See the module docstring for the invariants.
 */
export class EventRouter {
	private readonly host: EventHost;

	constructor(host: EventHost) {
		this.host = host;
	}

	/**
	 * Route one inbound notification. Workspace frames take the documented
	 * separate sink; every other frame runs the turn-event pipeline below.
	 */
	route(event: GatewayEvent | WorkspaceEvent): void {
		if (isWorkspaceEvent(event)) {
			this.host.workspaceEvent(event);
			return;
		}
		const host = this.host;
		if (host.isExiting()) return;
		// Login completions are feature-level frames with no chat identity:
		// deliver them before the session gate so the waiting command settles
		// instead of timing out. They never enter turn bookkeeping.
		if (SESSIONLESS_FEATURE_FRAMES.has(event.type)) {
			for (const handler of [...host.featureEventHandlers()]) handler(event);
			return;
		}
		// Every other frame is routed by session; a frame without a usable
		// session id cannot be bound to a chat and must not reach turn or
		// approval state.
		if (typeof event.sessionId !== "string" || !event.sessionId) {
			if (host.log.enabled) host.log.warn("event.invalid_session", { type: event.type });
			return;
		}
		if (event.type !== "message.delta" && host.log.enabled) {
			host.log.trace("event", { type: event.type, session: event.sessionId, turn: event.turnId });
		}
		for (const handler of [...host.featureEventHandlers()]) handler(event);
		if (event.type === "shell.job") {
			host.activity.setJob(event.sessionId, event.job as unknown as ShellJob);
			host.tui.requestRender();
			return;
		}
		if (event.type === "harness.job") {
			const job = event.job as unknown as HarnessJobView;
			host.setHarnessJob(job);
			// The child chat exists as soon as the job starts; refresh the
			// family so Activity/agent tabs can open it.
			if (job.session_id) host.refreshFamily();
			if (
				job.status !== "running"
				&& (job.owner_session_id === host.current()?.id || job.session_id === host.current()?.id)
			) {
				const selfChat = job.owner_session_id === job.session_id && job.session_id === host.current()?.id;
				const detail = (job.stdout || job.stderr || job.error || "").trim().split(/\r?\n/)[0] ?? "";
				if (job.needs_input) {
					host.transcript.addNotice(
						`Harness ${job.platform} is asking${detail ? `: ${detail.slice(0, 160)}` : " for direction"} — reply here or open it from Activity.`,
						"warning",
					);
				} else if (!selfChat) {
					host.transcript.addNotice(
						`Harness ${job.platform} ${job.status}${detail ? `: ${detail.slice(0, 120)}` : ""}`,
						job.status === "complete" ? "success" : job.status === "stopped" ? "warning" : "error",
					);
				}
			}
			host.tui.requestRender();
			return;
		}
		if (event.type === "agent.child") {
			const binding = host.turnSession.turnBinding(event.sessionId);
			const deleted = event.phase === "settled" && event.value === "deleted";
			if (!deleted && binding && (binding.retired.includes(event.turnId) || binding.turnId !== undefined && binding.turnId !== event.turnId)) return;
			host.onChildEvent(event);
			return;
		}
		// Feature-level frames carry no session/turn: they settle command
		// flows above and must not enter turn bookkeeping or hidden tails.
		if (event.type === "growth.status") {
			// Background training lands after the turn retires (and the
			// in-flight entry clears), so it bypasses turn tracking. Shown
			// only for the current session; never steals focus.
			if (event.sessionId !== host.current()?.id) return;
			if (host.footer.pulseGrowth()) host.tui.requestRender();
			return;
		}
		if (event.type === "context.compacted") {
			host.refreshContextUsage(event.sessionId);
			return;
		}
		const hidden = event.sessionId !== host.current()?.id;
		// Family bookkeeping runs before the turn-state gate. Observed child
		// events are handled above; delegate completion also refreshes the
		// family for transports that do not stream child progress.
		// A turn-start frame from an unfamiliar session is the only other
		// trigger worth a scan: later frames never rescan a foreign id
		// (negative cache in family.ts).
		if (event.type === "tool.complete" && event.name === "delegate") {
			host.refreshFamily();
		} else if (hidden && !host.isFamilyMember(event.sessionId) && isTurnStart(event)) {
			host.noteUnknownSession(event.sessionId);
		}
		if (event.type === "question.asked") {
			if (event.sessionId === host.current()?.id) host.promptQuestion(event);
			else host.queueQuestion(event);
			host.tui.requestRender();
			return;
		}
		const approvalFrame = event.type === "needs_approval"
			|| event.type === "permission.required"
			|| event.type === "approval.resolved"
			|| event.type === "permission.resolved"
			|| event.type === "approval.resumed";
		if (!host.turnSession.isInFlight(event.sessionId) && !approvalFrame) {
			// A session without a local turn binding (e.g. an in-flight turn
			// from the server side): preview it in the hidden session's tail;
			// terminal dedupe lives in the tail itself. Approval frames pass:
			// a delegated child has no local prompt turn, and its pause or
			// resolution must still reach the owning card.
			if (hidden) host.turnSession.bufferHiddenEvent(event);
			return;
		}
		// A turn's events may arrive after the user switched sessions, so match
		// against in-flight turns, not just the current session. Non-visible
		// completion still retires and accounts; it never renders, opens
		// dialogs, or disturbs the visible session.
		const record = event.type === "message.start"
			? host.turnSession.bindStart(event.sessionId, event.turnId)
			: host.turnSession.bindEvent(event.sessionId, event);
		if (!record && !approvalFrame) return;
		// Activity updates obey the same turn binding as transcript frames.
		if (event.type === "steering.update") {
			host.activity.setQueue(event.sessionId, event.items as unknown as SteeringItem[]);
			if (host.current()?.id === event.sessionId && event.delivered.length) {
				for (const item of event.delivered as unknown as SteeringItem[]) host.transcript.addUser(item.text);
				host.transcript.apply({ ...event, type: "message.start" });
			}
			host.tui.requestRender();
			return;
		}
		if (event.type === "tool.start") {
			host.turnSession.noteToolStart(event.sessionId, event.id, event.name);
			host.activity.noteToolStart(event.sessionId, event.id, event.name, event.args);
		} else if (event.type === "tool.complete") {
			host.turnSession.noteToolComplete(event.sessionId, event.id);
			const status = event.isError || event.outcome === "denied"
				? "error"
				: event.outcome === "paused" || event.outcome === "awaiting_approval"
					? "paused"
					: event.outcome === "unknown" || event.outcome === "outcome_unknown"
						? "unknown"
						: "ok";
			host.activity.noteToolComplete(event.sessionId, event.id, status, event.result, event.outcome);
		}
		else if (event.type === "message.complete") host.turnSession.clearActiveTools(event.sessionId);
		// A hidden session keeps a live tail so switching back shows the work
		// done while it was invisible (switchTo merges it
		// with the fetched history — buffered tail wins, no duplicates).
		if (hidden) host.turnSession.bufferHiddenEvent(event);
		// Retain reported output for local inspection (visible or not, but
		// only for the bound turn). Never refetched, never re-executed.
		if (event.type === "tool.complete") {
			if (typeof event.id === "string" && event.id) {
				host.outputStore.put(
					event.sessionId,
					event.id,
					typeof event.name === "string" && event.name ? event.name : "unknown tool",
					typeof event.result === "string" ? event.result : "",
					event.truncated ?? false,
				);
			}
		}
		if ((event.type === "needs_approval" || event.type === "permission.required")) {
			// A malformed frame has no resolvable identity; dropping it is safer
			// than opening a card whose request id cannot be a string.
			if (typeof event.requestId !== "string" || !event.requestId
				|| typeof event.sessionId !== "string" || !event.sessionId) {
				if (host.log.enabled) host.log.warn("approval.invalid", { type: event.type });
				return;
			}
			// R4-065: a pause frame can carry a terminal status — the broker's
			// deadline (or another transport) already resolved the request.
			// That is a resolution, not a card: retire any open card and
			// retained evidence without issuing a decision the record refuses.
			if (event.status !== undefined && event.status !== "pending") {
				host.turnSession.deletePendingApproval(event.requestId);
				const stale = host.turnSession.approvalDialog(event.requestId);
				stale?.card.setRecorded(`approval ${event.status}`);
				stale?.close();
				if (event.sessionId === host.current()?.id) {
					host.transcript.addNotice(
						`Approval ${event.requestId.slice(0, 8)} ${event.status}.`,
						event.status === "approved" ? "success" : event.status === "denied" ? "error" : "warning",
					);
				}
				host.tui.requestRender();
				return;
			}
			// Retain the review evidence even while its session is hidden
			// (R15); the card itself opens only in the bound visible session
			// and never carries a dialog into another session (T7 owns the
			// review card/queue). A later return rebuilds it via
			// restoreApprovals.
			host.turnSession.rememberPendingApproval(event);
			if (event.sessionId !== host.current()?.id) return;
			// Approval card bound to this exact session/turn — switching
			// sessions never approves the wrong session (Phase 5.6).
			host.promptApproval(event);
			host.tui.requestRender();
			return;
		}
		if ((event.type === "approval.resolved" || event.type === "permission.resolved")) {
			if (typeof event.requestId !== "string" || !event.requestId) {
				if (host.log.enabled) host.log.warn("approval.resolved.invalid", { type: event.type });
				return;
			}
			// The decision is durable regardless of which session is visible.
			host.turnSession.deletePendingApproval(event.requestId);
			if (event.sessionId !== host.current()?.id) return;
			const pending = host.turnSession.approvalDialog(event.requestId);
			pending?.card.setRecorded(`approval ${event.status}`);
			pending?.close();
			host.transcript.addNotice(
				`Approval ${event.requestId.slice(0, 8)} ${event.status}.`,
				event.status === "approved" ? "success" : event.status === "denied" ? "error" : "warning",
			);
			host.tui.requestRender();
			return;
		}
		if (event.type === "approval.resumed") {
			// The review is no longer pending; the approved action continues
			// under a fresh bound turn.
			host.turnSession.deletePendingApproval(event.requestId);
			if (host.turnSession.applyApprovalResume(event.sessionId, event.originTurnId, event.turnId)
				&& event.sessionId === host.current()?.id) {
				host.transcript.apply({ ...event, type: "message.start" });
			}
			if (event.sessionId !== host.current()?.id) return;
			host.transcript.addNotice(`Approval ${event.requestId.slice(0, 8)} resumed.`);
			host.tui.requestRender();
			return;
		}
		if (event.type === "turn.started") {
			host.tui.requestRender();
			return;
		}
		if (event.type === "turn.cleanup_pending" || event.type === "turn.ownership_lost") {
			if (event.sessionId !== host.current()?.id) return;
			host.transcript.suspend("cleanup_pending");
			host.footer.status = "cleanup_pending";
			host.shell.invalidateChrome();
			host.transcript.addNotice(event.type === "turn.cleanup_pending"
				? "Stopping: waiting for the running action to finish cleanup."
				: "Turn ownership was lost; stopping the running action.", "warning");
			host.tui.requestRender();
			return;
		}
		if (event.type === "usage") {
			host.turnSession.setUsageLatest(event.sessionId, event.tokens);
			if (event.sessionId === host.current()?.id) {
				host.footer.tokens = host.turnSession.sessionTokens + event.tokens;
				if (event.contextTokens !== undefined) {
					host.invalidateContextUsage();
					host.footer.contextTokens = event.contextTokens;
					host.footer.contextPercent = event.contextPercent ?? null;
				}
			}
			host.tui.requestRender();
			return;
		}
		if (event.sessionId !== host.current()?.id) {
			if (event.type === "message.complete") {
				host.turnSession.accountCompletion(event.sessionId, event.tokens);
				host.turnSession.retireTurn(event.sessionId, event.turnId);
				host.clearApprovals(event.sessionId);
			}
			// AH-032: never clear in-flight on "error" — the terminal
			// message.complete still follows with final accounting and
			// tool-card cleanup. Clearing early drops that completion.
			host.setRunning(host.turnSession.visibleInFlight());
			host.tui.requestRender();
			return;
		}
		if (event.type === "message.stopping") {
			host.footer.status = "stopping";
			host.shell.invalidateChrome();
		}
		if (event.type === "message.complete") host.clearApprovals(event.sessionId);
		const summary = host.transcript.apply(event);
		if (event.type === "error") {
			// Keep turn state until terminal completion (AH-032). The error
			// notice is already rendered by transcript.apply(); final
			// accounting arrives with message.complete.
			host.log.warn("gateway.error", { session: event.sessionId, message: event.message });
			host.setRunning(true);
			if (host.current()?.id === event.sessionId
				&& typeof event.message === "string"
				&& isKiroQuotaError(event.message)) {
				host.switchKiroFallback(event.sessionId);
			}
		} else if (summary) {
			if (event.type === "message.complete" && event.cancelled) host.turnSession.setInterrupted(event.sessionId, true);
			if (event.type === "message.complete") {
				host.log.info("turn.complete", {
					session: event.sessionId,
					turnId: event.turnId,
					cancelled: event.cancelled,
					tokens: summary.tokens,
				});
			}
			host.turnSession.accountCompletion(event.sessionId, summary.tokens);
			host.turnSession.retireTurn(event.sessionId, event.turnId);
			host.footer.tokens = host.turnSession.sessionTokens;
			host.setRunning(host.turnSession.isInFlight(event.sessionId));
			if (event.type === "message.complete" && !event.cancelled) {
				setTimeout(() => { host.flushSteering(event.sessionId); }, 0);
				host.refreshChangesBadge(event.sessionId);
			}
		}
		host.tui.requestRender();
	}
}

/** Turn-start frames: the first event of a turn in a session's stream. */
function isTurnStart(event: GatewayEvent): boolean {
	return event.type === "message.start" || event.type === "turn.started";
}

/**
 * Kiro quota exhaustion detection, identical to `App.isKiroQuotaError`.
 *
 * The router must make the same decision without importing `app.ts` (App will
 * import this module during integration). Exported so the facade can re-export
 * one implementation instead of keeping two copies.
 */
export function isKiroQuotaError(value: string): boolean {
	const text = value.toLowerCase();
	const kiro = text.includes("kiro");
	const quota =
		text.includes("usage limit") ||
		text.includes("monthly request limit") ||
		text.includes("monthly quota") ||
		text.includes("quota exhausted") ||
		text.includes("credits are exhausted") ||
		text.includes("http 402") ||
		(text.includes("http 429") && kiro);
	return (
		quota &&
		(kiro || text.includes("monthly request limit") || text.includes("monthly quota"))
	);
}
