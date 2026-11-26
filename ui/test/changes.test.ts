import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, type TUI } from "@earendil-works/pi-tui";
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
	scope: "session",
	project: "machina",
	updatedAt: "12:00:01",
	running: false,
};

function diffFor(turnId: string | undefined): ChangesDiffState {
	if (turnId === firstId) {
		return {
			turnId,
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
		turnId,
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
	assert.match(rendered, /MACHINA\s{2}CHANGES/);
	assert.match(rendered, /01 ❯/);
	assert.match(rendered, /Fix the parser and explain the failure/);
	assert.match(rendered, /src\/parser\.py/);
	assert.match(rendered, /\+12/);
	assert.match(rendered, /−3/);
	assert.match(rendered, /review 1/);
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
	assert.match(rendered, /Enter \/ a/);
	assert.equal(page.handleInput("r"), true);
	assert.deepEqual(started, [firstId]);
});

test("Enter on a selected finding opens the fix action for that finding", () => {
	const fixes: Array<[string, string]> = [];
	const page = new ChangesPage(fakeTui(), () => snapshot, diffFor, {
		onFixFinding: (turnId, finding) => fixes.push([turnId, finding.rule]),
	});
	page.render(110);
	page.handleInput("3");
	assert.equal(page.handleInput("\r"), true);
	assert.deepEqual(fixes, [[firstId, "hardcoded-secret"]]);
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

test("up/down selects the next prompt and reports it, and w toggles scope", () => {
	const selected: string[] = [];
	let scopes = 0;
	const page = new ChangesPage(fakeTui(), () => snapshot, diffFor, {
		onSelectTurn: (id) => selected.push(id),
		onToggleScope: () => { scopes++; },
	});
	page.render(110);
	assert.equal(page.handleInput("\x1b[B"), true); // down
	assert.deepEqual(selected, [secondId]);
	const rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /Add the missing parser tests/);
	assert.equal(page.handleInput("w"), true);
	assert.equal(scopes, 1);
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

test("empty state explains how prompt changes appear", () => {
	const page = new ChangesPage(fakeTui(), () => ({ ...snapshot, turns: [] }), diffFor);
	const rendered = page.render(110).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /No prompt changes recorded yet/);
	assert.match(rendered, /Each completed agent turn/);
});
