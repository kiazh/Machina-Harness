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

test("status page renders agents, shells, MCP calls and harnesses", () => {
	const tui = {
		terminal: { columns: 96, rows: 28 },
		requestRender() {},
	} as unknown as TUI;
	const page = new StatusPage(tui, () => snapshot);
	const rendered = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /Machina · Activity/);
	assert.match(rendered, /▏AGENTS/);
	assert.match(rendered, /Main · Build a calculator/);
	assert.match(rendered, /▏SHELLS/);
	assert.match(rendered, /foreground shell/);
	assert.match(rendered, /MCP · mcp_call/);
	assert.match(rendered, /▏HARNESSES/);
	assert.match(rendered, /Hermes/);
	assert.match(rendered, /queued guidance/);
	assert.ok(page.render(96).every((line) => stripTerminalSequences(line).length <= 96));
});

test("status rows expand local logs with Enter and collapse again", () => {
	const tui = {
		terminal: { columns: 96, rows: 28 },
		requestRender() {},
	} as unknown as TUI;
	const page = new StatusPage(tui, () => snapshot);
	assert.doesNotMatch(page.render(96).map(stripTerminalSequences).join("\n"), /Fourth detail/);
	assert.equal(page.handleInput("\r"), true);
	const expanded = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(expanded, /Session: main/);
	assert.match(expanded, /Fourth detail/);
	assert.equal(page.handleInput("\r"), true);
	assert.doesNotMatch(page.render(96).map(stripTerminalSequences).join("\n"), /Fourth detail/);
});

test("Enter opens agent and session rows instead of expanding their logs", () => {
	const tui = {
		terminal: { columns: 96, rows: 28 },
		requestRender() {},
	} as unknown as TUI;
	const opened: Array<[string | undefined, string | undefined]> = [];
	const page = new StatusPage(tui, () => snapshot, (row, section) => {
		opened.push([row.id, section]);
		return section === "Agents" || section === "Sessions";
	});
	page.render(96);
	assert.equal(page.handleInput("\r"), true);
	assert.deepEqual(opened, [["agent:main", "Agents"]]);
	// Opening navigates; it must not also expand the inspector logs.
	assert.doesNotMatch(page.render(96).map(stripTerminalSequences).join("\n"), /Fourth detail/);
});

test("status rows and filter tabs are clickable", () => {
	const tui = {
		terminal: { columns: 96, rows: 28 },
		requestRender() {},
	} as unknown as TUI;
	const page = new StatusPage(tui, () => snapshot);
	page.render(96);
	const click = (x: number, y: number) => ({
		type: "click", button: "left", x, y, screenX: x, screenY: y,
		width: 96, height: 28, shift: false, alt: false, ctrl: false,
	}) as never;
	const before = page.render(96).map(stripTerminalSequences);
	const rowY = before.findIndex(line => line.includes("● Main · Build a calculator"));
	assert.ok(rowY > 0);
	assert.equal(page.handleMouse(click(5, rowY))?.handled, true);
	assert.match(page.render(96).map(stripTerminalSequences).join("\n"), /Session: main/);
	// Click the Tools filter pill at its rendered position.
	const tabs = page.render(96).map(stripTerminalSequences)[1]!;
	const toolsX = tabs.indexOf("Tools") + 2;
	page.handleMouse(click(toolsX, 1));
	const filtered = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(filtered, /▏TOOLS \/ MCP/);
	assert.doesNotMatch(filtered, /▏AGENTS/);
});

test("status page swaps instantly without a transition frame", () => {
	const tui = {
		terminal: { columns: 80, rows: 24 },
		requestRender() {},
	} as unknown as TUI;
	const page = new StatusPage(tui, () => snapshot);
	page.enter(1);
	const rendered = page.render(80).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /Machina · Activity/);
	page.dispose();
});

test("number keys jump between filters", () => {
	const tui = {
		terminal: { columns: 96, rows: 28 },
		requestRender() {},
	} as unknown as TUI;
	const page = new StatusPage(tui, () => snapshot);
	page.render(96);
	assert.equal(page.handleInput("5"), true);
	const filtered = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(filtered, /▏TOOLS \/ MCP/);
	assert.doesNotMatch(filtered, /▏SHELLS/);
	assert.equal(page.handleInput("9"), false);
});

test("narrow terminals keep list and inspector on one screen", () => {
	const tui = {
		terminal: { columns: 64, rows: 22 },
		requestRender() {},
	} as unknown as TUI;
	const page = new StatusPage(tui, () => snapshot);
	const rendered = page.render(64).map(stripTerminalSequences);
	const screen = rendered.join("\n");
	assert.match(screen, /Inspector/);
	assert.match(screen, /Main · Build a calculator/);
	assert.match(screen, /Project|longcat/);
	assert.ok(rendered.every((line) => line.length <= 64));
});
