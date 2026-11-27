// Deterministic UI characterization harness (R0.3).
//
// Each scenario replays a fixed gateway event script through the reference
// fixture, renders the same state across the width/row matrix, and compares
// normalized frames against a checked-in golden. Reduced motion is canonical:
// animation frames and the caret blink never reach a snapshot.
//
// Regenerate after an intentional, reviewed visual change:
//   UPDATE_GOLDEN=1 node --test test/golden-ui.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, visibleWidth } from "@earendil-works/pi-tui";
import type { ReviewFindingInfo, ReviewRunInfo } from "../src/protocol.ts";
import { referenceFixture, type ReferenceOptions } from "./support/reference.ts";
import {
	GOLDEN_SIZES,
	assertFrameInvariants,
	lineWith,
	normalizeFrame,
	readGolden,
	selectionLine,
	updatingGolden,
	writeGolden,
} from "./support/golden.ts";

// Pin the canonical animation frame before any App (and its coordinator) is
// constructed. Test files run in their own process, so this never leaks.
process.env.MACHINA_UI_REDUCED_MOTION = "1";

const SESSION = "reference-session";
const TURN_A = "a".repeat(32);
const TURN_B = "b".repeat(32);
const FINDING: ReviewFindingInfo = {
	rule: "hardcoded-secret",
	severity: "high",
	title: "Possible API secret key added",
	body: "Remove the value from source and rotate it if it was real.",
	file: "src/parser.py",
	line: 4,
	fingerprint: "abc123",
};
const SECOND_FINDING: ReviewFindingInfo = {
	...FINDING,
	title: "Check the boundary",
	body: "The parser accepts an empty queue at the boundary.",
	fingerprint: "boundary",
};
const REVIEW: ReviewRunInfo = {
	id: "review-1",
	snapshotId: TURN_A,
	sessionId: SESSION,
	engine: "rules",
	status: "complete",
	provider: "fixture",
	model: "preview",
	createdAt: "2026-11-25T12:00:00Z",
	startedAt: "2026-11-25T12:00:00Z",
	finishedAt: "2026-11-25T12:00:01Z",
	summary: "2 findings: 2 high",
	findings: [FINDING, SECOND_FINDING],
	findingsCount: 2,
	error: null,
};

const CHANGES_RESPONSES: NonNullable<ReferenceOptions["responses"]> = {
	"changes.list": {
		turns: [
			{
				id: TURN_A,
				sessionId: SESSION,
				prompt: "Fix the parser and explain the failure",
				createdAt: "2026-11-25T12:00:00Z",
				provider: "fixture",
				model: "preview",
				status: "complete",
				stats: { files: 2, additions: 13, deletions: 4 },
				files: [
					{ path: "src/parser.py", change: "modified", additions: 12, deletions: 3, binary: false },
					{ path: "docs/parser.md", change: "modified", additions: 1, deletions: 1, binary: false },
				],
				review: REVIEW,
			},
			{
				id: TURN_B,
				sessionId: SESSION,
				prompt: "Add the missing parser tests",
				createdAt: "2026-11-25T12:05:00Z",
				provider: "fixture",
				model: "preview",
				status: "complete",
				stats: { files: 1, additions: 30, deletions: 0 },
				files: [
					{ path: "tests/test_parser.py", change: "added", additions: 30, deletions: 0, binary: false },
				],
				review: null,
			},
		],
	},
	"changes.diff": (params: Record<string, unknown>) => {
		if (params.snapshotId === TURN_B) {
			return {
				files: [{
					path: "tests/test_parser.py",
					change: "added",
					binary: false,
					truncated: false,
					text: "--- /dev/null\n+++ b/tests/test_parser.py\n@@ -0,0 +1 @@\n+new test\n",
				}],
				truncated: false,
			};
		}
		if (params.path === "docs/parser.md") {
			return {
				files: [{
					path: "docs/parser.md",
					change: "modified",
					binary: false,
					truncated: false,
					text: "--- a/docs/parser.md\n+++ b/docs/parser.md\n@@ -1 +1 @@\n-old docs\n+new docs\n",
				}],
				truncated: false,
			};
		}
		return {
			files: [
				{
					path: "src/parser.py",
					change: "modified",
					binary: false,
					truncated: false,
					text: "--- a/src/parser.py\n+++ b/src/parser.py\n@@ -1,2 +1,3 @@\n-old\n+new\n+extra\n",
				},
				{
					path: "docs/parser.md",
					change: "modified",
					binary: false,
					truncated: false,
					text: "--- a/docs/parser.md\n+++ b/docs/parser.md\n@@ -1 +1 @@\n-old docs\n+new docs\n",
				},
			],
			truncated: false,
		};
	},
};

const ACTIVITY_RESPONSES: NonNullable<ReferenceOptions["responses"]> = {
	"interop.status": {
		platforms: [{
			platform: "hermes",
			label: "Hermes",
			installed: true,
			running: true,
			version: "1.2.3",
			scope: "global",
			notes: ["registered"],
		}],
	},
	"mcp.list": {
		servers: [{
			id: "docs",
			display_name: "docs",
			transport: "streamable_http",
			state: "connected",
			server_name: "docs",
			server_version: "1.0.0",
			protocol_version: "2025-06-18",
		}],
	},
	"prompt.steering": { items: [] },
	"shell.jobs": {
		jobs: [{
			id: "job-1",
			command: "npm run check",
			status: "exited",
			returncode: 0,
			output: "1282 tests passed\ncheck clean",
			foreground: true,
		}],
	},
};

type Fixture = Awaited<ReturnType<typeof referenceFixture>>;

interface Scenario {
	name: string;
	options: ReferenceOptions;
	setup?: (fixture: Fixture) => Promise<void>;
}

const SCENARIOS: Scenario[] = [
	{
		name: "chat",
		options: { approval: false },
	},
	{
		name: "approval",
		options: {},
	},
	{
		name: "activity",
		options: { approval: false, responses: ACTIVITY_RESPONSES },
		setup: async (fixture) => {
			fixture.type("\x1b[1;3D"); // Alt+Left: Activity
			await fixture.settle();
		},
	},
	{
		name: "changes",
		options: { approval: false, responses: CHANGES_RESPONSES },
		setup: async (fixture) => {
			fixture.type("\x1b[1;3C"); // Alt+Right: Changes
			await fixture.settle();
		},
	},
];

function frame(fixture: Fixture, columns: number, rows: number, label: string): string[] {
	fixture.resize(columns, rows);
	const raw = fixture.tui.getScreenLines();
	assertFrameInvariants(label, raw, columns, rows);
	return normalizeFrame(raw.map(stripTerminalSequences));
}

test("the Chat/Activity/Changes/approval frame matrix is byte-stable", async () => {
	const frames: Record<string, string[]> = {};
	for (const scenario of SCENARIOS) {
		const fixture = await referenceFixture(120, 40, scenario.options);
		try {
			await scenario.setup?.(fixture);
			for (const size of GOLDEN_SIZES) {
				const label = `${scenario.name}-${size.columns}x${size.rows}`;
				frames[label] = frame(fixture, size.columns, size.rows, label);
				// A second render of the same state is the canonical frame too:
				// no animation phase, cursor blink, or timer may leak into it.
				assert.deepEqual(
					frame(fixture, size.columns, size.rows, `${label} (replay)`),
					frames[label],
					`${label}: a replay must render the same frame`,
				);
			}
		} finally {
			await fixture.app.exit();
		}
	}

	const text = `${JSON.stringify(frames, null, "\t")}\n`;
	if (updatingGolden()) writeGolden("screens.json", text);
	assert.deepEqual(frames, JSON.parse(readGolden("screens.json")), "golden screen matrix changed");
});

test("Activity and Changes key traces keep the same screen, selection and draft", async () => {
	const traces: Record<string, Array<Record<string, unknown>>> = {};

	const activity = await referenceFixture(120, 40, { approval: false, responses: ACTIVITY_RESPONSES });
	try {
		activity.type("unsent draft");
		activity.tui.renderNow(true);
		const steps: Array<Record<string, unknown>> = [];
		const capture = (step: string, evidence: string): void => {
			activity.tui.renderNow(true);
			const lines = normalizeFrame(activity.plain());
			steps.push({
				step,
				view: (activity.app as unknown as { view: string }).view,
				selection: selectionLine(lines),
				evidence: lineWith(lines, evidence),
			});
		};
		activity.type("\x1b[1;3D"); // Alt+Left
		await activity.settle();
		capture("activity", "Recent work");
		activity.type("4"); // Shells
		capture("shells", "npm run check");
		activity.type(" "); // expand retained output
		capture("expanded", "Return code: 0");
		activity.type("\x1b[B"); // down
		capture("down", "npm run check");
		activity.type("\x1b");
		capture("chat", "unsent draft");
		traces.activity = steps;
	} finally {
		await activity.app.exit();
	}

	const changes = await referenceFixture(120, 40, { approval: false, responses: CHANGES_RESPONSES });
	try {
		changes.type("unsent draft");
		changes.type("\x1b[1;3C"); // Alt+Right
		await changes.settle();
		const steps: Array<Record<string, unknown>> = [];
		const capture = (step: string, evidence: string): void => {
			changes.tui.renderNow(true);
			const lines = normalizeFrame(changes.plain());
			steps.push({
				step,
				view: (changes.app as unknown as { view: string }).view,
				selection: selectionLine(lines),
				evidence: lineWith(lines, evidence),
			});
		};
		capture("changes", "Changes in this chat");
		changes.type("2"); // Diff
		await changes.settle();
		capture("diff", "+extra");
		changes.type("]"); // next file
		await changes.settle();
		capture("next-file", "+new docs");
		changes.type("3"); // Review
		capture("review", "Possible API secret");
		changes.type("n"); // next finding
		capture("next-finding", "Check the boundary");
		changes.type("\x1b");
		capture("chat", "unsent draft");
		traces.changes = steps;
	} finally {
		await changes.app.exit();
	}

	const text = `${JSON.stringify(traces, null, "\t")}\n`;
	if (updatingGolden()) writeGolden("keys.json", text);
	assert.deepEqual(traces, JSON.parse(readGolden("keys.json")), "golden key trace changed");
});

test("an approval decision is a deterministic key trace", async () => {
	const fixture = await referenceFixture(120, 40);
	try {
		const steps: Array<Record<string, unknown>> = [];
		const decisions = (): unknown[] => fixture.requests
			.filter(request => request.method === "approvals.resolve")
			.map(request => request.params);
		const capture = (step: string, evidence: string): void => {
			fixture.tui.renderNow(true);
			const lines = normalizeFrame(fixture.plain());
			steps.push({
				step,
				view: (fixture.app as unknown as { view: string }).view,
				selection: selectionLine(lines),
				evidence: lineWith(lines, evidence),
				decisions: decisions(),
			});
		};
		capture("waiting", "request: demo-7f2a");
		fixture.type("\x1b[B");
		capture("session", "Allow for this session");
		fixture.type("\r");
		await fixture.settle();
		capture("decided", "Queue a message");
		assert.deepEqual(decisions(), [{
			requestId: "demo-7f2a",
			sessionId: SESSION,
			verdict: "approved",
			grant: "session",
		}]);

		const text = `${JSON.stringify(steps, null, "\t")}\n`;
		if (updatingGolden()) writeGolden("approval.json", text);
		assert.deepEqual(steps, JSON.parse(readGolden("approval.json")), "golden approval trace changed");
	} finally {
		await fixture.app.exit();
	}
});

test("the frame invariant rejects an injected extra row or an over-wide row", () => {
	const columns = 40;
	const rows = 8;
	assert.doesNotThrow(() => assertFrameInvariants("clean", Array.from({ length: rows }, () => "x"), columns, rows));
	assert.throws(
		() => assertFrameInvariants("extra-row", Array.from({ length: rows + 1 }, () => "x"), columns, rows),
		/expected 8 rendered rows/,
	);
	assert.throws(
		() => assertFrameInvariants("wide", [...Array.from({ length: rows - 1 }, () => "x"), "x".repeat(columns + 1)], columns, rows),
		/limit 40/,
	);
	assert.ok(visibleWidth("x".repeat(columns)) <= columns);
});
