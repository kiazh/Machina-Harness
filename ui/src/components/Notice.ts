// Concise notice: 1–2 lines with a text marker plus semantic color, so
// state survives monochrome skins. Explicit reports use a viewer instead.

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
		// Untrusted content path (command output, recalled transcripts, MCP
		// errors): strip terminal controls before display — same sanitizer
		// UserTurn/ToolTurn/ApprovalCard already use (wave-3 hotfix).
		const safe = sanitizeDisplay(this.raw);
		const marked =
			this.kind === "success"
				? `✓ ${safe}`
				: this.kind === "warning"
					? `! ${safe}`
					: this.kind === "error"
						? `✗ ${safe}`
						: safe;
		const style = {
			info: theme.info,
			plain: theme.text,
			guidance: theme.text,
			success: theme.success,
			warning: theme.warning,
			error: theme.error,
		}[this.kind];
		this.setText(style(marked));
	}

	override render(width: number): string[] {
		const cached = this.cached.get(width);
		if (cached) return cached;
		const rows = this.kind === "guidance" ? this.renderGuidance(width) : this.renderPlain(width);
		if (this.cached.size >= 4) this.cached.clear();
		this.cached.set(width, rows);
		return rows;
	}

	private renderPlain(width: number): string[] {
		const total = Math.max(0, width);
		const gutter = " ".repeat(transcriptInset(width));
		return [...BLOCK_GAP, ...super.render(Math.max(1, width - gutter.length)).map((line) => truncateToWidth(gutter + line, total))];
	}

	/**
	 * A guidance notice is an explanation, not a one-line status. Give it the
	 * same adaptive frame as an assistant response, labeled "next steps", so
	 * it reads as a deliberate panel instead of loose transcript text.
	 */
	private wrapGuidance(width: number): string[] {
		const w = Math.max(1, width);
		return sanitizeDisplay(this.raw)
			.split("\n")
			.flatMap((line) => {
				const leading = /^\s*/.exec(line)?.[0] ?? "";
				const body = line.slice(leading.length);
				const pieces = wrapDisplay(body, Math.max(1, w - visibleWidth(leading)));
				return pieces.map((piece) => leading + piece);
			});
	}

	private renderGuidance(width: number): string[] {
		const total = Math.max(0, width);
		const budget = assistantFrameBudget(total);
		if (!budget.framed) return this.renderPlain(width);
		const contentWidth = Math.min(Math.max(1, budget.available), 96);
		const rows = this.wrapGuidance(contentWidth);
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
				{ label: "next steps" },
			),
		];
	}

	override invalidate(): void {
		this.cached.clear();
		super.invalidate();
	}
}
