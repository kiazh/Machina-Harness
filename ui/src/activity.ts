import { truncateToWidth, visibleWidth, type TuiMouseEvent } from "@earendil-works/pi-tui";
import { theme } from "./theme.ts";
import { sanitizeDisplay } from "./design/text.ts";

export interface SteeringItem { id: string; text: string }
export interface ShellJob { id: string; command: string; status: string; returncode?: number | null; output?: string; truncated?: boolean; foreground?: boolean; deadline?: number | null }
export interface ToolActivity {
	id: string;
	sessionId: string;
	name: string;
	status: "running" | "ok" | "error" | "paused" | "unknown";
	startedAt: number;
	completedAt?: number;
	/** Bounded local copy for the status page; never re-executed or refetched. */
	args?: Record<string, unknown>;
	result?: string;
	outcome?: string;
}
export interface ActivityAction { label: string; run: () => void }

const MAX_TOOL_ARG_CHARS = 4_000;

/** Keep status evidence bounded without losing the connector/tool identity. */
function boundedToolArgs(args: Record<string, unknown>): Record<string, unknown> {
	let serialized: string;
	try {
		serialized = JSON.stringify(args);
	} catch {
		return { _unserializable: true };
	}
	if (serialized.length <= MAX_TOOL_ARG_CHARS) return { ...args };
	return {
		...(typeof args.name === "string" ? { name: args.name } : {}),
		_truncated: true,
		_preview: serialized.slice(0, MAX_TOOL_ARG_CHARS - 100),
	};
}

/**
 * What to show when `/activity` has nothing to inspect. Activity is created
 * by ordinary work, so the empty state has to teach the next action instead
 * of looking broken.
 */
export function activityEmptyGuidance(hasSession: boolean): string {
	const intro = hasSession
		? "Activity is empty. No queued prompts or background commands, and no delegated agents."
		: "No active session. Send a message first.";
	return [
		intro,
		"",
		hasSession ? "To create activity:" : "Then activity will appear here:",
		"  1. Press Enter while a reply is running to queue guidance for the next tool boundary.",
		"  2. Ask for a long-running command; it appears here as a shell job.",
		"  3. Use /delegate <task> to start a subagent.",
		"",
		"Open this view anytime with /activity or Ctrl+N then A.",
	].join("\n");
}

export class ActivityStore {
	readonly queues = new Map<string, SteeringItem[]>();
	private readonly queueVersions = new Map<string, number>();
	readonly jobs = new Map<string, Map<string, ShellJob>>();
	readonly toolEvents = new Map<string, Map<string, ToolActivity>>();
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
	allJobs(): Array<{ sessionId: string; job: ShellJob }> {
		return [...this.jobs.entries()].flatMap(([sessionId, jobs]) =>
			[...jobs.values()].map((job) => ({ sessionId, job })),
		);
	}
	queuedCount(): number {
		return [...this.queues.values()].reduce((total, items) => total + items.length, 0);
	}
	noteToolStart(sessionId: string, id: string, name: string, args: Record<string, unknown> = {}): void {
		const tools = this.toolEvents.get(sessionId) ?? new Map<string, ToolActivity>();
		tools.set(id, {
			id,
			sessionId,
			name,
			status: "running",
			startedAt: Date.now(),
			args: boundedToolArgs(args),
		});
		while (tools.size > 32) tools.delete(tools.keys().next().value!);
		this.toolEvents.set(sessionId, tools);
	}
	noteToolComplete(
		sessionId: string,
		id: string,
		status: ToolActivity["status"] = "ok",
		result = "",
		outcome = "",
	): void {
		const tools = this.toolEvents.get(sessionId);
		const record = tools?.get(id);
		if (!record) return;
		record.status = status;
		record.completedAt = Date.now();
		record.result = result.slice(0, 4_000);
		record.outcome = outcome;
	}
	allTools(limit = 24): ToolActivity[] {
		return [...this.toolEvents.values()]
			.flatMap((tools) => [...tools.values()])
			.sort((left, right) => right.startedAt - left.startedAt)
			.slice(0, Math.max(0, limit));
	}
	/** Drop every dormant per-session queue/job/tool record for one session. */
	pruneSession(sessionId: string): void {
		this.queues.delete(sessionId);
		this.queueVersions.delete(sessionId);
		this.jobs.delete(sessionId);
		this.toolEvents.delete(sessionId);
	}
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
		const separator = theme.dim(" · ");
		for (const action of this.actions()) {
			const label = truncateToWidth(action.label, width);
			if (row && visibleWidth(row) + visibleWidth(separator) + visibleWidth(label) > width) {
				rows.push(row);
				row = "";
			}
			const prefix = row ? visibleWidth(separator) : 0;
			targets.push({ x: visibleWidth(row) + prefix, y: rows.length, size: visibleWidth(label), action });
			row += row ? `${separator}${theme.accentBold(label)}` : theme.accentBold(label);
		}
		const summary = sanitizeDisplay(this.summary());
		if (summary) {
			const text = ` ${theme.muted(summary)} ${theme.dim("· Ctrl+N A")}`;
			if (row && width - visibleWidth(row) < 24) { rows.push(row); row = ""; }
			row += (row ? "  " : "") + truncateToWidth(text, Math.max(0, width - visibleWidth(row) - (row ? 2 : 0)));
		}
		if (row) rows.push(truncateToWidth(row, width));
		return { rows, targets };
	}
	render(width: number): string[] {
		return this.layout(width).rows;
	}
	/** Row count without a second full render when chrome is measured again. */
	measureRows(width: number): number {
		return this.layout(width).rows.length;
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
