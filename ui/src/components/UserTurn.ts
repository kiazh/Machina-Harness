import { truncateToWidth, type Component } from "@earendil-works/pi-tui";
import { BLOCK_GAP } from "../design/tokens.ts";
import { chatColumnWidth, transcriptInset } from "../design/spacing.ts";
import { sanitizeDisplay, wrapDisplay } from "../design/text.ts";
import { isOpaqueBackground, repairFill, theme } from "../theme.ts";

export class UserTurn implements Component {
	private readonly raw: string;
	private readonly cached = new Map<number, string[]>();

	constructor(text: string) {
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
		const roomy = column >= 52;
		const inner = Math.max(1, column - (roomy ? 5 : 2));
		const lines = sanitizeDisplay(this.raw)
			.split("\n")
			.flatMap((line) => wrapDisplay(line, inner));
		const body = lines.length ? lines : [""];
		const margin = " ".repeat(inset);
		const opaque = isOpaqueBackground();
		const ink = opaque ? theme.promptInk : theme.text;
		const rows = body.map((line, index) => {
			const prefix = index === 0 ? theme.accentBold("❯ ") : "  ";
			return `${roomy ? "  " : ""}${prefix}${ink(line)}`;
		});
		const bodyRows = roomy ? ["", ...rows, ""] : rows;
		const paintedRows = bodyRows.map((line) => {
			const padded = truncateToWidth(line, column, "", opaque);
			const painted = opaque ? repairFill("promptBg", padded) : padded;
			return truncateToWidth(`${margin}${painted}`, total, "");
		});
		const out = [...BLOCK_GAP, ...paintedRows, ""];
		if (this.cached.size >= 4) this.cached.clear();
		this.cached.set(width, out);
		return out;
	}

	invalidate(): void { this.cached.clear(); }
}
