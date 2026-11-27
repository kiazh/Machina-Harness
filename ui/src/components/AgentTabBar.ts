// Quiet agent family strip: plain names, one accent for the active chat,
// and a small dot for a running child. No pills, brackets, or counters.
import { type TuiMouseEvent, truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";
import { sanitizeLabelText } from "../design/text.ts";
import { theme } from "../design/theme.ts";
import type { FamilySession } from "../features/types.ts";

export class AgentTabBar {
	private readonly rows: () => FamilySession[];
	private readonly current: () => string | undefined;
	private readonly open: (sessionId: string) => void;
	private hitboxes: Array<{ start: number; end: number; id: string }> = [];

	constructor(rows: () => FamilySession[], current: () => string | undefined, open: (sessionId: string) => void) {
		this.rows = rows;
		this.current = current;
		this.open = open;
	}

	get visible(): boolean { return this.rows().length > 1; }

	render(width: number): string[] {
		if (width <= 0 || !this.visible) return [];
		this.hitboxes = [];
		let line = " ";
		let column = 1;
		for (const [index, row] of this.rows().entries()) {
			const title = row.main ? "main" : row.session.title.split(":", 1)[0] || "agent";
			const label = truncateToWidth(sanitizeLabelText(title), 18, "…");
			const dot = row.running ? " •" : "";
			const size = visibleWidth(label) + visibleWidth(dot);
			if (column + size > width) break;
			const style = row.session.id === this.current() ? theme.accentBold : theme.muted;
			this.hitboxes.push({ start: column, end: column + size, id: row.session.id });
			line += style(label);
			if (dot) line += theme.success(dot);
			column += size;
			if (index < this.rows().length - 1) {
				const gap = "   ";
				if (column + gap.length > width) break;
				line += gap;
				column += gap.length;
			}
		}
		return [truncateToWidth(line, width)];
	}

	handleMouse(event: TuiMouseEvent): { handled: boolean; render: boolean } | undefined {
		if (event.type !== "click" || event.button !== "left" || event.y !== 0) return undefined;
		const hit = this.hitboxes.find((box) => event.x >= box.start && event.x < box.end);
		if (!hit) return undefined;
		this.open(hit.id);
		return { handled: true, render: true };
	}

	invalidate(): void {}
}
