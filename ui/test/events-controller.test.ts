// EventRouter seam: gateway frames bind turns, stale work is inert, hidden
// sessions keep a tail, workspace notifications stay out of turn state, and a
// malformed frame never throws.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ActivityStore, type ShellJob } from "../src/activity.ts";
import {
	EventRouter,
	type AgentChildEvent,
	type EventHost,
	type QuestionPromptEvent,
} from "../src/app/events.ts";
import type { HarnessJobView } from "../src/app/status.ts";
import type { GatewayEvent } from "../src/protocol.ts";
import { TurnSessionStore, type ApprovalPromptEvent } from "../src/turnSessionStore.ts";
import type { WorkspaceEvent } from "../src/workspace/protocol.ts";

const SESSION = "session-a";
const HIDDEN = "session-b";

function ev(overrides: Record<string, unknown>): GatewayEvent {
	return { protocolVersion: 2, sessionId: SESSION, turnId: "turn-1", ...overrides } as GatewayEvent;
}

function fixture() {
	const family = new Set<string>();
	const applied: GatewayEvent[] = [];
	const order: string[] = [];
	const notices: string[] = [];
	const users: string[] = [];
	const suspended: string[] = [];
	const outputs: Array<{ sessionId: string; toolId: string; name: string; result: string; truncated: boolean }> = [];
	const logs: Array<{ level: string; message: string }> = [];
	const featureHandlers = new Set<(event: GatewayEvent) => void>();
	const harnessJobs: HarnessJobView[] = [];
	const workspaceEvents: WorkspaceEvent[] = [];
	const unknownSessions: string[] = [];
	const queuedQuestions: QuestionPromptEvent[] = [];
	const promptedQuestions: QuestionPromptEvent[] = [];
	const promptedApprovals: ApprovalPromptEvent[] = [];
	const clearedApprovals: string[] = [];
	const kiroFallbacks: string[] = [];
	const contextRefreshes: string[] = [];
	const running: boolean[] = [];
	const flushes: string[] = [];
	const badgeRefreshes: string[] = [];
	const childEvents: AgentChildEvent[] = [];
	let renders = 0;
	let familyRefreshes = 0;
	let chromeInvalidations = 0;
	let contextInvalidations = 0;
	let visible: string | undefined = SESSION;
	let exiting = false;

	const turnSession = new TurnSessionStore({
		visibleSessionId: () => visible,
		isFamilyMember: (sessionId) => family.has(sessionId),
	});
	const activity = new ActivityStore();

	const log: EventHost["log"] = {
		enabled: true,
		error: (message) => logs.push({ level: "error", message }),
		warn: (message) => logs.push({ level: "warn", message }),
		info: (message) => logs.push({ level: "info", message }),
		debug: (message) => logs.push({ level: "debug", message }),
		trace: (message) => logs.push({ level: "trace", message }),
	};
	const footer: EventHost["footer"] = {
		status: "ready",
		tokens: 0,
		contextTokens: 0,
		contextPercent: 0,
		pulseGrowth: () => false,
	};
	const transcript: EventHost["transcript"] = {
		apply: (event) => {
			applied.push(event);
			order.push(`apply:${event.type}`);
			return event.type === "message.complete"
				? { tokens: event.tokens, cancelled: event.cancelled }
				: undefined;
		},
		addNotice: (text, kind) => {
			notices.push(`${kind ?? "info"}:${text}`);
			order.push(`notice:${kind ?? "info"}`);
		},
		addUser: (text) => users.push(text),
		suspend: (phase = "stopping") => suspended.push(phase),
	};

	const host: EventHost = {
		tui: { requestRender: () => { renders += 1; } },
		log,
		activity,
		turnSession,
		footer,
		shell: { invalidateChrome: () => { chromeInvalidations += 1; } },
		transcript,
		outputStore: {
			put: (sessionId, toolId, name, result, truncated) => {
				outputs.push({ sessionId, toolId, name, result, truncated });
			},
		},
		isExiting: () => exiting,
		current: () => (visible === undefined ? undefined : {
			id: visible,
			title: visible,
			model: "m",
			provider: "p",
			status: "active",
			lastActivity: null,
		}),
		featureEventHandlers: () => featureHandlers,
		workspaceEvent: (event) => workspaceEvents.push(event),
		setHarnessJob: (job) => harnessJobs.push(job),
		refreshFamily: () => { familyRefreshes += 1; },
		isFamilyMember: (sessionId) => family.has(sessionId),
		noteUnknownSession: (sessionId) => unknownSessions.push(sessionId),
		queueQuestion: (event) => queuedQuestions.push(event),
		promptQuestion: (event) => promptedQuestions.push(event),
		promptApproval: (event) => promptedApprovals.push(event),
		clearApprovals: (sessionId) => clearedApprovals.push(sessionId),
		switchKiroFallback: (sessionId) => kiroFallbacks.push(sessionId),
		invalidateContextUsage: () => { contextInvalidations += 1; },
		refreshContextUsage: (sessionId) => contextRefreshes.push(sessionId),
		setRunning: (value) => running.push(value),
		flushSteering: (sessionId) => flushes.push(sessionId),
		refreshChangesBadge: (sessionId) => badgeRefreshes.push(sessionId),
		onChildEvent: (event) => childEvents.push(event),
	};

	const router = new EventRouter(host);

	return {
		router,
		host,
		turnSession,
		activity,
		family,
		applied,
		order,
		notices,
		users,
		suspended,
		outputs,
		logs,
		featureHandlers,
		harnessJobs,
		workspaceEvents,
		unknownSessions,
		queuedQuestions,
		promptedQuestions,
		promptedApprovals,
		clearedApprovals,
		kiroFallbacks,
		contextRefreshes,
		running,
		flushes,
		badgeRefreshes,
		childEvents,
		footer,
		setVisible: (id: string | undefined) => { visible = id; },
		setExiting: (value: boolean) => { exiting = value; },
		renders: () => renders,
		familyRefreshes: () => familyRefreshes,
		chromeInvalidations: () => chromeInvalidations,
		contextInvalidations: () => contextInvalidations,
	};
}

test("the first event binds a turn after feature subscribers observe it", () => {
	const f = fixture();
	f.turnSession.beginTurn(SESSION);
	f.featureHandlers.add((event) => f.order.push(`feature:${event.type}`));

	f.router.route(ev({ type: "message.start" }));
	f.router.route(ev({ type: "message.delta", text: "hello" }));

	assert.equal(f.turnSession.turnBinding(SESSION)?.turnId, "turn-1");
	assert.equal(f.turnSession.hadEvents(SESSION), true);
	assert.deepEqual(f.applied.map((event) => event.type), ["message.start", "message.delta"]);
	assert.deepEqual(f.order, [
		"feature:message.start",
		"apply:message.start",
		"feature:message.delta",
		"apply:message.delta",
	]);
	assert.ok(f.renders() >= 2);
});

test("stale and retired turn frames never reach the transcript", () => {
	const f = fixture();
	f.turnSession.beginTurn(SESSION);
	f.router.route(ev({ type: "message.start" }));
	const appliedBefore = f.applied.length;

	// A different turn for a session whose bound turn is still live is stale.
	f.router.route(ev({ type: "message.delta", text: "stale", turnId: "turn-2" }));
	assert.equal(f.applied.length, appliedBefore);

	// Complete and retire turn-1, then start turn-2 and replay turn-1 late.
	f.router.route(ev({
		type: "message.complete",
		text: "done",
		tokens: 7,
		iterations: 1,
		toolCalls: 0,
		cancelled: false,
	}));
	assert.deepEqual(f.running, [false]);
	f.turnSession.beginTurn(SESSION);
	f.router.route(ev({ type: "message.start", turnId: "turn-2" }));
	const afterNewStart = f.applied.length;
	f.router.route(ev({ type: "message.delta", text: "late", turnId: "turn-1" }));
	assert.equal(f.applied.length, afterNewStart);
});

test("hidden-session frames buffer into that session's live tail", () => {
	const f = fixture();
	f.router.route(ev({ sessionId: HIDDEN, type: "message.delta", text: "background work" }));

	const tail = f.turnSession.sessionTails.get(HIDDEN);
	assert.ok(tail);
	assert.deepEqual(tail.blocks, [{
		kind: "assistant",
		turnId: "turn-1",
		text: "background work",
		completed: false,
	}]);
	assert.equal(f.applied.length, 0, "a hidden session never renders");
	assert.equal(f.renders(), 0);
});

test("workspace notifications go to the workspace sink and never enter turn state", () => {
	const f = fixture();
	const event: WorkspaceEvent = {
		type: "workspace.diagnostics",
		workspaceId: "w1",
		epoch: 1,
		serverGeneration: 2,
		uri: "file:///root/main.py",
		version: 3,
		source: "pyright",
		diagnostics: [],
	};

	f.router.route(event);

	assert.deepEqual(f.workspaceEvents, [event]);
	assert.equal(f.turnSession.turns.size, 0);
	assert.equal(f.applied.length, 0);
	assert.equal(f.renders(), 0);
});

test("child events route through the host, with stale frames dropped", () => {
	const f = fixture();
	f.turnSession.beginTurn(SESSION);
	f.router.route(ev({ type: "message.start" }));

	f.router.route(ev({
		type: "agent.child",
		childSessionId: HIDDEN,
		phase: "start",
		value: "",
	}));
	assert.equal(f.childEvents.length, 1);

	// Stale transport turn for a bound parent is inert.
	f.router.route(ev({
		type: "agent.child",
		childSessionId: HIDDEN,
		phase: "text",
		value: "late",
		turnId: "turn-9",
	}));
	assert.equal(f.childEvents.length, 1);

	// Deletion always passes: the child's cleanup must be observed.
	f.router.route(ev({
		type: "agent.child",
		childSessionId: HIDDEN,
		phase: "settled",
		value: "deleted",
		turnId: "turn-9",
	}));
	assert.equal(f.childEvents.length, 2);
});

test("approval frames reach the host only for the visible session", () => {
	const f = fixture();
	f.turnSession.beginTurn(SESSION);
	f.router.route(ev({ type: "message.start" }));

	f.router.route(ev({
		type: "needs_approval",
		requestId: "req-1",
		operation: "write_file",
		target: "a.txt",
		argv: [],
		cwd: "/tmp",
		backend: "local",
		capabilities: [],
	}));
	assert.equal(f.promptedApprovals.length, 1);
	assert.equal(f.turnSession.pendingApproval("req-1")?.requestId, "req-1");

	f.router.route(ev({
		sessionId: HIDDEN,
		type: "needs_approval",
		requestId: "req-2",
		operation: "write_file",
		target: "b.txt",
		argv: [],
		cwd: "/tmp",
		backend: "local",
		capabilities: [],
	}));
	assert.equal(f.promptedApprovals.length, 1, "a hidden session never opens a card");
	assert.equal(f.turnSession.pendingApproval("req-2")?.sessionId, HIDDEN);
});

test("questions open in the visible session and queue for hidden ones", () => {
	const f = fixture();
	const visibleQuestion = ev({
		type: "question.asked",
		requestId: "q-1",
		question: "Which file?",
		options: [],
		allowOther: false,
	});
	f.router.route(visibleQuestion);
	assert.deepEqual(f.promptedQuestions, [visibleQuestion]);
	assert.equal(f.queuedQuestions.length, 0);

	const hiddenQuestion = ev({
		sessionId: HIDDEN,
		type: "question.asked",
		requestId: "q-2",
		question: "Proceed?",
		options: [],
		allowOther: false,
	});
	f.router.route(hiddenQuestion);
	assert.equal(f.promptedQuestions.length, 1);
	assert.deepEqual(f.queuedQuestions, [hiddenQuestion]);
});

test("shell and harness job frames update their sinks before turn binding", () => {
	const f = fixture();
	const shell: ShellJob = { id: "job-1", command: "pytest", status: "running", foreground: true };
	f.router.route(ev({ type: "shell.job", job: shell }));
	assert.equal(f.activity.listJobs(SESSION)[0]?.id, "job-1");

	const harness: HarnessJobView = {
		id: "harness-1",
		owner_session_id: SESSION,
		session_id: HIDDEN,
		platform: "hermes",
		task: "run the suite",
		status: "running",
		needs_input: false,
		started_at: null,
		ended_at: null,
		exit_code: null,
		command: [],
		stdout: "",
		stderr: "",
		error: "",
		progress: [],
	};
	f.router.route(ev({ type: "harness.job", job: harness }));
	assert.equal(f.harnessJobs.length, 1);
	assert.equal(f.familyRefreshes(), 1, "starting a harness job refreshes the family");
});

test("usage and completion update footer accounting and retire the turn", async () => {
	const f = fixture();
	f.turnSession.beginTurn(SESSION);
	f.router.route(ev({ type: "message.start" }));
	f.turnSession.sessionTokens = 100;

	f.router.route(ev({ type: "usage", tokens: 12, contextTokens: 500, contextPercent: 25 }));
	assert.equal(f.footer.tokens, 112);
	assert.equal(f.footer.contextTokens, 500);
	assert.equal(f.footer.contextPercent, 25);
	assert.equal(f.contextInvalidations(), 1);

	f.router.route(ev({
		type: "message.complete",
		text: "done",
		tokens: 40,
		iterations: 1,
		toolCalls: 0,
		cancelled: false,
	}));
	assert.equal(f.footer.tokens, 140);
	assert.equal(f.running.at(-1), false);
	assert.equal(f.turnSession.isInFlight(SESSION), false);

	await new Promise((resolve) => setTimeout(resolve, 0));
	assert.deepEqual(f.flushes, [SESSION]);
	assert.deepEqual(f.badgeRefreshes, [SESSION]);
});

test("a Kiro quota error asks the host for the fallback", () => {
	const f = fixture();
	f.turnSession.beginTurn(SESSION);
	f.router.route(ev({ type: "message.start" }));
	f.router.route(ev({ type: "error", message: "Kiro usage limit reached (HTTP 429)" }));
	assert.deepEqual(f.kiroFallbacks, [SESSION]);
	assert.equal(f.running.at(-1), true);
});

test("sessionless login frames settle feature subscribers", () => {
	const f = fixture();
	const seen: GatewayEvent[] = [];
	f.featureHandlers.add((event) => seen.push(event));

	f.router.route({
		protocolVersion: 2,
		sessionId: "",
		turnId: "",
		type: "auth.opencode.completed",
		account_id: "acct",
		expiry: 1_800_000_000,
		org_id: "org",
	} as GatewayEvent);

	assert.equal(seen.length, 1, "the login flow must observe its completion frame");
	assert.equal(f.applied.length, 0, "login frames never enter the transcript");
});

test("malformed frames are dropped without throwing or touching bookkeeping", () => {
	const f = fixture();
	f.turnSession.beginTurn(SESSION);
	let featureCalls = 0;
	f.featureHandlers.add(() => { featureCalls += 1; });

	assert.doesNotThrow(() => f.router.route({ type: "message.delta", text: "no session" } as GatewayEvent));
	assert.doesNotThrow(() => f.router.route({ type: "message.delta", sessionId: "", turnId: "t", text: "blank" } as GatewayEvent));
	assert.equal(featureCalls, 0, "invalid session ids never reach feature subscribers");
	assert.equal(f.applied.length, 0);
	assert.ok(f.logs.some((entry) => entry.level === "warn" && entry.message === "event.invalid_session"));

	const frames = [
		{ type: "needs_approval", sessionId: SESSION, turnId: "t", requestId: 17 },
		{ type: "approval.resolved", sessionId: SESSION, turnId: "t" },
		{ type: "future.event", sessionId: SESSION, turnId: "t" },
	] as unknown as GatewayEvent[];
	for (const frame of frames) assert.doesNotThrow(() => f.router.route(frame));
	assert.ok(f.logs.some((entry) => entry.message === "approval.invalid"));
	assert.ok(f.logs.some((entry) => entry.message === "approval.resolved.invalid"));
});
