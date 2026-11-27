// Assistant prose is a readable column, not a framed card. These tests pin
// the clean layout: stable measure, no frame glyphs, natural height, and
// exact streamed text.
import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, visibleWidth, type Component } from "@earendil-works/pi-tui";
import { AssistantTurn } from "../src/components/AssistantTurn.ts";
import { ICONS } from "../src/design/icons.ts";

const SHORT = "hello **world**";
const LONG = Array.from({ length: 24 }, (_, i) => `Paragraph ${i}: ${"detail ".repeat(6)}`).join("\n\n");
const FENCE = "```ts\nconst a = 1;\nconst b = 2;\nconsole.log(a + b);\n```";
const TABLE = "| name | value |\n| --- | --- |\n| alpha | 1 |";

function plainLines(component: Component, width: number): string[] {
	return component.render(width).map((line) => stripTerminalSequences(line));
}

test("assistant prose is frameless and uses the pane width", () => {
	const turn = new AssistantTurn(SHORT);
	const geometry = turn.responseGeometry(160);
	assert.equal(geometry.widthClass, "full");
	assert.equal(geometry.framed, false);
	assert.equal(geometry.contentWidth, 154, "the prose column fills the pane minus its gutter");
	const lines = plainLines(turn, 160);
	const text = lines.join("\n");
	assert.match(text, /hello/);
	assert.doesNotMatch(text, /[┌┐└┘│]/);
});

test("long replies keep natural height and every row fits", () => {
	const turn = new AssistantTurn(LONG);
	const lines = plainLines(turn, 160);
	assert.ok(lines.length > 10, "long prose emits its natural rows");
	for (const line of lines) assert.ok(visibleWidth(line) <= 160);
	const joined = lines.join("\n");
	assert.match(joined, /Paragraph 23/);
});

test("every emitted row fits extreme widths without throwing", () => {
	const widths = [0, 1, 2, 20, 40, 80, 120, 160, 240, 3, 8, 15, 16];
	for (const text of ["", SHORT, LONG, FENCE, TABLE]) {
		const turn = new AssistantTurn(text);
		for (const width of widths) {
			let lines: string[] = [];
			assert.doesNotThrow(() => {
				lines = turn.render(width);
			}, `render threw at width ${width}`);
			for (const line of lines) {
				assert.ok(
					visibleWidth(line) <= width,
					`width ${width} exceeded: ${JSON.stringify(stripTerminalSequences(line))}`,
				);
			}
		}
	}
});

test("streaming text is exact and the marker disappears on canonical text", () => {
	const turn = new AssistantTurn("");
	const tokens = ["Short opener.", "\n\n", "more text"];
	for (const token of tokens) turn.append(token);
	assert.equal(turn.getText(), tokens.join(""), "appended text is exact");
	assert.match(plainLines(turn, 100).join("\n"), /Short opener/);
	turn.setText("Short again.");
	assert.equal(turn.getText(), "Short again.", "canonical text replaces the streamed text exactly");
	const text = plainLines(turn, 100).join("\n");
	assert.match(text, /· Short again/);
	for (const frame of ICONS.workingFrames) assert.ok(!text.includes(frame), `no ${frame} marker remains after completion`);
});

test("the measure follows the pane and fills it", () => {
	const turn = new AssistantTurn(LONG);
	assert.equal(turn.responseGeometry(240).contentWidth, 234);
	assert.ok(turn.responseGeometry(80).contentWidth < 96);
	assert.equal(turn.responseGeometry(80).contentWidth, 74);
	for (const line of turn.render(240)) assert.ok(visibleWidth(line) <= 240);
});

test("geometry is cached by content revision and pane width", () => {
	const turn = new AssistantTurn(SHORT);
	const first = turn.responseGeometry(160);
	assert.equal(turn.responseGeometry(160), first, "same revision and pane reuse the cached decision");
	assert.notEqual(turn.responseGeometry(80), first, "a pane change recomputes");
	turn.append(" more words");
	assert.notEqual(turn.responseGeometry(160), first, "appended content invalidates the cache");
});

test("sanitization runs before rendering: hostile control text stays inert", () => {
	const url = `https://example.com/${"x".repeat(300)}`;
	const hostile = `\x1b]8;;${url}\x07link\x1b]8;;\x07 and \x1b[2Jcleared\x1b[0m`;
	const turn = new AssistantTurn(hostile);
	const joined = turn.render(160).join("\n");
	assert.ok(!joined.includes("\x1b]"), "OSC introducers never reach the transcript");
	assert.ok(!joined.includes("\x1b[2J"), "unsafe CSI is stripped before rendering");
	assert.match(stripTerminalSequences(joined), /link/);
	assert.match(stripTerminalSequences(joined), /cleared/);
});
