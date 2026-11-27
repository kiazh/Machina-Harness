// One intermediate reasoning/commentary block. Collapsed by default, like
// OpenCode's Thought row; expanding shows the assistant prose beneath it.
import { truncateToWidth, type Component, type TuiMouseEvent } from "@earendil-works/pi-tui";
import { theme } from "../theme.ts";
import { AssistantTurn } from "./AssistantTurn.ts";

function duration(ms: number): string {
	if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
	return `${(ms / 1000).toFixed(1)}s`;
}

export class ThoughtTurn implements Component {
	private readonly turn: AssistantTurn;
	private readonly elapsedMs: number;
	private collapsed: boolean;

	constructor(turn: AssistantTurn, elapsedMs: number, collapsed = false) {
		this.turn = turn;
		this.elapsedMs = elapsedMs;
		this.collapsed = collapsed;
	}

	render(width: number): string[] {
		const label = this.elapsedMs > 0 ? `Thought · ${duration(this.elapsedMs)}` : "Thought";
		const header = truncateToWidth(
			`  ${theme.secondary(this.collapsed ? "▸" : "▾")} ${theme.muted(label)}`,
			Math.max(0, width),
		);
		if (this.collapsed) return [header];
		return [header, ...this.turn.render(width)];
	}

	handleMouse(event: TuiMouseEvent): { handled: boolean; render: boolean } | undefined {
		if (event.type !== "click" || event.button !== "left" || event.y !== 0) return undefined;
		this.collapsed = !this.collapsed;
		return { handled: true, render: true };
	}

	refreshSkin(): void {
		this.turn.refreshSkin();
	}

	invalidate(): void {
		this.turn.invalidate();
	}
}
