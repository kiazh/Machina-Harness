// Plain user turn: dim `❯` prefix, normal text, no box. Multiline content is
// preserved; continuation wraps beneath the text column.
//
// The prompt shares the transcript column with assistant prose: a two-cell
// gutter, a two-cell marker, then the body. The raised fill stops at the
// column edge so the user's own words never outshout the answer.

import { truncateToWidth, visibleWidth, type Component } from "@earendil-works/pi-tui";
import { BLOCK_GAP } from "../design/tokens.ts";
import { chatColumnWidth, transcriptInset } from "../design/spacing.ts";
import { sanitizeDisplay, wrapDisplay } from "../design/text.ts";
import { isOpaqueBackground, repairFill, theme } from "../theme.ts";

export class UserTurn implements Component {
	private readonly raw: string;
	private readonly cached = new Map<number, string[]>();

	constructor(text: string, _index?: number) {
		this.raw = text;
	}

	/** Re-resolve theme colors (render is lazy; text was never baked). */
	refreshSkin(): void {
		this.invalidate();
	}

	render(width: number): string[] {
		const cached = this.cached.get(width);
		if (cached) return cached;
		const total = Math.max(0, width);
		const inset = transcriptInset(total);
		const column = chatColumnWidth(total);
		const block = Math.min(total, inset + column);
		const head = "❯ ";
		const inner = Math.max(1, block - inset - visibleWidth(head) - 1);
		const lines = sanitizeDisplay(this.raw)
			.split("\n")
			.flatMap((line) => wrapDisplay(line, inner));
		const body = lines.length ? lines : [""];
		const margin = " ".repeat(inset);
		const opaque = isOpaqueBackground();
		const ink = opaque ? theme.promptInk : theme.text;
		const rows = body.map((line) => {
			const prefix = ink(head);
			const content = `${margin}${prefix}${ink(truncateToWidth(line, inner))}`;
			const filled = Math.max(0, block - visibleWidth(content));
			const padded = `${content}${" ".repeat(filled)}`;
			const painted = opaque ? repairFill("promptBg", theme.promptBg(padded)) : padded;
			return `${painted}${" ".repeat(Math.max(0, total - visibleWidth(painted)))}`;
		});
		const out = [...BLOCK_GAP, ...rows.map((line) => truncateToWidth(line, total))];
		if (this.cached.size >= 4) this.cached.clear();
		this.cached.set(width, out);
		return out;
	}

	invalidate(): void { this.cached.clear(); }
}
