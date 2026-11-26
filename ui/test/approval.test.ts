// Exact approval review (A01/A02/A05): evidence rendering, one-time default
// decisions, and control-sequence safety. App-level binding tests live in
// state.test.ts through the production event flow.

import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, visibleWidth, type TUI } from "@earendil-works/pi-tui";
import { ApprovalCard, type ApprovalDecision, type ApprovalDetails } from "../src/components/ApprovalCard.ts";
import { isOpaqueBackground, setOpaqueBackground } from "../src/theme.ts";

function details(overrides: Partial<ApprovalDetails> = {}): ApprovalDetails {
	return {
		requestId: "req-abcdef01",
		sessionId: "session-a",
		originTurnId: "turn-a",
		transportTurnId: "turn-a",
		operation: "terminal",
		tool: "terminal",
		target: "host",
		argv: ["pytest", "tests/test_installer_env.py", "-q"],
		cwd: "/repo",
		backend: "host",
		timeout: 120,
		capabilities: ["exec"],
		...overrides,
	};
}

function plain(card: ApprovalCard, width = 80): string {
	return card.render(width).map((line) => stripTerminalSequences(line)).join("\n");
}

test("hostile approval fields never add rows, split escapes, or overflow", () => {
	const previous = isOpaqueBackground();
	try {
		for (const opaque of [true, false]) {
			setOpaqueBackground(opaque);
			const card = new ApprovalCard(details({
				requestId: "\x1b[7m sel \x1b[0m",
				target: "name\nsecond line",
				capabilities: ["exec", "write\nfile"],
				contentPreview: "line one\nline two",
				fileDiff: { diff: "+added\n-removed", truncated: true },
			}), { docked: true });
			card.setError("failed\nagain");
			card.setRecorded("recorded\nstatus");
			for (const width of [1, 2, 3, 9, 20, 80]) {
				for (const row of [...card.render(width), ...card.evidence.render(width)]) {
					assert.doesNotMatch(row, /[\r\n]/, `width ${width} row must be one line`);
					assert.ok(visibleWidth(row) <= width, `width ${width}: ${visibleWidth(row)}`);
					// eslint-disable-next-line no-control-regex -- proving no split escape survives is the point.
					assert.doesNotMatch(row, /\x1b(?!\[[0-9;]*m)/, "no dangling escape byte survives a truncation");
				}
			}
		}
	} finally {
		setOpaqueBackground(previous);
	}
});

const stubTui = (rows: number, columns = 80): TUI =>
	({ terminal: { rows, columns } }) as unknown as TUI;

test("A01 approval evidence shows exact boundaries and backend fields", () => {
	const decisions: ApprovalDecision[] = [];
	const card = new ApprovalCard(
		details({
			argv: ["ls", "-la", "--all", "a b"],
			network: ["example.com"],
			contentDigest: "sha256:abcdef0123456789",
			contentLength: 42,
			contentPreview: "hello\nworld",
			fileDiff: { diff: "@@ -1 +1 @@\n-old\n+new", truncated: true },
			durable: false,
		}),
		{ onDecision: (d) => void decisions.push(d) },
	);
	const out = plain(card);
	assert.match(out, /Approval required/);
	assert.match(out, /argv: "ls" "-la" "--all" "a b"/, "argument boundaries preserved exactly");
	assert.match(out, /Session: session-.*turn: turn-a.*request: req-abcd/);
	assert.match(out, /cwd: \/repo.*backend: host/);
	assert.match(out, /Timeout: 120s/);
	assert.match(out, /Capabilities: exec/);
	assert.match(out, /Network: example\.com/);
	assert.match(out, /sha256:abcdef0123456789.*\(42 chars\)/);
	assert.match(out, /Preview:/);
	assert.match(out, /❯ Allow once/, "the initial choice grants only this request");
	assert.match(out, /this operation only/, "session grants are scoped, never all tools");
	for (const line of card.render(80)) assert.ok(visibleWidth(line) <= 80);
	card.handleInput("\x1b[6~");
	card.handleInput("\x1b[6~");
	const scrolled = plain(card);
	assert.match(scrolled, /Local-only approval/);
	assert.match(scrolled, /diff truncated/);
});

test("A01 absent timeout reads unspecified, never an invented default", () => {
	const card = new ApprovalCard(details({ timeout: undefined }));
	assert.match(plain(card), /Timeout: unspecified/);
	assert.doesNotMatch(plain(card), /Timeout: 60s/);
});

test("A02 default Enter allows once; a session grant needs deliberate selection", () => {
	const decisions: ApprovalDecision[] = [];
	const card = new ApprovalCard(details(), { onDecision: (d) => void decisions.push(d) });
	card.render(80);
	card.handleInput("\r");
	assert.deepEqual(decisions, [{ verdict: "approved", grant: "once" }]);

	const allow: ApprovalDecision[] = [];
	const card2 = new ApprovalCard(details(), { onDecision: (d) => void allow.push(d) });
	card2.render(80);
	card2.handleInput("\x1b[B");
	assert.match(plain(card2), /❯ Allow for this session/);
	card2.handleInput("\r");
	assert.deepEqual(allow, [{ verdict: "approved", grant: "session" }]);
});

test("A02 duplicate Enter while resolving sends one request", () => {
	const decisions: ApprovalDecision[] = [];
	const card = new ApprovalCard(details(), { onDecision: (d) => void decisions.push(d) });
	card.render(80);
	card.setResolving(true);
	card.handleInput("\r");
	card.handleInput("\r");
	assert.deepEqual(decisions, [], "no decision escapes while resolving");
});

test("A02 clipped terminals refuse Allow but keep Deny safe", () => {
	const decisions: ApprovalDecision[] = [];
	const card = new ApprovalCard(details(), { tui: stubTui(8, 40), onDecision: (d) => void decisions.push(d) });
	card.render(40);
	assert.equal(card.isAllowBlocked(), true);
	assert.match(plain(card, 40), /Allow blocked/);
	assert.match(plain(card, 40), /❯ Allow once/);
	card.handleInput("\r");
	assert.deepEqual(decisions, [], "unseen actions are never approved");
	card.handleInput("\x1b[B");
	card.handleInput("\x1b[B");
	card.handleInput("\r");
	assert.deepEqual(decisions, [{ verdict: "denied", grant: "once" }], "denial stays available without review");
});

test("A05 control sequences in approval text never escape or forge", () => {
	const card = new ApprovalCard(
		details({
			argv: ["ls", "\x1b[2J\x1b[1;1Hclean", "\x1b]0;title\x07click"],
			cwd: "/tmp\x1b]52;c;zzz\x07",
			contentPreview: "ok\x1b[2K\rhidden",
			fileDiff: { diff: "+shown\x1b[38;5;1m" },
		}),
	);
	const frames = card.render(80);
	const raw = frames.join("\n");
	assert.ok(!raw.includes("\x1b]"), "no OSC/title/clipboard sequences");
	assert.ok(!raw.includes("[2J") && !raw.includes("[1;1H") && !raw.includes("[2K"), "no cursor/clear effects");
	assert.ok(!raw.includes("\r"), "no carriage returns");
	const out = plain(card);
	assert.match(out, /clean/);
	assert.match(out, /click/);
	assert.match(out, /shown/);
});

test("A01 long evidence scrolls with decision controls visible", () => {
	const card = new ApprovalCard(
		details({
			argv: Array.from({ length: 20 }, (_, i) => `arg-${i}`),
			contentPreview: Array.from({ length: 20 }, (_, i) => `preview ${i}`).join("\n"),
			fileDiff: { diff: Array.from({ length: 30 }, (_, i) => ` line ${i}`).join("\n") },
		}),
		{ tui: stubTui(24) },
	);
	const first = plain(card);
	assert.match(first, /more evidence rows \(pgup\/pgdn\)/);
	assert.match(first, /❯ Allow once/, "decision controls stay visible while evidence overflows");
	card.handleInput("\x1b[6~");
	const second = plain(card);
	assert.notEqual(second, first, "details scroll");
	for (const line of card.render(80)) assert.ok(visibleWidth(line) <= 80);
});
