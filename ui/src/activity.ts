import { truncateToWidth, visibleWidth, type TuiMouseEvent } from "@earendil-works/pi-tui";
import { theme } from "./theme.ts";
import { sanitizeDisplay } from "./design/text.ts";

export interface SteeringItem { id: string; text: string }
export interface ShellJob { id: string; command: string; status: string; returncode?: number | null; output?: string; truncated?: boolean; foreground?: boolean; deadline?: number | null }
export interface ActivityAction { label: string; run: () => void }

export class ActivityStore {
	readonly queues = new Map<string, SteeringItem[]>();
	private readonly queueVersions = new Map<string, number>();
	readonly jobs = new Map<string, Map<string, ShellJob>>();
	setQueue(sessionId: string, items: SteeringItem[]): void {
		this.queues.set(sessionId, items.slice(0, 32));
		this.queueVersions.set(sessionId, this.queueVersion(sessionId) + 1);
	}
	queueVersion(sessionId: string): number { return this.queueVersions.get(sessionId) ?? 0; }
	setJob(sessionId: string, job: ShellJob): void {
		const jobs = this.jobs.get(sessionId) ?? new Map<string, ShellJob>();
		const prior = jobs.get(job.id);
		if (prior && prior.status !== "running" && job.status === "running") return;
		if (prior?.foreground === false && job.foreground === true) job = { ...job, foreground: false };
		jobs.set(job.id, job);
		if (jobs.size > 128) jobs.delete(jobs.keys().next().value!);
		this.jobs.set(sessionId, jobs);
	}
	queue(sessionId: string): SteeringItem[] { return this.queues.get(sessionId) ?? []; }
	listJobs(sessionId: string): ShellJob[] { return [...(this.jobs.get(sessionId)?.values() ?? [])]; }
}

export class ActivityBar {
	private readonly summary: () => string;
	private readonly open: () => void;
	private readonly actions: () => ActivityAction[];
	constructor(summary: () => string, open: () => void, actions: () => ActivityAction[] = () => []) { this.summary = summary; this.open = open; this.actions = actions; }
	get visible(): boolean { return Boolean(this.summary()) || this.actions().length > 0; }
	private layout(width: number): { rows: string[]; targets: { x: number; y: number; size: number; action: ActivityAction }[] } {
		if (width <= 0) return { rows: [], targets: [] };
		const rows: string[] = [];
		const targets: { x: number; y: number; size: number; action: ActivityAction }[] = [];
		let row = "";
		for (const action of this.actions()) {
			const label = truncateToWidth(`[${action.label}]`, width);
			if (row && visibleWidth(row) + 1 + visibleWidth(label) > width) { rows.push(row); row = ""; }
			if (row) row += " ";
			targets.push({ x: visibleWidth(row), y: rows.length, size: visibleWidth(label), action });
			row += theme.accentBold(label);
		}
		const summary = sanitizeDisplay(this.summary());
		if (summary) {
			const text = theme.muted(" " + summary + "  ") + theme.dim("Ctrl+N A details");
			if (row && width - visibleWidth(row) < 24) { rows.push(row); row = ""; }
			row += (row ? "  " : "") + truncateToWidth(text, Math.max(0, width - visibleWidth(row) - (row ? 2 : 0)));
		}
		if (row) rows.push(truncateToWidth(row, width));
		return { rows, targets };
	}
	render(width: number): string[] {
		return this.layout(width).rows;
	}
	handleMouse(event: TuiMouseEvent): { handled: boolean; render: boolean } | undefined {
		if (event.type !== "click" || event.button !== "left") return undefined;
		const { rows, targets } = this.layout(event.width);
		if (event.y < 0 || event.y >= rows.length) return undefined;
		const target = targets.find(target => target.y === event.y && event.x >= target.x && event.x < target.x + target.size);
		if (target) target.action.run(); else this.open();
		return { handled: true, render: true };
	}
	invalidate(): void {}
}
