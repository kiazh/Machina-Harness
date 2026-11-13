import assert from "node:assert/strict";
import test from "node:test";
import { stripTerminalSequences, visibleWidth } from "@earendil-works/pi-tui";
import { ActivityBar, ActivityStore, activityEmptyGuidance } from "../src/activity.ts";

test("command action controls wrap and each mouse target invokes its own action", () => {
 const clicked: string[] = [];
 const bar = new ActivityBar(() => "1 shell running", () => clicked.push("details"), () => ["Background", "Output", "Stop"].map(label => ({ label, run: () => clicked.push(label) })));
 const rows = bar.render(22).map(stripTerminalSequences);
 assert.match(rows.join("\n"), /\[Background\]/);
 assert.match(rows.join("\n"), /\[Output\]/);
 assert.match(rows.join("\n"), /\[Stop\]/);
 assert.ok(rows.every(row => visibleWidth(row) <= 22));
 for (const label of ["Background", "Output", "Stop"]) {
  const y = rows.findIndex(row => row.includes(`[${label}]`));
  const x = rows[y]!.indexOf(`[${label}]`) + 1;
  bar.handleMouse({ type: "click", button: "left", x, y, width: 22 } as Parameters<ActivityBar["handleMouse"]>[0]);
 }
 assert.deepEqual(clicked, ["Background", "Output", "Stop"]);
});

test("activity empty guidance explains how to create activity", () => {
	const empty = activityEmptyGuidance(true);
	assert.match(empty, /Activity is empty/);
	assert.match(empty, /Press Enter while a reply is running/);
	assert.match(empty, /long-running command/);
	assert.match(empty, /\/delegate <task>/);
	assert.match(empty, /Ctrl\+N then A/);

	const noSession = activityEmptyGuidance(false);
	assert.match(noSession, /No active session/);
	assert.match(noSession, /Send a message first/);
	assert.doesNotMatch(noSession, /Activity is empty/);
});

test("activity stores queued prompts and command status by session", () => {
	const store = new ActivityStore();
	store.setQueue("main", [{ id: "q", text: "steer" }]);
	store.setJob("main", { id: "j", command: "pytest", status: "running" });
	store.setJob("main", { id: "j", command: "pytest", status: "exited", returncode: 0 });
	assert.equal(store.queue("other").length, 0);
	assert.equal(store.listJobs("main").length, 1);
	assert.equal(store.listJobs("main")[0]?.status, "exited");
	store.setJob("main", { id: "j", command: "pytest", status: "running" });
	assert.equal(store.listJobs("main")[0]?.status, "exited", "a stale list response cannot revive a finished job");
});

test("activity row collapses when empty and stays inside narrow terminal widths", () => {
	let summary = "";
	const bar = new ActivityBar(() => summary, () => {});
	assert.equal(bar.visible, false);
	assert.deepEqual(bar.render(80), []);
	summary = "2 queued · 1 shell running";
	assert.equal(bar.visible, true);
	for (const width of [1, 20, 80, 160]) {
		assert.ok(bar.render(width).every((row) => visibleWidth(row) <= width));
	}
	assert.match(bar.render(80).map(stripTerminalSequences).join(""), /2 queued/);
});
