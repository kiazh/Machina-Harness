// A quiet colophon closes every turn: one accent glyph, elapsed time, tools
// and tokens. No box and no second rule; the transcript already has rhythm.
import { truncateToWidth, type Component } from "@earendil-works/pi-tui";
import { BLOCK_GAP } from "../design/tokens.ts";
import { transcriptInset } from "../design/spacing.ts";
import { theme } from "../theme.ts";

export interface TurnSummaryInfo {
	elapsedMs: number;
	tools: number;
	tokens: number;
	cancelled: boolean;
}

function compactTokens(tokens: number): string {
	if (tokens < 1000) return `${tokens} tokens`;
	if (tokens < 10_000) return `${(tokens / 1000).toFixed(1)}k tokens`;
	return `${Math.round(tokens / 1000)}k tokens`;
}

function elapsed(ms: number): string {
	if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
	if (ms < 60_000) return `${(ms / 1000).toFixed(0)}s`;
	const minutes = Math.floor(ms / 60_000);
	const seconds = Math.round((ms % 60_000) / 1000);
	return `${minutes}m ${seconds}s`;
}

export class TurnSummary implements Component {
	private readonly info: TurnSummaryInfo;

	constructor(info: TurnSummaryInfo) {
		this.info = info;
	}

	render(width: number): string[] {
		const total = Math.max(0, width);
		const pad = " ".repeat(transcriptInset(total));
		const parts: string[] = [];
		if (this.info.elapsedMs > 0) parts.push(elapsed(this.info.elapsedMs));
		if (this.info.tools > 0) parts.push(`${this.info.tools} tool${this.info.tools === 1 ? "" : "s"}`);
		if (this.info.tokens > 0) parts.push(compactTokens(this.info.tokens));
		if (!parts.length) return [];
		const glyph = this.info.cancelled ? theme.warning("⊘") : theme.accent("✻");
		const verb = this.info.cancelled ? "stopped after" : "done in";
		return [
			...BLOCK_GAP,
			truncateToWidth(`${pad}${glyph} ${theme.dim(`${verb} ${parts.join(" · ")}`)}`, total),
		];
	}

	invalidate(): void {}
}
