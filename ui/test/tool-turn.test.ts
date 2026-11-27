import assert from "node:assert/strict";
import test from "node:test";
import { stripTerminalSequences, type TuiMouseEvent } from "@earendil-works/pi-tui";
import { ToolTurn } from "../src/components/ToolTurn.ts";

const ACTIVITY = { frame: (frames: readonly string[]) => frames[0] ?? "" };

function click(y: number): TuiMouseEvent {
	return {
		type: "click",
		button: "left",
		x: 2,
		y,
		screenX: 2,
		screenY: y,
		width: 80,
		height: 24,
		shift: false,
		alt: false,
		ctrl: false,
	};
}

function text(tool: ToolTurn, width = 80): string {
	return tool.render(width).map(stripTerminalSequences).join("\n");
}

test("harness_delegate shows which external harness is being consulted", () => {
	const tool = new ToolTurn(
		"harness_delegate",
		{ platform: "codex", task: "review the change" },
		ACTIVITY,
	);

	const running = tool.render(80).join("\n");
	assert.match(running, /Consulting Codex/);
	assert.match(running, /waiting for external harness/);

	tool.settle({ text: "done", backendTruncated: false, locallyTruncated: false }, "success");
	const finished = tool.render(80).join("\n");
	assert.match(finished, /Consulting Codex/);
	assert.doesNotMatch(finished, /waiting for external harness/);
});

test("harness_delegate without a platform says external harness", () => {
	const tool = new ToolTurn("harness_delegate", { task: "review" }, ACTIVITY);

	assert.match(tool.render(80).join("\n"), /Consulting external harness/);
});

test("mcp_call names the connector mention and a clean tool label", () => {
	const tool = new ToolTurn(
		"mcp_call",
		{ name: "vercel__list_projects", arguments: {} },
		ACTIVITY,
	);

	const running = tool.render(80).join("\n");
	assert.match(running, /@vercel · List projects/);
	assert.doesNotMatch(running, /needs_approval|vercel__/);
});

test("a settled tool expands on a header click and collapses again", () => {
	const tool = new ToolTurn("terminal", { command: "pytest" }, ACTIVITY);
	tool.settle(
		{ text: "one\ntwo\nthree\nfour", backendTruncated: false, locallyTruncated: false },
		"success",
	);
	const collapsed = text(tool);
	assert.match(collapsed, /▸/);
	assert.match(collapsed, /one/);
	assert.doesNotMatch(collapsed, /three/);
	assert.equal(tool.handleMouse(click(1))?.handled, true);
	const expanded = text(tool);
	assert.match(expanded, /▾/);
	assert.match(expanded, /three/);
	assert.match(expanded, /four/);
	assert.equal(tool.handleMouse(click(8)), undefined, "body clicks stay available for selection");
	assert.equal(tool.handleMouse(click(1))?.handled, true);
	assert.doesNotMatch(text(tool), /three/);
});

test("write_file shows its new content without putting content in the header", () => {
	const tool = new ToolTurn(
		"write_file",
		{ path: "src/app.py", content: "one\ntwo\nthree\n" },
		ACTIVITY,
	);
	const header = text(tool);
	assert.match(header, /Write file src\/app\.py/);
	assert.doesNotMatch(header, /content=/);
	assert.match(header, /one/);
	assert.doesNotMatch(header, /three/);
	tool.settle(
		{ text: "Successfully wrote 14 characters to src/app.py", backendTruncated: false, locallyTruncated: false },
		"success",
	);
	tool.handleMouse(click(1));
	assert.match(text(tool), /three/);
	assert.doesNotMatch(text(tool), /Successfully wrote/);
});
