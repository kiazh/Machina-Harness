// Plain user turn: dim `>` prefix, normal text, no box. Multiline content is
// preserved; continuation wraps beneath the text column.

import { truncateToWidth, visibleWidth, type Component } from "@earendil-works/pi-tui";
import { BLOCK_GAP } from "../design/tokens.ts";
import { sanitizeDisplay, wrapDisplay } from "../design/text.ts";
import { isOpaqueBackground, repairFill, theme } from "../theme.ts";

export class UserTurn implements Component {
	private readonly raw: string;
	private readonly index?: number;
	private readonly cached = new Map<number, string[]>();

	constructor(text: string, index?: number) {
		this.raw = text;
		this.index = index;
	}

	/** Re-resolve theme colors (render is lazy; text was never baked). */
	refreshSkin(): void {
		this.invalidate();
	}

	render(width: number): string[] {
		const cached = this.cached.get(width);
		if (cached) return cached;
		const total = Math.max(0, width);
		const number = this.index === undefined ? "" : `${String(this.index).padStart(2, "0")} `;
		const head = `${number}❯ `;
		const inner = Math.max(1, total - visibleWidth(head) - 1);
		const lines = sanitizeDisplay(this.raw)
			.split("\n")
			.flatMap((line) => wrapDisplay(line, inner));
		const body = lines.length ? lines : [""];
		const opaque = isOpaqueBackground();
		const ink = opaque ? theme.promptInk : theme.text;
		const rows = body.map((line, index) => {
			const prefix = index === 0 ? ink(head) : " ".repeat(visibleWidth(head));
			const content = `${prefix}${ink(truncateToWidth(line, inner))}`;
			const padded = `${content}${" ".repeat(Math.max(0, total - visibleWidth(content)))}`;
			return opaque ? repairFill("promptBg", theme.promptBg(padded)) : padded;
		});
		const out = [...BLOCK_GAP, ...rows.map((line) => truncateToWidth(line, total))];
		if (this.cached.size >= 4) this.cached.clear();
		this.cached.set(width, out);
		return out;
	}

	invalidate(): void { this.cached.clear(); }
}
