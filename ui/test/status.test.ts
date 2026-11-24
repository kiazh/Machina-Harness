import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, type TUI } from "@earendil-works/pi-tui";
import { StatusPage, type StatusSnapshot } from "../src/components/StatusPage.ts";

const snapshot: StatusSnapshot = {
	agents: [
		{ id: "agent:main", label: "Main · Build a calculator", detail: "longcat · tool terminal", state: "running", running: true, logs: ["Session: main", "Active tool: terminal", "Third detail"] },
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
	assert.match(rendered, /Machina status/);
	assert.match(rendered, /Agents/);
	assert.match(rendered, /Main · Build a calculator/);
	assert.match(rendered, /Shells/);
	assert.match(rendered, /foreground shell/);
	assert.match(rendered, /MCP · mcp_call/);
	assert.match(rendered, /Harnesses/);
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
	assert.doesNotMatch(page.render(96).map(stripTerminalSequences).join("\n"), /Third detail/);
	assert.equal(page.handleInput("\r"), true);
	const expanded = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(expanded, /Session: main/);
	assert.match(expanded, /Third detail/);
	assert.equal(page.handleInput("\r"), true);
	assert.doesNotMatch(page.render(96).map(stripTerminalSequences).join("\n"), /Third detail/);
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
	assert.doesNotMatch(page.render(96).map(stripTerminalSequences).join("\n"), /Third detail/);
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
	assert.equal(page.handleMouse(click(5, 7))?.handled, true);
	assert.match(page.render(96).map(stripTerminalSequences).join("\n"), /Session: main/);
	// Tab row starts at x=1; click the Tools filter.
	page.handleMouse(click(40, 1));
	const filtered = page.render(96).map(stripTerminalSequences).join("\n");
	assert.match(filtered, /~Tools \/ MCP/);
	assert.doesNotMatch(filtered, /~Agents/);
});

test("status page swaps instantly without a transition frame", () => {
	const tui = {
		terminal: { columns: 80, rows: 24 },
		requestRender() {},
	} as unknown as TUI;
	const page = new StatusPage(tui, () => snapshot);
	page.enter(1);
	const rendered = page.render(80).map(stripTerminalSequences).join("\n");
	assert.match(rendered, /Machina status/);
	page.dispose();
});
