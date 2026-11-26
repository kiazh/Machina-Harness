import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, type TUI } from "@earendil-works/pi-tui";
import { StatusPage, type StatusSnapshot } from "../src/components/StatusPage.ts";
import { theme } from "../src/theme.ts";

const snapshot: StatusSnapshot = {
	agents: [
		{ id: "agent:main", label: "Main · Build a calculator", detail: "longcat · tool terminal", state: "running", logs: ["Session: main", "Active tool: terminal", "Third detail", "Fourth detail"] },
		{ id: "agent:reviewer", label: "Reviewer", detail: "longcat", state: "idle" },
	],
	shells: [{ id: "shell:main:job-1", label: "foreground shell", detail: "Build a calculator · pytest", state: "running", logs: ["Command: pytest", "Output:", "2 passed"] }],
	tools: [{ label: "MCP · mcp_call", detail: "Build a calculator", state: "running" }],
	mcp: [{ label: "docs", detail: "streamable_http", state: "connected" }],
	harnesses: [{ label: "Hermes", detail: "registered", state: "running" }],
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

test("status keeps a work index next to the selected evidence on wide terminals", () => {
	const page = pageAt();
	const rendered = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /Recent work/);
	assert.match(rendered, /Agents\s+│ Main · Build a calculator/);
	assert.match(rendered, /foreground shell.+running/);
	assert.doesNotMatch(rendered, /╭|╰/);
	assert.match(rendered, /Main · Build a calculator/);
	assert.match(rendered, /Shells\s+│/);
	assert.match(rendered, /foreground shell/);
	assert.match(rendered, /MCP · mcp_call/);
	assert.match(rendered, /Harnesses\s+│/);
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
	const tabsY = tabs.findIndex(line => line.includes("5 Tools"));
	const toolsX = tabs[tabsY]!.indexOf("5 Tools") + 2;
	page.handleMouse(click(toolsX, tabsY));
	const filtered = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(filtered, /Tools\s+│/);
	assert.doesNotMatch(filtered, /Agents\s+│/);
});

test("number keys jump between filters", () => {
	const page = pageAt();
	page.render(96);
	assert.equal(page.handleInput("5"), true);
	const filtered = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(filtered, /Tools\s+│/);
	assert.doesNotMatch(filtered, /Shells\s+│/);
	assert.equal(page.handleInput("9"), false);
});

test("expanded logs page with PgUp/PgDn instead of clipping", () => {
	const long = Array.from({ length: 30 }, (_, index) => `log line ${index + 1}`);
	const page = new StatusPage(
		{ terminal: { columns: 96, rows: 24 }, requestRender() {} } as unknown as TUI,
		() => ({ ...snapshot, agents: [{ id: "agent:main", label: "Main", state: "running", logs: long }] }),
	);
	page.render(96);
	page.handleInput("\r");
	const first = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(first, /1–\d+ of 30 lines · PgUp\/PgDn/);
	assert.match(first, /log line 1/);
	page.handleInput("\x1b[6~");
	const second = page.render(96).map(stripTerminalSequences).join("\n");
	assert.notEqual(second, first, "PgDn moves the retained-log window");
	assert.match(second, /of 30 lines · PgUp\/PgDn/);
	assert.match(second, /log line 30/);
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

test("a selected shell shows actual output without expanding or moving selection", () => {
	const page = pageAt(80, 24);
	page.handleInput("4");
	const text = () => page.render(80).map(stripTerminalSequences).join("\n");
	assert.match(text(), /Output preview/);
	assert.match(text(), /Command: pytest/);
	assert.match(text(), /2 passed/);
	page.handleInput("\x1b[6~");
	assert.match(text(), /❯ foreground shell/);
	assert.match(text(), /Retained output/);
});

test("preview rendering does not inspect retained output or format offscreen rows", () => {
	let reads = 0;
	const logs = ["metadata"];
	Object.defineProperty(logs, 0, { get() { reads++; return "metadata"; } });
	const shells = [{ id: "visible", label: "build", logs, preview: ["build output"] },
		...Array.from({ length: 50 }, (_, i) => ({ id: `job:${i}`, label: `job ${i}` })),
		{ id: "offscreen", get label() { reads++; return "offscreen"; }, get preview() { reads++; return ["hidden"]; } }];
	const page = new StatusPage({ terminal: { columns: 96, rows: 24 }, requestRender() {} } as unknown as TUI,
		() => ({ ...snapshot, shells }));
	page.handleInput("4");
	assert.ok(page.render(96).map(stripTerminalSequences).some(line => line.includes("build output")));
	assert.equal(reads, 0);
});

test("the wheel moves the Activity selection and reads the selected output", () => {
	const page = pageAt(96, 24);
	page.handleInput("4"); // Shells
	const text = () => page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(text(), /❯ foreground shell/);
	const wheel = (x: number, y: number, wheelDelta: number) => ({
		type: "wheel", button: "none", x, y, wheelDelta, screenX: x, screenY: y,
		width: 96, height: 24, shift: false, alt: false, ctrl: false,
	}) as never;
	// Detail pane (right of the divider): scroll expands the output in place.
	page.handleMouse(wheel(80, 8, 1));
	assert.match(text(), /Retained output/);
	assert.match(text(), /2 passed/);
	// Back to the list: the wheel changes the selection.
	page.handleInput("1");
	page.render(96);
	const reviewerY = page.render(96).map(stripTerminalSequences).findIndex(line => line.includes("Reviewer"));
	page.handleMouse(wheel(6, reviewerY, 1));
	assert.match(text(), /❯ Reviewer/);
});

test("PgUp on a collapsed log row moves the selection instead of doing nothing", () => {
	const page = pageAt(96, 24);
	page.handleInput("4"); // Shells: the only row has logs and starts collapsed
	assert.match(page.render(96).map(stripTerminalSequences).join("\n"), /❯ foreground shell/);
	page.handleInput("1"); // All: agents first, the shell further down
	page.handleInput("\x1b[B");
	page.handleInput("\x1b[B");
	assert.match(page.render(96).map(stripTerminalSequences).join("\n"), /❯ foreground shell/);
	page.handleInput("\x1b[5~"); // PgUp must page the list, not expand silently
	assert.match(page.render(96).map(stripTerminalSequences).join("\n"), /❯ Main/);
});

test("a short viewport keeps the page inside its row budget", () => {
	const page = pageAt(40, 5);
	page.setViewportRows(5);
	assert.equal(page.render(40).length, 5);
	assert.ok(page.render(40).every(row => stripTerminalSequences(row).length <= 40));
});

test("selectDefault resets the retained-output window with the selection", () => {
	const long = Array.from({ length: 30 }, (_, index) => `line ${index + 1}`);
	const page = new StatusPage(
		{ terminal: { columns: 96, rows: 24 }, requestRender() {} } as unknown as TUI,
		() => ({ ...snapshot, agents: [{ id: "agent:main", label: "Main", state: "running", logs: long }] }),
	);
	const text = () => page.render(96).map(stripTerminalSequences).join("\n");
	page.render(96);
	page.handleInput("\r");
	page.render(96);
	page.handleInput("\x1b[6~");
	assert.match(text(), /line 30/);
	page.selectDefault();
	assert.match(text(), /line 1/);
	assert.doesNotMatch(text(), /line 30/);
});

test("disconnected is an error, not a successful connection", () => {
	const tui = { terminal: { columns: 100, rows: 24 }, requestRender() {} } as unknown as TUI;
	const page = new StatusPage(tui, () => ({ ...snapshot, mcp: [{ label: "docs", state: "disconnected" }] }));
	page.handleInput("6");
	const detail = page.render(100).find(line => stripTerminalSequences(line).split("│").at(-1)?.trim() === "disconnected");
	assert.ok(detail?.includes(theme.error("disconnected")));
});
