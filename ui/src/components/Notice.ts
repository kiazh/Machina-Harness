// Notices are generated output from commands, tools, and the harness.
// Generated reports, multi-line output, and guidance use the same quiet framed
// surface as assistant prose. Short status lines stay compact so they do not
// crowd out approval evidence. Narrow terminals keep a plain fallback because
// a frame would consume the text column.

import { Text, truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";
import type { NoticeKind } from "../features/types.ts";
import {
	assistantFrameBudget,
	renderAssistantFrame,
	type ResponseGeometry,
} from "../design/responseLayout.ts";
import { BLOCK_GAP } from "../design/tokens.ts";
import { transcriptInset } from "../design/spacing.ts";
import { sanitizeDisplay, wrapDisplay } from "../design/text.ts";
import { repairFill, theme } from "../theme.ts";

const LABELS: Record<NoticeKind, string> = {
	info: "notice",
	plain: "output",
	guidance: "next steps",
	success: "success",
	warning: "warning",
	error: "error",
};

export class Notice extends Text {
	private readonly raw: string;
	private readonly kind: NoticeKind;
	private readonly cached = new Map<number, string[]>();

	constructor(text: string, kind: NoticeKind = "info") {
		super("", 0, 0);
		this.raw = text;
		this.kind = kind;
		this.refreshSkin();
	}

	/** Re-resolve theme colors (skins switch live; text was baked at construction). */
	refreshSkin(): void {
		this.cached.clear();
		this.setText(this.style(this.markedText()));
	}

	override render(width: number): string[] {
		const cached = this.cached.get(width);
		if (cached) return cached;
		const rows = this.renderFramed(width);
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

	/** The semantic marker and sanitized body, without ANSI styling. */
	private markedText(): string {
		const safe = sanitizeDisplay(this.raw);
		if (this.kind === "success") return `✓ ${safe}`;
		if (this.kind === "warning") return `! ${safe}`;
		if (this.kind === "error") return `✗ ${safe}`;
		return safe;
	}

	private wrappedLines(width: number): string[] {
		const text = this.markedText();
		return text
			.split("\n")
			.flatMap((line) => {
				if (!line) return [""];
				const leading = /^\s*/.exec(line)?.[0] ?? "";
				const body = line.slice(leading.length);
				return wrapDisplay(body, Math.max(1, width - visibleWidth(leading))).map(
					(piece) => leading + piece,
				);
			});
	}

	private shouldFrame(): boolean {
		// Short status lines stay compact. Generated reports, multi-line
		// output, and guidance get the full framed surface.
		return (
			this.kind === "plain" ||
			this.kind === "guidance" ||
			this.raw.includes("\n") ||
			this.raw.length > 120
		);
	}

	private renderFramed(width: number): string[] {
		const total = Math.max(0, width);
		const budget = assistantFrameBudget(total);
		if (!this.shouldFrame() || !budget.framed) return this.renderPlain(width);
		const contentWidth = Math.max(1, budget.available);
		const rows = this.wrappedLines(contentWidth).map((line) => this.style(line));
		const geometry: ResponseGeometry = {
			paneWidth: total,
			widthClass: "full",
			framed: true,
			indent: budget.indent,
			span: Math.min(budget.frameBudget, contentWidth + 4),
			contentWidth,
			frameRows: rows.length + 2,
		};
		return [
			...BLOCK_GAP,
			...renderAssistantFrame(
				rows,
				geometry,
				theme.dim("·"),
				{
					border: theme.border,
					accentBold: theme.accentBold,
					fill: (line) => repairFill("assistantBg", theme.assistantBg(line)),
					dim: theme.dim,
				},
				{ label: LABELS[this.kind] },
			),
		];
	}

	private renderPlain(width: number): string[] {
		const total = Math.max(0, width);
		const gutter = " ".repeat(transcriptInset(width));
		const text = this.style(this.markedText());
		this.setText(text);
		return [
			...BLOCK_GAP,
			...super
				.render(Math.max(1, width - gutter.length))
				.map((line) => truncateToWidth(gutter + line, total)),
		];
	}
}
