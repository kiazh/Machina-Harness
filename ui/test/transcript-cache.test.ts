import assert from "node:assert/strict";
import test from "node:test";
import { stripTerminalSequences, type Component } from "@earendil-works/pi-tui";
import { AssistantTurn } from "../src/components/AssistantTurn.ts";
import { Notice } from "../src/components/Notice.ts";
import { ToolTurn } from "../src/components/ToolTurn.ts";
import { UserTurn } from "../src/components/UserTurn.ts";

function plain(component: Component, width = 80): string {
	return component.render(width).map((line) => stripTerminalSequences(line)).join("\n");
}

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

test("streamed deltas keep a split escape tail out of the visible text", () => {
	// Untrusted output can split an OSC title across two deltas. The carry
	// holds the incomplete sequence until its terminator arrives, so no part
	// of the title is ever shown.
	const turn = new AssistantTurn("");
	turn.append("visible ");
	turn.append("\x1b]0;hostile-title");
	assert.match(plain(turn), /visible/);
	assert.doesNotMatch(plain(turn), /hostile-title/);
	turn.append("\x07 tail");
	const out = plain(turn);
	assert.match(out, /visible\s+tail/);
	assert.doesNotMatch(out, /hostile-title/);
	assert.equal(turn.getText(), "visible \x1b]0;hostile-title\x07 tail", "raw text is preserved verbatim");
});

test("coalesced deltas and the final canonical text never lose or duplicate content", () => {
	const turn = new AssistantTurn("");
	turn.append("one ");
	turn.append("two ");
	turn.append("three");
	assert.match(plain(turn), /one two three/);
	// Canonical reconciliation is synchronous, even with a flush scheduled.
	turn.append(" stale");
	turn.setText("canonical answer");
	const out = plain(turn);
	assert.match(out, /canonical answer/);
	assert.doesNotMatch(out, /stale/);
});
