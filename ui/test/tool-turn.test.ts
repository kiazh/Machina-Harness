import assert from "node:assert/strict";
import test from "node:test";
import { ToolTurn } from "../src/components/ToolTurn.ts";

const ACTIVITY = { frame: (frames: readonly string[]) => frames[0] ?? "" };

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
