// Notices are generated output from commands, tools, and the harness.
// They read as a quiet line of prose: a semantic marker, an optional label,
// and wrapped text. No frame, no fill, and no nested card.

import { Text, truncateToWidth } from "@earendil-works/pi-tui";
import type { NoticeKind } from "../features/types.ts";
import { BLOCK_GAP } from "../design/tokens.ts";
import { transcriptInset } from "../design/spacing.ts";
import { sanitizeDisplay, wrapDisplay } from "../design/text.ts";
import { theme } from "../theme.ts";

const LABELS: Record<NoticeKind, string> = {
	info: "notice",
	plain: "output",
	guidance: "next",
	success: "done",
	warning: "warning",
	error: "error",
};

const MARKERS: Record<NoticeKind, string> = {
	info: "·",
	plain: " ",
	guidance: "→",
	success: "✓",
	warning: "!",
	error: "✗",
};

export class Notice extends Text {
	private readonly raw: string;
	private readonly kind: NoticeKind;
	private readonly cached = new Map<number, string[]>();

	constructor(text: string, kind: NoticeKind = "info") {
		super("", 0, 0);
		this.raw = text;
		this.kind = kind;
	}

	/** Re-resolve theme colors (skins switch live; render is lazy). */
	refreshSkin(): void {
		this.cached.clear();
	}

	override render(width: number): string[] {
		const cached = this.cached.get(width);
		if (cached) return cached;
		const rows = this.renderNotice(width);
		if (this.cached.size >= 4) this.cached.clear();
		this.cached.set(width, rows);
		return rows;
	}

	override invalidate(): void {
		this.cached.clear();
		super.invalidate();
	}

	private style(text: string): string {
		return {
			info: theme.info,
			plain: theme.text,
			guidance: theme.text,
			success: theme.success,
			warning: theme.warning,
			error: theme.error,
		}[this.kind](text);
	}

	private renderNotice(width: number): string[] {
		const total = Math.max(0, width);
		const pad = " ".repeat(transcriptInset(width));
		const inner = Math.max(1, total - pad.length - 2);
		const body = sanitizeDisplay(this.raw);
		const lines = body.split("\n").flatMap((line) => wrapDisplay(line, inner));
		const out = [...BLOCK_GAP];
		const marker = this.style(MARKERS[this.kind]);
		const multiline = lines.length > 1 || this.kind === "plain" || this.kind === "guidance";
		if (multiline && this.kind !== "plain") {
			out.push(truncateToWidth(`${pad}${theme.dim(LABELS[this.kind])}`, total));
		}
		lines.forEach((line, index) => {
			const prefix = index === 0 ? `${marker} ` : "  ";
			out.push(truncateToWidth(`${pad}${prefix}${this.style(line)}`, total));
		});
		return out;
	}
}
