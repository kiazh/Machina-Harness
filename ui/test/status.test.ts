import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, type TUI } from "@earendil-works/pi-tui";
import { StatusPage, type StatusSnapshot } from "../src/components/StatusPage.ts";

const snapshot: StatusSnapshot = {
	agents: [
		{ id: "agent:main", label: "Main · Build a calculator", detail: "longcat · tool terminal", state: "running", running: true, logs: ["Session: main", "Active tool: terminal", "Third detail", "Fourth detail"] },
		{ id: "agent:reviewer", label: "Reviewer", detail: "longcat", state: "idle" },
	],
	shells: [{ id: "shell:main:job-1", label: "foreground shell", detail: "Build a calculator · pytest", state: "running", running: true, logs: ["Command: pytest", "Output:", "2 passed"] }],
	tools: [{ label: "MCP · mcp_call", detail: "Build a calculator", state: "running", running: true }],
	mcp: [{ label: "docs", detail: "streamable_http", state: "connected", running: true }],
	harnesses: [{ label: "Hermes", detail: "registered", state: "running", running: true }],
	queued: 2,
	approvals: 1,
	updatedAt: "12:00:00",
};

function pageAt(width = 96, rows = 28, opened?: (row: { id?: string }, section: string | undefined) => boolean): StatusPage {
	const tui = {
		terminal: { columns: width, rows },
		requestRender() {},
	} as unknown as TUI;
	return new StatusPage(tui, () => snapshot, opened);
}

test("status page renders agents, shells, tools and harnesses as bounded cards", () => {
	const page = pageAt();
	const rendered = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(rendered, / ACTIVITY/);
	assert.match(rendered, /╭─ Agents · 2/);
	assert.match(rendered, /╰─+╯/);
	assert.match(rendered, /Main · Build a calculator/);
	assert.match(rendered, /Shells · 1/);
	assert.match(rendered, /foreground shell/);
	assert.match(rendered, /MCP · mcp_call/);
	assert.match(rendered, /Harnesses · 1/);
	assert.match(rendered, /Hermes/);
	assert.match(rendered, /queued guidance/);
	assert.ok(page.render(96).every((line) => stripTerminalSequences(line).length <= 96));
});

test("the selected row expands its retained logs inline with Enter", () => {
	const page = pageAt();
	assert.doesNotMatch(page.render(96).map(stripTerminalSequences).join("\n"), /Fourth detail/);
	assert.match(page.render(96).map(stripTerminalSequences).join("\n"), /Session: main/, "the first two log rows preview inline");
	assert.equal(page.handleInput("\r"), true);
	const expanded = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(expanded, /Session: main/);
	assert.match(expanded, /Fourth detail/);
	assert.equal(page.handleInput("\r"), true);
	assert.doesNotMatch(page.render(96).map(stripTerminalSequences).join("\n"), /Fourth detail/);
});

test("Enter opens agent and session rows instead of expanding their logs", () => {
	const opened: Array<[string | undefined, string | undefined]> = [];
	const page = pageAt(96, 28, (row, section) => {
		opened.push([row.id, section]);
		return section === "Agents" || section === "Sessions";
	});
	page.render(96);
	assert.equal(page.handleInput("\r"), true);
	assert.deepEqual(opened, [["agent:main", "Agents"]]);
	// Opening navigates; it must not also expand the inline logs.
	assert.doesNotMatch(page.render(96).map(stripTerminalSequences).join("\n"), /Fourth detail/);
});

test("status rows and filter tabs are clickable", () => {
	const page = pageAt();
	page.render(96);
	const click = (x: number, y: number) => ({
		type: "click", button: "left", x, y, screenX: x, screenY: y,
		width: 96, height: 28, shift: false, alt: false, ctrl: false,
	}) as never;
	const before = page.render(96).map(stripTerminalSequences);
	const reviewerY = before.findIndex(line => line.includes("Reviewer"));
	assert.ok(reviewerY > 0);
	assert.equal(page.handleMouse(click(5, reviewerY))?.handled, true);
	const selected = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(selected, /❯ Reviewer/, "the clicked row becomes current");

	// Click the Tools filter at its rendered position.
	const tabs = page.render(96).map(stripTerminalSequences);
	const tabsY = tabs.findIndex(line => line.includes("TOOLS"));
	const toolsX = tabs[tabsY]!.indexOf("TOOLS") + 2;
	page.handleMouse(click(toolsX, tabsY));
	const filtered = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(filtered, /Tools · 1/);
	assert.doesNotMatch(filtered, /Agents · /);
});

test("status page swaps instantly without a transition frame", () => {
	const page = pageAt(80, 24);
	page.enter(1);
	const rendered = page.render(80).map(stripTerminalSequences).join("\n");
	assert.match(rendered, / ACTIVITY/);
	page.dispose();
});

test("number keys jump between filters", () => {
	const page = pageAt();
	page.render(96);
	assert.equal(page.handleInput("5"), true);
	const filtered = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(filtered, /Tools · 1/);
	assert.doesNotMatch(filtered, /Shells · /);
	assert.equal(page.handleInput("9"), false);
});

test("expanded logs page with PgUp/PgDn instead of clipping", () => {
	const long = Array.from({ length: 30 }, (_, index) => `log line ${index + 1}`);
	const page = new StatusPage(
		{ terminal: { columns: 96, rows: 24 }, requestRender() {} } as unknown as TUI,
		() => ({ ...snapshot, agents: [{ id: "agent:main", label: "Main", state: "running", running: true, logs: long }] }),
	);
	page.render(96);
	page.handleInput("\r");
	const first = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(first, /↓ \d+ later · PgDn/);
	assert.match(first, /log line 1/);
	page.handleInput("\x1b[6~");
	const second = page.render(96).map(stripTerminalSequences).join("\n");
	assert.notEqual(second, first, "PgDn moves the retained-log window");
	assert.match(second, /↑ \d+ earlier · PgUp/);
	page.handleInput("\x1b[5~");
	assert.match(page.render(96).map(stripTerminalSequences).join("\n"), /log line 1/, "PgUp returns to the start");
});

test("narrow terminals keep the selected detail inline and inside the width", () => {
	const page = pageAt(64, 22);
	const rendered = page.render(64).map(stripTerminalSequences);
	const screen = rendered.join("\n");
	assert.match(screen, /Main · Build a calculator/);
	assert.match(screen, /Session: main/);
	assert.doesNotMatch(screen, /Inspector/);
	assert.ok(rendered.every((line) => line.length <= 64));
});
