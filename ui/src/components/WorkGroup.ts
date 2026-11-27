// A per-turn work block: intermediate thoughts and tool calls collapse into
// one count row after the turn finishes. The final answer stays outside.
import { truncateToWidth, type Component, type TuiMouseEvent } from "@earendil-works/pi-tui";
import { ToolTurn } from "./ToolTurn.ts";

type WorkKind = "thought" | "tool";

interface WorkCounts {
	commands: number;
	edits: number;
	reads: number;
	agents: number;
	other: number;
	thoughts: number;
}

function isToolName(name: string): string {
	if (name === "terminal") return "command";
	if (name === "write_file" || name.includes("edit") || name.includes("patch")) return "edit";
	if (["read_file", "list_files", "search_files", "artifact_read"].includes(name)) return "read";
	if (name === "delegate" || name === "harness_delegate") return "agent";
	return "other";
}

function plural(count: number, noun: string): string {
	return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

export class WorkGroup implements Component {
	private readonly children: Component[] = [];
	private readonly counts: WorkCounts = {
		commands: 0,
		edits: 0,
		reads: 0,
		agents: 0,
		other: 0,
		thoughts: 0,
	};
	private collapsed = false;
	private readonly cached = new Map<number, string[]>();

	add(component: Component, kind: WorkKind, toolName = ""): void {
		if (kind === "thought") {
			this.counts.thoughts += 1;
		} else {
			const bucket = isToolName(toolName) as "command" | "edit" | "read" | "agent" | "other";
			const key = bucket === "command" ? "commands" : bucket === "edit" ? "edits" : bucket === "read" ? "reads" : bucket === "agent" ? "agents" : "other";
			this.counts[key] += 1;
			if (component instanceof ToolTurn && this.children.at(-1) instanceof ToolTurn) {
				component.joinPreviousTool();
			}
		}
		this.children.push(component);
		this.cached.clear();
	}

	finish(_elapsedMs: number): void {
		this.collapsed = true;
		this.cached.clear();
	}

	toggle(): void {
		this.collapsed = !this.collapsed;
		this.cached.clear();
	}

	get toolCount(): number {
		return this.counts.commands + this.counts.edits + this.counts.reads + this.counts.agents + this.counts.other;
	}

	get empty(): boolean {
		return this.children.length === 0;
	}

	dissolve(): Component[] {
		const children = [...this.children];
		this.children.length = 0;
		return children;
	}

	detach(component: Component): boolean {
		const index = this.children.indexOf(component);
		if (index === -1) return false;
		this.children.splice(index, 1);
		if (!(component instanceof ToolTurn)) this.counts.thoughts = Math.max(0, this.counts.thoughts - 1);
		this.cached.clear();
		if (this.children.length === 0) return true;
		return false;
	}

	replace(component: Component, replacement: Component): boolean {
		const index = this.children.indexOf(component);
		if (index === -1) return false;
		this.children[index] = replacement;
		this.cached.clear();
		return true;
	}

	private summary(): string {
		const parts: string[] = [];
		if (this.counts.commands) parts.push(plural(this.counts.commands, "command"));
		if (this.counts.edits) parts.push(plural(this.counts.edits, "edit"));
		if (this.counts.thoughts) parts.push(plural(this.counts.thoughts, "thought"));
		if (this.counts.reads) parts.push(plural(this.counts.reads, "read"));
		if (this.counts.agents) parts.push(plural(this.counts.agents, "agent"));
		if (this.counts.other) parts.push(plural(this.counts.other, "tool"));
		if (!parts.length) parts.push(plural(this.children.length, "step"));
		return parts.join(", ");
	}

	render(width: number): string[] {
		const cached = this.cached.get(width);
		if (cached && this.cached.size > 0) return cached;
		const total = Math.max(0, width);
		const marker = this.collapsed ? "+" : "-";
		const line = truncateToWidth(`${marker} ${this.summary()}`, total);
		const rows = this.collapsed ? [line] : [line, ...this.children.flatMap((child) => child.render(width))];
		if (this.collapsed && this.cached.size >= 4) this.cached.clear();
		if (this.collapsed) this.cached.set(width, rows);
		return rows;
	}

	handleMouse(event: TuiMouseEvent): { handled: boolean; render: boolean } | undefined {
		if (event.type !== "click" || event.button !== "left") return undefined;
		if (event.y === 0) {
			this.toggle();
			return { handled: true, render: true };
		}
		if (this.collapsed) return undefined;
		let offset = 1;
		for (const child of this.children) {
			const height = child.render(event.width).length;
			if (event.y >= offset && event.y < offset + height) {
				if (!child.handleMouse) return undefined;
				const result = child.handleMouse({ ...event, y: event.y - offset, height });
				return result ? { handled: result.handled === true, render: result.render === true } : undefined;
			}
			offset += height;
		}
		return undefined;
	}

	refreshSkin(): void {
		for (const child of this.children) {
			if ("refreshSkin" in child && typeof (child as { refreshSkin?: unknown }).refreshSkin === "function") {
				(child as { refreshSkin(): void }).refreshSkin();
			}
		}
		this.cached.clear();
	}

	invalidate(): void {
		for (const child of this.children) child.invalidate?.();
		this.cached.clear();
	}
}
