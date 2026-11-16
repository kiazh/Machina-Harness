// Per-session turn/session state: staged turn bindings, in-flight guards,
// steering, delegated runs, streamed tool activity, token totals, retained
// approvals and hidden-session tails. App owns rendering and feature wiring;
// this store owns the maps and the bookkeeping that only touches them.

import type { ApprovalCard } from "./components/ApprovalCard.ts";
import type { GatewayEvent } from "./protocol.ts";
import { SessionTailStore, type SessionTail } from "./tail.ts";

/** Presentation record for one session's in-flight turn (section 4.2). */
export interface SessionTurn {
	/** Accepted turn ID; undefined until the first bound event or submit result. */
	turnId: string | undefined;
	/** True while the binding is provisional (submit unresolved or start-only). */
	provisional: boolean;
	/** A message.start has been applied for the bound turn. */
	started: boolean;
	/** Any event has bound to this record (dispatch evidence). */
	hadEvents: boolean;
	/** The local watchdog has warned once for this turn. */
	warnedSlow: boolean;
	/** Latest cumulative usage for the active turn. */
	usageLatest: number;
	/** Bounded retired turn IDs; late duplicates are inert. */
	retired: string[];
}

export interface ApprovalDialog {
	requestId: string;
	sessionId: string;
	originTurnId: string | undefined;
	transportTurnId: string;
	card: ApprovalCard;
	evidenceAdded: boolean;
	close: () => void;
	dialog: Promise<void | undefined>;
}

export type ApprovalPromptEvent = Extract<GatewayEvent, { type: "needs_approval" | "permission.required" }>;

/** What the store must know about App's visible session and family state. */
export interface TurnSessionStoreOptions {
	/** The session currently rendered, or undefined before the first switch. */
	visibleSessionId(): string | undefined;
	/** True for a live family member (parent root or delegated child). */
	isFamilyMember(sessionId: string): boolean;
	/** Diagnostic watchdog expiry for the visible session. */
	onSlowTurn?(sessionId: string): void;
	/** Remove non-turn side state (activity/output/family) after LRU eviction. */
	onEvicted?(sessionId: string): void;
	maxRetainedSessions?: number;
	inFlightTimeoutMs?: number;
}

/** Default turn-diagnostic deadline (server turn timeout). */
const DEFAULT_IN_FLIGHT_TIMEOUT_MS = 300_000;
/** Retained dormant sessions. */
const DEFAULT_MAX_RETAINED_SESSIONS = 50;
/** Bounded retired IDs per session. */
const MAX_RETIRED_TURNS = 20;
/** Bounded delegated streaming text per child. */
const MAX_DELEGATED_TEXT = 65_536;
/** Bounded locally queued steering prompts per session. */
const MAX_QUEUED_STEERING = 32;

/**
 * One cohesive owner for per-session turn bindings and the state that follows
 * a turn: in-flight timers, steering queues, delegated runs, active tools,
 * token totals, retained approvals and hidden-session tails. Methods here
 * mutate only store-owned state; rendering and RPC dispatch stay in App.
 */
export class TurnSessionStore {
	/** Per-session turn binding, provisional pre-RPC state and retired IDs. */
	readonly turns = new Map<string, SessionTurn>();
	/** Session ids of turns that are still in flight (prompt submitted, no message.complete yet). */
	readonly inFlight = new Set<string>();
	readonly delegatedRunning = new Set<string>();
	readonly activeTools = new Map<string, Map<string, string>>();
	readonly nextSteering = new Map<string, string[]>();
	readonly steeringBusy = new Set<string>();
	readonly steeringFlushing = new Set<string>();
	readonly interruptedSteering = new Set<string>();
	readonly kiroFallbackSessions = new Set<string>();
	readonly delegatedText = new Map<string, string>();
	/** Switch recency (oldest first) for bounded per-session state. */
	readonly sessionRecency: string[] = [];
	/** Timers for the local watchdog (diagnostic deadline, not ownership). */
	readonly inFlightTimers = new Map<string, NodeJS.Timeout>();
	/** Preserved token counts per session so switchTo does not lose them. */
	readonly sessionTokensById = new Map<string, number>();
	/** Hidden-session live tails; consumed by switchTo and bounded (tail.ts). */
	readonly sessionTails: SessionTailStore;
	/** Retained approval evidence keyed by request ID (survives navigation). */
	readonly pendingApprovals = new Map<string, ApprovalPromptEvent>();
	/** Open approval reviews keyed by request ID (binding captured at open). */
	readonly approvalDialogs = new Map<string, ApprovalDialog>();

	/** Token total for the visible session. */
	sessionTokens = 0;

	private readonly options: TurnSessionStoreOptions;
	private readonly maxRetainedSessions: number;
	private readonly inFlightTimeoutMs: number;

	constructor(options: TurnSessionStoreOptions) {
		this.options = options;
		this.maxRetainedSessions = options.maxRetainedSessions ?? DEFAULT_MAX_RETAINED_SESSIONS;
		this.inFlightTimeoutMs = options.inFlightTimeoutMs ?? DEFAULT_IN_FLIGHT_TIMEOUT_MS;
		this.sessionTails = new SessionTailStore({
			visible: () => this.options.visibleSessionId(),
			running: (sessionId) => this.isRunning(sessionId),
			retained: (sessionId) =>
				sessionId === this.options.visibleSessionId() || this.inFlight.has(sessionId) || this.options.isFamilyMember(sessionId),
		});
	}

	// ─── in-flight guards ───────────────────────────────────────────────────

	isInFlight(sessionId: string): boolean {
		return this.inFlight.has(sessionId);
	}

	isRunning(sessionId: string): boolean {
		return this.inFlight.has(sessionId) || this.delegatedRunning.has(sessionId);
	}

	visibleInFlight(): boolean {
		const visible = this.options.visibleSessionId();
		return visible !== undefined && this.isInFlight(visible);
	}

	inFlightSessions(): string[] {
		return [...this.inFlight];
	}

	/** Register the submitting record before dispatch and start its watchdog. */
	beginTurn(sessionId: string): SessionTurn {
		this.inFlight.add(sessionId);
		const record = this.turnRecord(sessionId);
		record.turnId = undefined;
		record.provisional = true;
		record.started = false;
		record.hadEvents = false;
		record.warnedSlow = false;
		record.usageLatest = 0;
		this.startInFlightTimer(sessionId);
		return record;
	}

	/** Read the current binding without creating one. */
	turnBinding(sessionId: string): SessionTurn | undefined {
		return this.turns.get(sessionId);
	}

	turnRecord(sessionId: string): SessionTurn {
		let record = this.turns.get(sessionId);
		if (!record) {
			record = {
				turnId: undefined,
				provisional: true,
				started: false,
				hadEvents: false,
				warnedSlow: false,
				usageLatest: 0,
				retired: [],
			};
			this.turns.set(sessionId, record);
		}
		return record;
	}

	turnId(sessionId: string): string | undefined {
		return this.turns.get(sessionId)?.turnId;
	}

	hadEvents(sessionId: string): boolean {
		return this.turns.get(sessionId)?.hadEvents ?? false;
	}

	usageLatest(sessionId: string): number {
		return this.turns.get(sessionId)?.usageLatest ?? 0;
	}

	setUsageLatest(sessionId: string, tokens: number): void {
		const record = this.turns.get(sessionId);
		if (record) record.usageLatest = tokens;
	}

	isRetired(sessionId: string, turnId: string): boolean {
		return this.turns.get(sessionId)?.retired.includes(turnId) ?? false;
	}

	/**
	 * Confirm a provisional binding against the submit result.
	 * Returns "missing" without a record, "bound" when the event-bound ID is
	 * kept, "confirmed" when the result agreed, and "mismatch" otherwise.
	 */
	confirmTurn(sessionId: string, turnId: string | undefined): "missing" | "bound" | "confirmed" | "mismatch" {
		const record = this.turns.get(sessionId);
		if (!record) return "missing";
		if (turnId === undefined) return "bound"; // keep the event-bound provisional ID
		if (record.turnId === undefined) {
			record.turnId = turnId;
			record.provisional = false;
			return "confirmed";
		}
		if (record.turnId !== turnId) return "mismatch";
		record.provisional = false;
		return "confirmed";
	}

	/** Apply a message.start; undefined means the frame is inert. */
	bindStart(sessionId: string, turnId: string): SessionTurn | undefined {
		const record = this.turns.get(sessionId);
		if (!record) return undefined;
		if (record.retired.includes(turnId)) return undefined;
		if (record.started) return undefined;
		if (record.turnId !== undefined && turnId !== record.turnId) return undefined;
		if (record.turnId === undefined) {
			record.turnId = turnId;
			record.provisional = true;
		}
		record.started = true;
		record.hadEvents = true;
		return record;
	}

	/**
	 * Bind a non-start event to the session's turn record. Approval frames may
	 * match the reviewed origin turn; every other stale/retired frame is inert.
	 * Returns undefined when there is no record or the event does not match.
	 */
	bindEvent(sessionId: string, event: GatewayEvent): SessionTurn | undefined {
		const record = this.turns.get(sessionId);
		if (!record) return undefined;
		const eventTurn = event.turnId;
		if (record.turnId === undefined) {
			// Events arrived before any start: bind provisionally so pre-RPC
			// output and completion are retained once.
			record.turnId = eventTurn;
			record.provisional = true;
		} else if (eventTurn !== record.turnId && !this.approvalMatches(record, event)) {
			return undefined; // stale turn: no state, text or usage mutation
		} else if (record.retired.includes(eventTurn)) {
			return undefined; // late duplicate of a retired turn
		}
		record.hadEvents = true;
		return record;
	}

	/** Approval events carry the reviewed origin turn alongside the transport turn. */
	approvalMatches(record: SessionTurn, event: GatewayEvent): boolean {
		if (event.type !== "needs_approval" && event.type !== "permission.required" &&
			event.type !== "approval.resolved" && event.type !== "permission.resolved" && event.type !== "approval.resumed") return false;
		const origin = (event as { originTurnId?: string }).originTurnId;
		return origin !== undefined && origin === record.turnId;
	}

	/** Move the binding to the approved continuation turn; true when applied. */
	applyApprovalResume(sessionId: string, originTurnId: string | undefined, turnId: string): boolean {
		const record = this.turns.get(sessionId);
		if (!record || originTurnId !== record.turnId) return false;
		if (record.turnId && !record.retired.includes(record.turnId)) {
			record.retired.push(record.turnId);
			if (record.retired.length > MAX_RETIRED_TURNS) record.retired.shift();
		}
		record.turnId = turnId;
		record.provisional = false;
		record.started = true;
		record.usageLatest = 0;
		return true;
	}

	/** Retire a turn: release the guard once, keep a bounded retired-ID set. */
	retireTurn(sessionId: string, turnId: string): void {
		const record = this.turnRecord(sessionId);
		if (!record.retired.includes(turnId)) {
			record.retired.push(turnId);
			if (record.retired.length > MAX_RETIRED_TURNS) record.retired.shift();
		}
		if (record.turnId === turnId) {
			record.turnId = undefined;
			record.provisional = true;
			record.started = false;
			record.hadEvents = false;
			record.warnedSlow = false;
			record.usageLatest = 0;
		}
		this.clearInFlight(sessionId);
	}

	/**
	 * Local watchdog: a UI diagnostic deadline, never ownership authority. On
	 * expiry while connected it warns once, retains the in-flight guard, and
	 * leaves inspection, cancel and exit available.
	 */
	private startInFlightTimer(sessionId: string): void {
		const previous = this.inFlightTimers.get(sessionId);
		if (previous) clearTimeout(previous);
		const timer = setTimeout(() => {
			this.inFlightTimers.delete(sessionId);
			const record = this.turns.get(sessionId);
			if (this.inFlight.has(sessionId) && record && !record.warnedSlow) {
				record.warnedSlow = true;
				if (sessionId === this.options.visibleSessionId()) this.options.onSlowTurn?.(sessionId);
			}
		}, this.inFlightTimeoutMs);
		timer.unref();
		this.inFlightTimers.set(sessionId, timer);
	}

	/** Remove a session from in-flight tracking and cancel its stale timer. */
	clearInFlight(sessionId: string): void {
		this.inFlight.delete(sessionId);
		const timer = this.inFlightTimers.get(sessionId);
		if (timer) {
			clearTimeout(timer);
			this.inFlightTimers.delete(sessionId);
		}
	}

	/** Retire every in-flight guard (gateway exit). */
	clearAllInFlight(): void {
		for (const sessionId of [...this.inFlight]) this.clearInFlight(sessionId);
	}

	/** Drop every watchdog without changing guards (app exit). */
	clearInFlightTimers(): void {
		for (const timer of this.inFlightTimers.values()) clearTimeout(timer);
		this.inFlightTimers.clear();
	}

	// ─── tokens / usage ─────────────────────────────────────────────────────

	/** Common "refresh footer" value: completed total plus the active turn. */
	displayTokens(sessionId: string): number {
		return this.sessionTokens + this.usageLatest(sessionId);
	}

	/** Preserve the visible counter under its session before switching away. */
	saveSessionTokens(sessionId: string): void {
		this.sessionTokensById.set(sessionId, this.sessionTokens);
	}

	/** Restore a session's completed total into the visible counter. */
	restoreSessionTokens(sessionId: string): void {
		this.sessionTokens = this.sessionTokensById.get(sessionId) ?? 0;
	}

	resetSessionTokens(): void {
		this.sessionTokens = 0;
	}

	/** Add a turn total once: duplicate or late completion adds zero. */
	accountCompletion(sessionId: string, tokens: number): void {
		if (sessionId === this.options.visibleSessionId()) {
			this.sessionTokens += tokens;
			this.sessionTokensById.set(sessionId, this.sessionTokens);
			return;
		}
		this.sessionTokensById.set(sessionId, (this.sessionTokensById.get(sessionId) ?? 0) + tokens);
	}

	// ─── delegated child runs ───────────────────────────────────────────────

	startDelegated(childId: string): void {
		this.delegatedRunning.add(childId);
		this.delegatedText.set(childId, "");
	}

	appendDelegated(childId: string, text: string): void {
		this.delegatedText.set(childId, (this.delegatedText.get(childId) ?? "").concat(text).slice(-MAX_DELEGATED_TEXT));
	}

	delegatedTextFor(childId: string): string {
		return this.delegatedText.get(childId) ?? "";
	}

	/** Settle/ pause a delegated run and return its streamed text. */
	finishDelegated(childId: string): string {
		this.delegatedRunning.delete(childId);
		const text = this.delegatedText.get(childId) ?? "";
		this.delegatedText.delete(childId);
		return text;
	}

	// ─── streamed tool state ────────────────────────────────────────────────

	noteToolStart(sessionId: string, toolId: string, name: string): void {
		const tools = this.activeTools.get(sessionId) ?? new Map<string, string>();
		tools.set(toolId, name);
		this.activeTools.set(sessionId, tools);
	}

	noteToolComplete(sessionId: string, toolId: string): void {
		this.activeTools.get(sessionId)?.delete(toolId);
	}

	clearActiveTools(sessionId: string): void {
		this.activeTools.delete(sessionId);
	}

	/** "tool +N" summary for the activity bar, or undefined when idle. */
	activeToolLabel(sessionId: string): string | undefined {
		const tools = this.activeTools.get(sessionId);
		const current = tools?.values().next().value;
		if (!current) return undefined;
		return tools && tools.size > 1 ? `${current} +${tools.size - 1}` : current;
	}

	// ─── steering queues ────────────────────────────────────────────────────

	isSteeringBusy(sessionId: string): boolean {
		return this.steeringBusy.has(sessionId);
	}

	setSteeringBusy(sessionId: string, busy: boolean): void {
		if (busy) this.steeringBusy.add(sessionId);
		else this.steeringBusy.delete(sessionId);
	}

	isSteeringFlushing(sessionId: string): boolean {
		return this.steeringFlushing.has(sessionId);
	}

	setSteeringFlushing(sessionId: string, flushing: boolean): void {
		if (flushing) this.steeringFlushing.add(sessionId);
		else this.steeringFlushing.delete(sessionId);
	}

	isInterrupted(sessionId: string): boolean {
		return this.interruptedSteering.has(sessionId);
	}

	setInterrupted(sessionId: string, interrupted: boolean): void {
		if (interrupted) this.interruptedSteering.add(sessionId);
		else this.interruptedSteering.delete(sessionId);
	}

	queuedSteering(sessionId: string): readonly string[] {
		return this.nextSteering.get(sessionId) ?? [];
	}

	/** Append one locally retained prompt; throws when its cap is reached. */
	pushSteering(sessionId: string, text: string): void {
		const pending = this.nextSteering.get(sessionId) ?? [];
		if (pending.length >= MAX_QUEUED_STEERING) throw new Error("The steering queue is full");
		this.nextSteering.set(sessionId, [...pending, text]);
	}

	replaceSteering(sessionId: string, items: string[]): void {
		if (items.length > 0) this.nextSteering.set(sessionId, items);
		else this.nextSteering.delete(sessionId);
	}

	/** Remove and return the head prompt (undefined when empty). */
	shiftSteering(sessionId: string): string | undefined {
		const pending = this.nextSteering.get(sessionId);
		if (!pending || pending.length === 0) return undefined;
		const [head, ...rest] = pending;
		if (rest.length > 0) this.nextSteering.set(sessionId, rest);
		else this.nextSteering.delete(sessionId);
		return head;
	}

	hasKiroFallback(sessionId: string): boolean {
		return this.kiroFallbackSessions.has(sessionId);
	}

	setKiroFallback(sessionId: string, active: boolean): void {
		if (active) this.kiroFallbackSessions.add(sessionId);
		else this.kiroFallbackSessions.delete(sessionId);
	}

	// ─── hidden-session tails ───────────────────────────────────────────────

	takeSessionTail(sessionId: string): SessionTail | undefined {
		return this.sessionTails.take(sessionId);
	}

	bufferHiddenEvent(event: GatewayEvent): void {
		this.sessionTails.buffer(event);
	}

	pruneSessionTails(): void {
		this.sessionTails.prune();
	}

	// ─── retention / pruning ────────────────────────────────────────────────

	/**
	 * Mark a session as recently visited and prune dormant state once the
	 * retained set exceeds its bound. Visible, in-flight, delegated and family
	 * sessions are never evicted.
	 */
	rememberSession(sessionId: string): void {
		const index = this.sessionRecency.indexOf(sessionId);
		if (index !== -1) this.sessionRecency.splice(index, 1);
		this.sessionRecency.push(sessionId);
		while (this.sessionRecency.length > this.maxRetainedSessions) {
			const candidate = this.sessionRecency.find(
				(id) => id !== this.options.visibleSessionId()
					&& !this.inFlight.has(id)
					&& !this.delegatedRunning.has(id)
					&& !this.options.isFamilyMember(id),
			);
			if (candidate === undefined) break;
			this.pruneSessionState(candidate);
			this.options.onEvicted?.(candidate);
		}
	}

	/**
	 * Drop every store-owned dormant record for one session. Callers are
	 * responsible for the retention guard (visible/in-flight/family).
	 */
	pruneSessionState(sessionId: string): void {
		this.turns.delete(sessionId);
		this.sessionTokensById.delete(sessionId);
		this.activeTools.delete(sessionId);
		this.nextSteering.delete(sessionId);
		this.steeringBusy.delete(sessionId);
		this.steeringFlushing.delete(sessionId);
		this.interruptedSteering.delete(sessionId);
		this.kiroFallbackSessions.delete(sessionId);
		this.delegatedText.delete(sessionId);
		this.delegatedRunning.delete(sessionId);
		const timer = this.inFlightTimers.get(sessionId);
		if (timer !== undefined) {
			clearTimeout(timer);
			this.inFlightTimers.delete(sessionId);
		}
		this.sessionTails.pruneSession(sessionId);
		const index = this.sessionRecency.indexOf(sessionId);
		if (index !== -1) this.sessionRecency.splice(index, 1);
	}

	/**
	 * Guarded prune: visible, in-flight, delegated and family sessions keep
	 * their state. Returns true when the session was actually pruned.
	 */
	pruneSessionIfDormant(sessionId: string): boolean {
		if (
			sessionId === this.options.visibleSessionId() ||
			this.inFlight.has(sessionId) ||
			this.delegatedRunning.has(sessionId) ||
			this.options.isFamilyMember(sessionId)
		) {
			return false;
		}
		this.pruneSessionState(sessionId);
		return true;
	}

	// ─── approvals ──────────────────────────────────────────────────────────

	pendingApproval(requestId: string): ApprovalPromptEvent | undefined {
		return this.pendingApprovals.get(requestId);
	}

	rememberPendingApproval(event: ApprovalPromptEvent): void {
		this.pendingApprovals.set(event.requestId, event);
	}

	deletePendingApproval(requestId: string): void {
		this.pendingApprovals.delete(requestId);
	}

	pendingApprovalEntries(): Array<[string, ApprovalPromptEvent]> {
		return [...this.pendingApprovals];
	}

	clearPendingApprovals(sessionId?: string): void {
		if (sessionId === undefined) {
			this.pendingApprovals.clear();
			return;
		}
		for (const [requestId, event] of [...this.pendingApprovals]) {
			if (event.sessionId === sessionId) this.pendingApprovals.delete(requestId);
		}
	}

	hasApprovalDialog(requestId: string): boolean {
		return this.approvalDialogs.has(requestId);
	}

	approvalDialog(requestId: string): ApprovalDialog | undefined {
		return this.approvalDialogs.get(requestId);
	}

	setApprovalDialog(requestId: string, entry: ApprovalDialog): void {
		this.approvalDialogs.set(requestId, entry);
	}

	deleteApprovalDialog(requestId: string): void {
		this.approvalDialogs.delete(requestId);
	}

	approvalDialogCount(): number {
		return this.approvalDialogs.size;
	}

	approvalDialogEntries(): ApprovalDialog[] {
		return [...this.approvalDialogs.values()];
	}

	firstApprovalDialog(): ApprovalDialog | undefined {
		return this.approvalDialogs.values().next().value;
	}
}
