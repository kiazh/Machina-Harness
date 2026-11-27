// Golden-frame primitives shared by the UI characterization harness.
//
// Frames are normalized so a checked-in snapshot never depends on wall-clock
// time, locale, animation phase, or tool latency. Width/row invariants are
// asserted on the raw terminal lines before normalization.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { visibleWidth } from "@earendil-works/pi-tui";

export const GOLDEN_SIZES = [
	{ columns: 40, rows: 8 },
	{ columns: 40, rows: 15 },
	{ columns: 40, rows: 40 },
	{ columns: 80, rows: 8 },
	{ columns: 80, rows: 15 },
	{ columns: 80, rows: 40 },
	{ columns: 120, rows: 8 },
	{ columns: 120, rows: 15 },
	{ columns: 120, rows: 40 },
] as const;

/**
 * Canonical display form: no trailing padding, no wall-clock time, no age or
 * tool-duration drift. Every frame is rendered under reduced motion, so an
 * animation frame never reaches this function.
 */
export function normalizeFrame(lines: readonly string[]): string[] {
	return lines.map(line => line
		// The Thought row shows elapsed ms only when the block closed on a
		// later tick; the collapsed label alone is the canonical evidence.
		.replace(/\bThought · \d+(?:\.\d+)?(?:ms|s)\b/g, "Thought")
		.replace(/\b\d{1,2}:\d{2}:\d{2}(?:\s?[ap]\.m\.)?/gi, "<time>")
		.replace(/\b\d+ms\b/g, "<ms>")
		.replace(/\b(?:now|\d+[smhd] ago)\b/g, "<age>")
		.trimEnd());
}

/** The raw frame must fill the terminal exactly and never overflow it. */
export function assertFrameInvariants(
	label: string,
	lines: readonly string[],
	columns: number,
	rows: number,
): void {
	assert.equal(lines.length, rows, `${label}: expected ${rows} rendered rows`);
	for (const [index, line] of lines.entries()) {
		assert.ok(
			visibleWidth(line) <= columns,
			`${label}: row ${index + 1} is ${visibleWidth(line)} cells wide (limit ${columns})`,
		);
	}
}

export function goldenPath(name: string): URL {
	return new URL(`../golden/${name}`, import.meta.url);
}

export function readGolden(name: string): string {
	return readFileSync(goldenPath(name), "utf8");
}

export function writeGolden(name: string, text: string): void {
	const path = goldenPath(name);
	mkdirSync(dirname(fileURLToPath(path)), { recursive: true });
	writeFileSync(path, text);
}

export function updatingGolden(): boolean {
	return process.env.UPDATE_GOLDEN === "1";
}

/** First line carrying a selection glyph; stable enough to follow navigation. */
export function selectionLine(lines: readonly string[]): string {
	return lines.find(line => /[›❯]/.test(line))?.trim() ?? "";
}

/** First line containing `needle`, trimmed; empty when absent. */
export function lineWith(lines: readonly string[], needle: string): string {
	return lines.find(line => line.includes(needle))?.trim() ?? "";
}
