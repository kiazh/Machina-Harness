// Compact, non-focus-stealing tabs for the current agent family.
import { type TuiMouseEvent, truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";
import { sanitizeDisplay } from "../design/text.ts";
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
		let line = "";
		let column = 0;
		for (const row of this.rows()) {
			const title = row.main ? "Main" : row.session.title.split(":", 1)[0] || "Agent";
			const label = ` ${sanitizeDisplay(title).slice(0, 18)}${row.running ? " ●" : ""} `;
			const size = visibleWidth(label);
			if (column + size > width) break;
			this.hitboxes.push({ start: column, end: column + size, id: row.session.id });
			line += row.session.id === this.current() ? theme.accent(label) : theme.dim(label);
			column += size;
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
