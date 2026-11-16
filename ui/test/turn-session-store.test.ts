import assert from "node:assert/strict";
import { test } from "node:test";
import type { GatewayEvent } from "../src/protocol.ts";
import { TurnSessionStore, type ApprovalPromptEvent } from "../src/turnSessionStore.ts";

function approvalEvent(sessionId: string, requestId = "req-1"): ApprovalPromptEvent {
	return {
		protocolVersion: 2,
		type: "needs_approval",
		sessionId,
		turnId: "t",
		requestId,
		operation: "exec",
		tool: "terminal",
		target: "ls",
		argv: ["ls"],
		shell: "",
		cwd: "/tmp",
		backend: "host",
		timeout: 60,
		network: [],
		capabilities: [],
		contentDigest: "",
		contentPreview: "",
		contentLength: 0,
		status: "pending",
	};
}

test("turn bindings: start, confirm, mismatch, retire and duplicate inertness", () => {
	const store = new TurnSessionStore({
		visibleSessionId: () => "s1",
		isFamilyMember: () => false,
	});
	store.beginTurn("s1");
	assert.equal(store.isInFlight("s1"), true);
	assert.equal(store.hadEvents("s1"), false);

	const started = store.bindStart("s1", "t1");
	assert.equal(started?.turnId, "t1");
	assert.equal(started?.started, true);
	assert.equal(started?.hadEvents, true);
	assert.equal(store.bindStart("s1", "t1"), undefined, "duplicate start is inert");
	assert.equal(store.bindStart("s1", "t2"), undefined, "foreign start is inert");

	assert.equal(store.confirmTurn("s1", "t2"), "mismatch");
	assert.equal(store.turnId("s1"), "t1", "mismatch keeps the bound guard");
	assert.equal(store.confirmTurn("s1", "t1"), "confirmed");

	store.retireTurn("s1", "t1");
	assert.equal(store.isInFlight("s1"), false);
	assert.equal(store.turnId("s1"), undefined);
	assert.equal(store.isRetired("s1", "t1"), true);
	assert.equal(store.bindStart("s1", "t1"), undefined, "retired duplicate is inert");
	store.clearInFlightTimers();
});

test("per-session state: tokens, tools, steering and delegated text stay independent", () => {
	let visible = "s1";
	const store = new TurnSessionStore({
		visibleSessionId: () => visible,
		isFamilyMember: () => false,
	});
	store.sessionTokens = 10;
	store.saveSessionTokens("s1");
	store.resetSessionTokens();
	store.restoreSessionTokens("s1");
	assert.equal(store.sessionTokens, 10, "restoring the visible total keeps prior usage");

	store.beginTurn("s1");
	store.setUsageLatest("s1", 4);
	assert.equal(store.displayTokens("s1"), 14);
	store.retireTurn("s1", "t1");

	store.noteToolStart("s1", "tool-1", "terminal");
	store.noteToolStart("s1", "tool-2", "read_file");
	assert.equal(store.activeToolLabel("s1"), "terminal +1");
	store.noteToolComplete("s1", "tool-1");
	assert.equal(store.activeToolLabel("s1"), "read_file");
	store.clearActiveTools("s1");
	assert.equal(store.activeToolLabel("s1"), undefined);

	store.pushSteering("s1", "first");
	store.pushSteering("s1", "second");
	assert.equal(store.queuedSteering("s1").length, 2);
	assert.equal(store.shiftSteering("s1"), "first");
	assert.equal(store.queuedSteering("s1")[0], "second");

	store.startDelegated("child");
	store.appendDelegated("child", "working");
	assert.equal(store.delegatedTextFor("child"), "working");
	assert.equal(store.isRunning("child"), true);
	assert.equal(store.finishDelegated("child"), "working");
	assert.equal(store.delegatedTextFor("child"), "");
	assert.equal(store.isRunning("child"), false);

	visible = "s2";
	store.accountCompletion("s1", 3);
	assert.equal(store.sessionTokensById.get("s1"), 13, "hidden completions account under their session");
	assert.equal(store.sessionTokens, 10, "the visible counter does not absorb a hidden total");
	store.clearInFlightTimers();
});

test("retention: LRU pruning drops turn, approval, tail and tool state but preserves protected sessions", () => {
	const evicted: string[] = [];
	const family = new Set<string>();
	const store = new TurnSessionStore({
		visibleSessionId: () => "visible",
		isFamilyMember: (sessionId) => family.has(sessionId),
		maxRetainedSessions: 2,
		onEvicted: (sessionId) => evicted.push(sessionId),
	});

	store.rememberSession("visible");
	store.rememberSession("old");
	store.turnRecord("old");
	store.sessionTokensById.set("old", 5);
	store.noteToolStart("old", "tool", "terminal");
	store.pushSteering("old", "queued");
	store.rememberPendingApproval(approvalEvent("old"));
	store.sessionTails.buffer({
		protocolVersion: 2,
		sessionId: "old",
		turnId: "t",
		type: "message.delta",
		text: "hidden",
	} as GatewayEvent);
	store.setApprovalDialog("req-1", {
		requestId: "req-1",
		sessionId: "old",
		originTurnId: undefined,
		transportTurnId: "t",
		card: {} as never,
		evidenceAdded: false,
		close: () => {},
		dialog: Promise.resolve(undefined),
	});

	// The third retained session evicts the oldest dormant one.
	store.rememberSession("mid");
	assert.deepEqual(evicted, ["old"]);
	assert.equal(store.turns.has("old"), false);
	assert.equal(store.sessionTokensById.has("old"), false);
	assert.equal(store.activeTools.has("old"), false);
	assert.equal(store.nextSteering.has("old"), false);
	// Retained approval evidence is not LRU state: it is retired only by a
	// decision/turn completion, so returning can still rebuild the review.
	assert.equal(store.pendingApprovals.has("req-1"), true);
	assert.equal(store.approvalDialogs.has("req-1"), true);
	assert.equal(store.sessionTails.has("old"), false, "buffered hidden tail is dropped");

	// A visible, in-flight or family session is never pruned.
	store.rememberSession("visible");
	store.beginTurn("busy");
	family.add("relative");
	store.rememberSession("relative");
	store.rememberSession("next-1");
	store.rememberSession("next-2");
	assert.equal(store.turns.has("busy"), true, "in-flight session survives the LRU bound");
	assert.equal(store.pruneSessionIfDormant("busy"), false);
	assert.equal(store.pruneSessionIfDormant("relative"), false);
	assert.equal(store.pruneSessionIfDormant("visible"), false);
	store.clearInFlightTimers();
});
