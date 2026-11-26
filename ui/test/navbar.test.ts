import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences } from "@earendil-works/pi-tui";
import { NavBar, type NavSnapshot } from "../src/components/NavBar.ts";

const snapshot = (overrides: Partial<NavSnapshot> = {}): NavSnapshot => ({
	view: "chat",
	title: "Overhaul the terminal UI",
	project: "machina",
	branch: "main",
	model: "qwen/qwen4-max",
	status: "ready",
	activityCount: 2,
	changeCount: 3,
	...overrides,
});

function bar(snap: NavSnapshot, rows = 24): NavBar {
	return new NavBar(() => snap, () => {}, () => rows);
}

test("command strip carries brand, tabs, session context and state", () => {
	const rows = bar(snapshot()).render(120).map(stripTerminalSequences);
	assert.match(rows[0] ?? "", /MACHINA/);
	assert.match(rows[0] ?? "", /Chat/);
	assert.match(rows[0] ?? "", /Activity 2/);
	assert.match(rows[0] ?? "", /Changes 3/);
	assert.match(rows[0] ?? "", /Overhaul the terminal UI/);
	assert.match(rows[0] ?? "", /machina · main/);
	assert.match(rows[0] ?? "", /qwen\/qwen4-max/);
	assert.match(rows[0] ?? "", /ready/);
	assert.match(rows[1] ?? "", /━+/);
	assert.ok(rows[1]!.indexOf("━") >= (rows[0]!.indexOf("Chat")), "the bright segment sits under the active tab");
});

test("command strip follows the active view and collapses to one row", () => {
	const activity = bar(snapshot({ view: "activity" }), 9).render(80).map(stripTerminalSequences);
	assert.equal(activity.length, 1);
	assert.match(activity[0] ?? "", /Activity/);
	assert.doesNotMatch(activity[0] ?? "", /━/);

	const changes = bar(snapshot({ view: "changes" })).render(80).map(stripTerminalSequences);
	assert.match(changes[1] ?? "", /━/);
	assert.ok(changes[1]!.indexOf("━") >= changes[0]!.indexOf("Changes"));
});

test("clicking a tab selects that screen", () => {
	const selected: string[] = [];
	const nav = new NavBar(() => snapshot(), (view) => selected.push(view), () => 24);
	const rows = nav.render(120).map(stripTerminalSequences);
	const y = rows[0]!;
	const x = y.indexOf("Activity");
	const result = nav.handleMouse({
		type: "click", button: "left", x: x + 1, y: 0, screenX: x + 1, screenY: 0,
		width: 120, height: 24, shift: false, alt: false, ctrl: false,
	} as never);
	assert.equal(result?.handled, true);
	assert.deepEqual(selected, ["activity"]);
});

test("narrow command strips keep every tab and inside the width", () => {
	for (const width of [40, 60, 80, 160]) {
		const rows = bar(snapshot()).render(width);
		const line = stripTerminalSequences(rows[0]!);
		assert.ok(rows.every(row => stripTerminalSequences(row).length <= width));
		assert.match(line, /Chat/);
		if (width < 64) {
			assert.match(line, /Act/);
			assert.match(line, /Chg/);
		} else {
			assert.match(line, /Activity/);
			assert.match(line, /Changes/);
		}
	}
});
