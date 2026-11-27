import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, type TUI, type TuiMouseEvent } from "@earendil-works/pi-tui";
import {
	ChangesPage,
	type ChangesDiffState,
	type ChangesSnapshot,
} from "../src/components/ChangesPage.ts";
import type { ChangeTurnInfo, ReviewRunInfo } from "../src/protocol.ts";

const review: ReviewRunInfo = {
	id: "review-1",
	snapshotId: "a".repeat(32),
	sessionId: "session-a",
	engine: "rules",
	status: "complete",
	provider: "hermes",
	model: "qwen/qwen4-max",
	createdAt: "2026-11-25T12:00:00Z",
	startedAt: "2026-11-25T12:00:00Z",
	finishedAt: "2026-11-25T12:00:01Z",
	summary: "1 finding: 1 high",
	findings: [{
		rule: "hardcoded-secret",
		severity: "high",
		title: "Possible API secret key added",
		body: "Remove the value from source and rotate it if it was real.",
		file: "src/parser.py",
		line: 4,
		fingerprint: "abc123",
	}],
	error: null,
};

function turn(
	id: string,
	prompt: string,
	overrides: Partial<ChangeTurnInfo> = {},
): ChangeTurnInfo {
	return {
		id,
		sessionId: "session-a",
		prompt,
		createdAt: "2026-11-25T12:00:00Z",
		provider: "hermes",
		model: "qwen/qwen4-max",
		status: "complete",
		stats: { files: 1, additions: 12, deletions: 3 },
		files: [{
			path: "src/parser.py",
			change: "modified",
			additions: 12,
			deletions: 3,
			binary: false,
		}],
		review: null,
		...overrides,
	};
}

const firstId = "a".repeat(32);
const secondId = "b".repeat(32);

const snapshot: ChangesSnapshot = {
	turns: [
		turn(firstId, "Fix the parser and explain the failure", { review }),
		turn(secondId, "Add the missing parser tests", {
			stats: { files: 1, additions: 30, deletions: 0 },
			files: [{
				path: "tests/test_parser.py",
				change: "added",
				additions: 30,
				deletions: 0,
				binary: false,
			}],
		}),
	],
};

function diffFor(turnId: string | undefined): ChangesDiffState {
	if (turnId === firstId) {
		return {
			loading: false,
			truncated: false,
			files: [{
				path: "src/parser.py",
				change: "modified",
				binary: false,
				truncated: false,
				text: "--- a/src/parser.py\n+++ b/src/parser.py\n@@ -1,2 +1,3 @@\n-old\n+new\n+extra\n",
			}],
		};
	}
	return {
		loading: false,
		truncated: false,
		files: [{
			path: "tests/test_parser.py",
			change: "added",
			binary: false,
			truncated: false,
			text: "--- /dev/null\n+++ b/tests/test_parser.py\n@@ -0,0 +1 @@\n+new test\n",
		}],
	};
}

function fakeTui(columns = 110, rows = 32): TUI {
	return {
		terminal: { columns, rows },
		requestRender() {},
	} as unknown as TUI;
}

test("changes page renders prompts, files, stats and review state", () => {
	const page = new ChangesPage(fakeTui(), () => snapshot, diffFor);
	const rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /Changes in this chat/);
	assert.match(rendered, /Prompts\s+1\/2\s+│ \[1 Files/);
	assert.match(rendered, /❯ 01/);
	assert.doesNotMatch(rendered, /╭|╰/);
	assert.match(rendered, /Fix the parser and explain the failure/);
	assert.match(rendered, /src\/parser\.py/);
	assert.match(rendered, /\+12/);
	assert.match(rendered, /−3/);
	assert.match(rendered, /1 finding/);
	assert.ok(page.render(110).every(line => stripTerminalSequences(line).length <= 110));
});

test("review tab shows findings and r starts a run for the selected prompt", () => {
	const started: string[] = [];
	const page = new ChangesPage(fakeTui(), () => snapshot, diffFor, {
		onReview: (id) => started.push(id),
	});
	page.render(110);
	assert.equal(page.handleInput("3"), true);
	const rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /Possible API secret/);
	assert.match(rendered, /src\/parser\.py:4/);
	assert.match(rendered, /Enter\s+Fix in Chat/);
	assert.equal(page.handleInput("r"), true);
	assert.deepEqual(started, [firstId]);
});

test("m starts a model review and the review footer names both engines", () => {
	const started: string[] = [];
	const page = new ChangesPage(fakeTui(), () => snapshot, diffFor, {
		onReviewModel: (id) => started.push(id),
	});
	page.render(110);
	page.handleInput("3");
	const rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /R Rules\s+M Model/);
	assert.equal(page.handleInput("m"), true);
	assert.deepEqual(started, [firstId]);
});

test("review coverage is explicit about partial diffs and omitted findings", () => {
	const reviewed = {
		...review,
		engine: "model",
		coverage: { files: 2, reviewed: 1, chars: 120, truncated: true, invalid: 1 },
	};
	const changes = { ...snapshot, turns: [turn(firstId, "Review the parser", { review: reviewed })] };
	const page = new ChangesPage(fakeTui(), () => changes, diffFor);
	page.handleInput("3");
	const rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /Model reviewed 1\/2 files · partial diff · 1 omitted/);
});

test("Enter on a selected finding opens the fix action for that finding", () => {
	const fixes: Array<[string, string]> = [];
	const page = new ChangesPage(fakeTui(), () => snapshot, diffFor, {
		onFixFinding: (turnId, finding) => fixes.push([turnId, finding.rule]),
	});
	page.render(110);
	page.handleInput("3");
	page.render(110);
	assert.equal(page.handleInput("\r"), true);
	assert.deepEqual(fixes, [[firstId, "hardcoded-secret"]]);
});

test("review separates findings from their details and pages one explanation at a time", () => {
	const unrelated = "An unrelated explanation only for the second finding.";
	const findings = [
		{ ...review.findings[0]!, body: Array.from({ length: 28 }, (_, index) => `Evidence line ${index + 1}`).join("\n") },
		{ ...review.findings[0]!, title: "Check boundary", body: unrelated, fingerprint: "second" },
	];
	const changes = { ...snapshot, turns: [turn(firstId, "Review the parser", { review: { ...review, findings } })] };
	for (const [width, rows] of [[150, 24], [46, 15]] as const) {
		const page = new ChangesPage(fakeTui(width, rows), () => changes, diffFor);
		page.handleInput("3");
		const text = () => page.render(width).map(stripTerminalSequences).join("\n");
		const initial = text();
		assert.match(initial, /Fix in Chat/);
		assert.match(initial, /Finding 1\/2/);
		assert.match(initial, /Evidence line 1/);
		assert.doesNotMatch(initial, /An unrelated explanation/, "only the selected finding's body is rendered");
		if (width >= 100) {
			assert.match(initial, /2\. Check boundary/);
			assert.ok(initial.indexOf("Check boundary") < initial.indexOf("HIGH · src/parser.py:4"));
			assert.equal(initial.match(/Possible API secret key added/g)?.length, 1, "no duplicate headline");
		}
		if (width < 50) assert.doesNotMatch(initial, /Check boundary/);
		page.handleInput("G");
		assert.match(text(), /Evidence line 28/);
		assert.match(text(), /Fix in Chat/, "action stays visible while paging");
		page.handleInput("n");
		assert.match(text(), /Finding 2\/2/);
		assert.match(text(), /An unrelated explanation/);
	}
});

test("a sent finding stays unverified and Clear handled does not bring it back", () => {
	const second = { ...review.findings[0]!, title: "Boundary check", fingerprint: "second" };
	const sent = new Map([
		[`${firstId}:${review.findings[0]!.fingerprint}`, {
			turnId: firstId,
			finding: review.findings[0]!,
			cleared: false,
		}],
	]);
	const changes = {
		...snapshot,
		turns: [turn(firstId, "Review the parser", { review: { ...review, findings: [second] } })],
		sentFindings: sent,
	};
	let cleared = "";
	const page = new ChangesPage(fakeTui(150), () => changes, diffFor, {
		onClearHandled: (turnId) => { cleared = turnId; sent.get(`${firstId}:${review.findings[0]!.fingerprint}`)!.cleared = true; },
	});
	page.handleInput("3");
	const text = () => page.render(150).map(stripTerminalSequences).join("\n");
	assert.match(text(), /Sent {2}Possible API secret/);
	assert.doesNotMatch(text(), /Fixed/, "absence from the historical review does not verify the repaired file");
	assert.match(text(), /C {2}Clear handled/);
	assert.match(text(), /Boundary check/, "remaining findings stay actionable");
	page.handleInput("c");
	assert.equal(cleared, firstId);
	changes.turns[0]!.review = { ...review, findings: [review.findings[0]!, second] };
	assert.doesNotMatch(text(), /Clear handled|Possible API secret/);
});

test("the wheel selects prompts and files in their respective panes", () => {
	const page = new ChangesPage(fakeTui(150), () => snapshot, diffFor);
	page.render(150);
	const wheel = (x: number, y: number, wheelDelta = 1) => ({ type: "wheel", button: "none", x, y, wheelDelta } as TuiMouseEvent);
	page.handleMouse(wheel(6, 5));
	assert.match(page.render(150).map(stripTerminalSequences).join("\n"), /❯ 02/);
	page.handleInput("1");
	page.handleMouse(wheel(80, 10, 1));
	assert.match(page.render(150).map(stripTerminalSequences).join("\n"), /❯ 02/, "detail wheel never reorders prompts");

	const files = [...snapshot.turns[0]!.files, ...snapshot.turns[1]!.files];
	const multi = new ChangesPage(fakeTui(150), () => ({ ...snapshot, turns: [turn(firstId, "Two files", { files })] }), diffFor);
	multi.render(150);
	multi.handleMouse(wheel(80, 10));
	assert.match(multi.render(150).map(stripTerminalSequences).join("\n"), /▸ A tests\/test_parser\.py/);
});

test("the last diff page does not clip its last lines", () => {
	const state = diffFor(firstId);
	state.files[0]!.text = Array.from({ length: 100 }, (_, i) => `+diff row ${i + 1}`).join("\n");
	const page = new ChangesPage(fakeTui(110, 24), () => snapshot, () => state);
	page.handleInput("2");
	page.render(110);
	page.handleInput("G");
	const rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /\+diff row 100/);
	assert.match(rendered, /–100 of 100 rows/);
});

test("a failed review offers Fix this and sends it once", () => {
	const failed = { ...review, status: "failed" as const, findings: [], summary: "rules engine crashed", error: "rules engine crashed while parsing the diff" };
	const changes = { ...snapshot, turns: [turn(firstId, "Review the parser", { review: failed })] };
	const fixes: string[] = [];
	const handled = new Set<string>();
	const page = new ChangesPage(fakeTui(150), () => ({ ...changes, handledErrors: handled }), diffFor, {
		onFixFailure: (_turnId, code) => { fixes.push(code); handled.add(code); },
	});
	page.handleInput("3");
	const text = () => page.render(150).map(stripTerminalSequences).join("\n");
	assert.match(text(), /Could not load this diff|rules engine crashed/);
	assert.match(text(), /Fix this/);
	page.handleInput("\r");
	assert.equal(fixes.length, 1);
	assert.match(text(), /Sent to chat/);
	page.handleInput("\r");
	assert.equal(fixes.length, 1, "a failure is handed over once");
});

test("selecting a finding by mouse does not fix it until its action is clicked", () => {
	const findings = [review.findings[0]!, { ...review.findings[0]!, title: "Second issue", fingerprint: "second" }];
	const changes = { ...snapshot, turns: [turn(firstId, "Review", { review: { ...review, findings } })] };
	const fixes: string[] = [];
	const page = new ChangesPage(fakeTui(150), () => changes, diffFor, {
		onFixFinding: (_, finding) => fixes.push(finding.fingerprint),
	});
	const click = (label: string): void => {
		const rows = page.render(150).map(stripTerminalSequences);
		const y = rows.findIndex(row => row.includes(label));
		const x = rows[y]?.indexOf(label);
		assert.ok(x !== undefined && x >= 0);
		page.handleMouse({ type: "click", button: "left", x, y } as TuiMouseEvent);
	};
	click("3 Review");
	click("Second issue");
	page.handleInput("\r");
	assert.deepEqual(fixes, ["second"], "Enter acts on the finding selected by the click");
	click("Enter  Fix in Chat");
	assert.deepEqual(fixes, ["second", "second"]);
});

test("a remaining finding can be sent after an earlier finding was handled", () => {
	const first = review.findings[0]!;
	const second = { ...first, title: "Second issue", fingerprint: "second" };
	const changes = {
		...snapshot,
		turns: [turn(firstId, "Review", { review: { ...review, findings: [first, second] } })],
		sentFindings: new Map([[`${firstId}:${first.fingerprint}`, { turnId: firstId, finding: first }]]),
	};
	const fixes: string[] = [];
	const page = new ChangesPage(fakeTui(), () => changes, diffFor, {
		onFixFinding: (_, finding) => fixes.push(finding.fingerprint),
	});
	page.handleInput("3");
	assert.match(page.render(110).map(stripTerminalSequences).join("\n"), /Second issue/);
	page.handleInput("\r");
	assert.deepEqual(fixes, ["second"]);
});

test("click and Enter hand off the failure shown in the diff, not the review", () => {
	const failed = { ...review, status: "failed" as const, error: "Review error" };
	const changes = { ...snapshot, turns: [turn(firstId, "Review", { review: failed })] };
	const fixes: Array<[string, string]> = [];
	const page = new ChangesPage(fakeTui(), () => changes, () => ({ ...diffFor(firstId), error: "Diff error" }), {
		onFixFailure: (id, code) => fixes.push([id, code]),
	});
	page.handleInput("2");
	const rows = page.render(110).map(stripTerminalSequences);
	const y = rows.findIndex(row => row.includes("Fix this"));
	assert.ok(y >= 0);
	page.handleMouse({ type: "click", button: "left", x: rows[y]!.indexOf("Enter"), y } as TuiMouseEvent);
	page.handleInput("\r");
	assert.equal(fixes.length, 2);
	for (const [id, code] of fixes) {
		assert.equal(id, firstId);
		assert.match(code, /Diff error/);
	}
});

test("a read-only chat offers no fix action for a finding or a failure", () => {
	const failed = { ...review, status: "failed" as const, error: "engine crashed" };
	const turns = [
		turn(firstId, "Review the parser", { review: failed }),
		turn(secondId, "Review again", { review }),
	];
	const fixes: string[] = [];
	const page = new ChangesPage(fakeTui(), () => ({ turns, hasChat: true, canFix: false }), diffFor, {
		onFixFailure: (id) => fixes.push(id),
		onFixFinding: (id) => fixes.push(id),
	});
	page.handleInput("3");
	let text = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(text, /engine crashed/);
	assert.doesNotMatch(text, /Fix this/);
	assert.match(text, /Subagent chats are for inspection/);
	page.handleInput("\r");
	page.handleInput("\x1b[B");
	text = page.render(110).map(stripTerminalSequences).join("\n");
	assert.doesNotMatch(text, /Fix in Chat/);
	assert.match(text, /Subagent chat: return to the main chat/);
	page.handleInput("\r");
	assert.deepEqual(fixes, []);
});

test("a queued handoff reads Queued, not Sent", () => {
	const finding = review.findings[0]!;
	const handled = new Map([[`${firstId}:${finding.fingerprint}`, { turnId: firstId, finding, queued: true }]]);
	const changes = { ...snapshot, turns: [turn(firstId, "Review", { review })], sentFindings: handled };
	const page = new ChangesPage(fakeTui(110), () => changes, diffFor);
	page.handleInput("3");
	const text = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(text, /Queued {2}Possible API secret/);
	assert.doesNotMatch(text, /Sent {2}Possible API secret/);
});

test("pages render inside viewports shorter than eight rows", () => {
	const half = new ChangesPage(fakeTui(40, 5), () => snapshot, diffFor);
	half.setViewportRows(5);
	half.handleInput("3");
	const rows = half.render(40).map(stripTerminalSequences);
	assert.equal(rows.length, 5, "the shell keeps its own row budget");
	assert.ok(rows.every(row => row.length <= 40));
});

test("a 40×9 shell keeps the review title and fix action visible", () => {
	const page = new ChangesPage(fakeTui(40, 9), () => snapshot, diffFor);
	page.setViewportRows(8); // one row is reserved for the shell tabs
	page.handleInput("3");
	const lines = page.render(40).map(stripTerminalSequences);
	assert.equal(lines.length, 8);
	assert.match(lines.join("\n"), /Possible API secret/);
	assert.match(lines.join("\n"), /HIGH · src\/parser\.py:4/);
	assert.match(lines.join("\n"), /Remove the value/);
	assert.match(lines.join("\n"), /Fix in Chat/);
	page.handleInput("\x1b[6~");
	assert.match(page.render(40).map(stripTerminalSequences).join("\n"), /rotate it if it was real/);
});

test("diff tab renders the stable diff and file cycling updates the target", () => {
	const withDocs = {
		...snapshot,
		turns: [turn(firstId, "Fix the parser and explain the failure", {
			review,
			files: [
				...turn(firstId, "").files,
				{ path: "docs/parser.md", change: "modified" as const, additions: 0, deletions: 0, binary: false },
			],
		})],
	};
	const page = new ChangesPage(fakeTui(), () => withDocs, diffFor);
	page.render(110);
	assert.equal(page.handleInput("2"), true);
	let rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /\+new/);
	assert.match(rendered, /\+extra/);
	page.render(110);
	assert.equal(page.handleInput("]"), true);
	rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /docs\/parser\.md/);
	assert.match(rendered, /No textual diff recorded for this file/);
});

test("a narrow diff keeps the filename and wraps the tail of a long code line", () => {
	const path = "src/some/deeply/nested/project/directory/name/runner.ts";
	const files = [{ ...snapshot.turns[0]!.files[0]!, path }];
	const data = { ...snapshot, turns: [turn(firstId, "Read the current diff", { files })] };
	const diff = {
		loading: false,
		truncated: false,
		files: [{ ...files[0]!, truncated: false, text: `+${"long_value ".repeat(7)} tail_is_visible\n` }],
	};
	const page = new ChangesPage(fakeTui(48, 16), () => data, () => diff);
	assert.match(page.render(48).map(stripTerminalSequences).join("\n"), /…\/runner\.ts/);
	page.handleInput("2");
	const screen = page.render(48).map(stripTerminalSequences).join("\n");
	assert.match(screen, /…\/runner\.ts/);
	assert.match(screen, /tail_is_visible/);
});

test("up/down selects the next prompt and requests its diff only in Diff", () => {
	const requested: Array<string | undefined> = [];
	const page = new ChangesPage(fakeTui(), () => snapshot, diffFor, {
		onOpenDiff: () => requested.push(page.selectedDiffTurn),
	});
	page.render(110);
	assert.equal(page.handleInput("\x1b[B"), true); // down
	assert.deepEqual(requested, []);
	const rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /Add the missing parser tests/);
	page.handleInput("2");
	page.handleInput("\x1b[A");
	assert.deepEqual(requested, [secondId, firstId]);
	assert.equal(page.handleInput("w"), false, "scope switching is gone: changes belong to this chat");
});

test("narrow terminals keep the selected prompt and its evidence without overflow", () => {
	const page = new ChangesPage(fakeTui(64, 22), () => snapshot, diffFor);
	const lines = page.render(64).map(stripTerminalSequences);
	const rendered = lines.join("\n");
	assert.match(rendered, /Fix the parser/);
	assert.match(rendered, /src\/parser\.py/);
	assert.doesNotMatch(rendered, /Inspector/);
	assert.ok(lines.every(line => line.length <= 64));
});

test("empty state explains that changes belong to the open chat", () => {
	const page = new ChangesPage(fakeTui(), () => ({ ...snapshot, turns: [] }), diffFor);
	const rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /No changes in this chat yet/);
	assert.match(rendered, /prompt in this chat/);
});

test("with no chat there are no changes to show", () => {
	const page = new ChangesPage(fakeTui(), () => ({ ...snapshot, turns: [], hasChat: false }), diffFor);
	const rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /No chat yet/);
	assert.match(rendered, /send a message to start one/);
});
