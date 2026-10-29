import assert from "node:assert/strict";
import test from "node:test";
import type { Component } from "@earendil-works/pi-tui";
import { AssistantTurn } from "../src/components/AssistantTurn.ts";
import { Notice } from "../src/components/Notice.ts";
import { ToolTurn } from "../src/components/ToolTurn.ts";
import { UserTurn } from "../src/components/UserTurn.ts";

test("finished transcript blocks reuse rendered rows until content, width, or skin changes", () => {
	const blocks: Component[] = [
		new AssistantTurn("A **response**"),
		new UserTurn("A prompt"),
		new Notice("done"),
	];
	const tool = new ToolTurn("read_file", { path: "a.py" }, { frame: (frames) => frames[0] ?? "" });
	tool.settle({ text: "content", backendTruncated: false, locallyTruncated: false }, "success");
	blocks.push(tool);
	for (const block of blocks) {
		const first = block.render(80);
		assert.equal(block.render(80), first, "unchanged block reuses the same rows");
		assert.notEqual(block.render(79), first, "width changes invalidate the cached rows");
		assert.equal(block.render(80), first, "split-layout measurement widths do not evict the visible width");
		block.invalidate();
		assert.notEqual(block.render(80), first, "theme invalidation rebuilds the rows");
	}
	const assistant = blocks[0] as AssistantTurn;
	const prior = assistant.render(80);
	assistant.append(" more");
	assert.notEqual(assistant.render(80), prior);
});
