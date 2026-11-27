// Full-screen /usage dashboard, modeled on Codex's Usage page: a small
// centered content column under left-aligned title tabs, with an identity
// block, five headline stats, a 12-month activity view, and two insight
// columns. 1/2 (or Tab) switch tabs, g cycles daily → weekly → cumulative,
// r refreshes, and Esc closes.

import {
	Key,
	matchesKey,
	truncateToWidth,
	visibleWidth,
	type Component,
	type TUI,
} from "@earendil-works/pi-tui";
import type { UsageDailyPoint, UsageReportResult, UsageSkillRow } from "../protocol.ts";
import { theme } from "../theme.ts";

type Tab = "usage" | "skills";
type Granularity = 0 | 1 | 2;

const TAB_LABELS: ReadonlyArray<{ id: Tab; label: string }> = [
	{ id: "usage", label: "Usage" },
	{ id: "skills", label: "Skills" },
];

const MODES = ["Daily", "Weekly", "Cumulative"] as const;
const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"] as const;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;
const CONTENT_MAX = 109;

const CHART_ROWS = 8;

export interface UsagePageOptions {
	tui: Pick<TUI, "terminal" | "requestRender">;
	onClose: () => void;
	onRefresh: () => void;
}

function compact(value: number): string {
	if (!Number.isFinite(value) || value <= 0) return "0";
	return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function duration(seconds: number): string {
	const total = Math.max(0, Math.floor(seconds));
	if (total < 60) return total ? `${total}s` : "0m";
	const minutes = Math.floor(total / 60);
	if (minutes < 60) return `${minutes}m`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) {
		const rest = minutes % 60;
		return rest ? `${hours}h ${rest}m` : `${hours}h`;
	}
	const days = Math.floor(hours / 24);
	const rest = hours % 24;
	return rest ? `${days}d ${rest}h` : `${days}d`;
}

function days(value: number): string {
	return `${Math.max(0, Math.floor(value))} ${value === 1 ? "day" : "days"}`;
}

function center(text: string, width: number): string {
	const w = visibleWidth(text);
	if (w >= width) return truncateToWidth(text, width);
	const left = Math.floor((width - w) / 2);
	return `${" ".repeat(left)}${text}${" ".repeat(Math.max(0, width - w - left))}`;
}

function padRight(text: string, width: number): string {
	const w = visibleWidth(text);
	if (w >= width) return truncateToWidth(text, width);
	return `${text}${" ".repeat(width - w)}`;
}

function padLeft(text: string, width: number): string {
	const w = visibleWidth(text);
	if (w >= width) return truncateToWidth(text, width);
	return `${" ".repeat(width - w)}${text}`;
}

function parseDay(value: string): Date {
	const [year, month, day] = value.split("-").map(Number);
	return new Date(year ?? 1970, (month ?? 1) - 1, day ?? 1);
}

function dayKey(value: Date): string {
	const pad = (n: number) => String(n).padStart(2, "0");
	return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
}

function intensity(value: number, maximum: number): number {
	if (value <= 0 || maximum <= 0) return 0;
	return Math.max(1, Math.min(4, Math.ceil(Math.sqrt(value / maximum) * 4)));
}

export class UsagePage implements Component {
	private readonly options: UsagePageOptions;
	private report: UsageReportResult | undefined;
	private error = "";
	private refreshing = false;
	private tab: Tab = "usage";
	private mode: Granularity = 0;
	private scroll = 0;
	private viewport = 1;

	constructor(options: UsagePageOptions) {
		this.options = options;
	}

	invalidate(): void {}

	setReport(report: UsageReportResult): void {
		this.report = report;
		this.error = "";
		this.refreshing = false;
		this.options.tui.requestRender();
	}

	setError(message: string): void {
		this.error = message;
		this.refreshing = false;
		this.options.tui.requestRender();
	}

	setRefreshing(): void {
		this.refreshing = true;
		this.options.tui.requestRender();
	}

	handleInput(data: string): void {
		if (matchesKey(data, Key.escape) || matchesKey(data, Key.ctrl("c"))) {
			this.options.onClose();
			return;
		}
		if (data === "1") {
			this.tab = "usage";
			this.scroll = 0;
			this.options.tui.requestRender();
			return;
		}
		if (data === "2") {
			this.tab = "skills";
			this.scroll = 0;
			this.options.tui.requestRender();
			return;
		}
		if (matchesKey(data, Key.tab)) {
			this.tab = this.tab === "usage" ? "skills" : "usage";
			this.scroll = 0;
			this.options.tui.requestRender();
			return;
		}
		if (data === "g" || data === "G") {
			this.mode = ((this.mode + 1) % MODES.length) as Granularity;
			this.scroll = 0;
			this.options.tui.requestRender();
			return;
		}
		if (data === "r" || data === "R") {
			if (!this.refreshing) {
				this.setRefreshing();
				this.options.onRefresh();
			}
			return;
		}
		if (matchesKey(data, Key.up)) this.scrollBy(-1);
		else if (matchesKey(data, Key.down)) this.scrollBy(1);
		else if (matchesKey(data, Key.pageUp)) this.scrollBy(-this.viewport);
		else if (matchesKey(data, Key.pageDown)) this.scrollBy(this.viewport);
		else if (matchesKey(data, Key.home)) this.scrollBy(-Number.MAX_SAFE_INTEGER);
		else if (matchesKey(data, Key.end)) this.scrollBy(Number.MAX_SAFE_INTEGER);
	}

	private scrollBy(delta: number): void {
		this.scroll = Math.max(0, this.scroll + delta);
		this.options.tui.requestRender();
	}

	private header(width: number): string {
		const account = this.report?.account;
		const plan = account?.plan ?? "Machina";
		const email = account?.email ? ` · ${account.email}` : "";
		return truncateToWidth(`${theme.bold("Usage")}${theme.dim(` · ${plan}${email}`)}`, width);
	}

	private tabs(width: number): string {
		const parts = TAB_LABELS.map(({ id, label }, index) => {
			const text = ` ${index + 1} ${label} `;
			return id === this.tab
				? theme.bold(theme.toolBg(text))
				: theme.dim(text);
		});
		return truncateToWidth(parts.join(" "), width);
	}

	private usageBody(width: number): string[] {
		const report = this.report;
		if (!report) {
			return [this.error ? theme.error(this.error) : theme.dim("Reading usage…")];
		}
		const account = report.account;
		const lines: string[] = [
			center(theme.bold(account?.name ?? "Machina"), width),
			center(theme.dim(account?.email ?? "all providers"), width),
			center(theme.dim(account?.plan ?? `${report.days} days of activity`), width),
			"",
		];
		lines.push(...this.stats(report, width));
		lines.push("");
		lines.push(theme.bold("Token activity"));
		lines.push(theme.dim("Last 12 months"));
		lines.push("");
		lines.push(...this.activityView(report, width));
		lines.push("");
		lines.push(...this.insightColumns(report, width));
		lines.push("");
		const stamp = new Date(report.generatedAt).toLocaleDateString("en-US", {
			month: "short",
			day: "numeric",
			year: "numeric",
		});
		lines.push(theme.dim(`Statistics as of ${stamp}`));
		return lines;
	}

	private stats(report: UsageReportResult, width: number): string[] {
		const values = [
			compact(report.lifetimeTokens),
			compact(report.peakTokens),
			duration(report.longestChatSeconds),
			days(report.currentStreakDays),
			days(report.longestStreakDays),
		];
		const labels = ["Lifetime tokens", "Peak tokens", "Longest chat", "Current streak", "Longest streak"];
		const column = Math.max(8, Math.floor(width / labels.length));
		const total = column * labels.length;
		const inset = Math.max(0, Math.floor((width - total) / 2));
		const render = (cells: string[], style: (text: string) => string) =>
			`${" ".repeat(inset)}${cells.map((text) => style(center(text, column))).join("")}`.trimEnd();
		return [render(values, theme.bold), render(labels, theme.dim)];
	}

	private weeks(report: UsageReportResult): {
		start: Date;
		last: Date;
		columns: number;
		byDate: Map<string, UsageDailyPoint>;
	} {
		const daily = report.daily.length ? report.daily : [{ date: dayKey(new Date()), tokens: 0 }];
		const first = parseDay(daily[0]!.date);
		const last = parseDay(daily[daily.length - 1]!.date);
		const start = new Date(first);
		start.setDate(start.getDate() - start.getDay());
		const columns = Math.max(
			1,
			Math.ceil((Math.floor((last.getTime() - start.getTime()) / 86_400_000) + 1) / 7),
		);
		const byDate = new Map(daily.map((point) => [point.date, point]));
		return { start, last, columns, byDate };
	}

	private activityView(report: UsageReportResult, width: number): string[] {
		if (this.mode === 0) return this.heatmap(report, width);
		if (this.mode === 2) return this.cumulativeChart(report, width);
		return this.barChart(report, width);
	}

	private cumulativeChart(report: UsageReportResult, width: number): string[] {
		const { start, last, columns, byDate } = this.weeks(report);
		const weekly: number[] = [];
		for (let col = 0; col < columns; col++) {
			let total = 0;
			for (let row = 0; row < 7; row++) {
				const date = new Date(start);
				date.setDate(start.getDate() + col * 7 + row);
				if (date > last) continue;
				total += byDate.get(dayKey(date))?.tokens ?? 0;
			}
			weekly.push(total);
		}
		const cumulative: number[] = [];
		let running = 0;
		for (const total of weekly) {
			running += total;
			cumulative.push(running);
		}
		const maximum = Math.max(1, running);
		const step = 2;
		const visible = Math.min(columns, Math.max(1, Math.floor((width - 4) / step)));
		const offset = columns - visible;
		const shown = cumulative.slice(offset);
		const labels = this.monthLabels(start, last, visible, offset, step);
		const out: string[] = [`   ${theme.dim(labels.join(""))}`.trimEnd()];
		const barRows = CHART_ROWS - 1;
		for (let row = 0; row < barRows; row++) {
			const axis = row === 0 ? "max" : "";
			const cells = shown
				.map((value) => {
					const height = value > 0 ? Math.max(1, Math.round((value / maximum) * barRows)) : 0;
					return height > barRows - row ? `${theme.activity("■", 0.7, 0)} ` : "  ";
				})
				.join("");
			out.push(`${theme.dim(axis.padEnd(3))} ${cells}`.trimEnd());
		}
		// A connected base band under the narrow bars, as in the reference.
		const base = shown.map((value) => (value > 0 ? `${theme.activity("■", 0.7, 0)} ` : "  ")).join("");
		out.push(`${theme.dim("0".padEnd(3))} ${base}`.trimEnd());
		out.push("");
		out.push(theme.dim(`Running total · top ${compact(running)}`));
		return out;
	}

	private monthLabels(
		start: Date,
		last: Date,
		visible: number,
		offset: number,
		step: number,
	): string[] {
		const labels = Array.from({ length: visible * step }, () => " ");
		for (let month = 0; month < 13; month++) {
			const probe = new Date(start.getFullYear(), start.getMonth() + month, 1);
			if (probe < start || probe > last) continue;
			const week = Math.floor(Math.floor((probe.getTime() - start.getTime()) / 86_400_000) / 7) - offset;
			if (week < 0 || week >= visible) continue;
			const col = week * step;
			const label = MONTHS[probe.getMonth()]!;
			const end = col + label.length;
			if (labels.slice(Math.max(0, col - 1), end).some((value) => value !== " ")) continue;
			for (let index = 0; index < label.length && col + index < labels.length; index++) {
				labels[col + index] = label[index]!;
			}
		}
		return labels;
	}

	private heatmap(report: UsageReportResult, width: number): string[] {
		const { start, last, columns, byDate } = this.weeks(report);
		const step = width >= 109 ? 2 : 1;
		const visible = Math.min(columns, Math.max(1, Math.floor((width - 3) / step)));
		const offset = columns - visible;
		const dates: Array<Array<Date | undefined>> = [];
		const values: number[][] = [];
		for (let col = 0; col < visible; col++) {
			const weekStart = new Date(start);
			weekStart.setDate(start.getDate() + (col + offset) * 7);
			const rowDates: Array<Date | undefined> = [];
			const rowValues: number[] = [];
			for (let row = 0; row < 7; row++) {
				const date = new Date(weekStart);
				date.setDate(weekStart.getDate() + row);
				rowDates.push(date > last ? undefined : date);
				rowValues.push(date > last ? 0 : (byDate.get(dayKey(date))?.tokens ?? 0));
			}
			dates.push(rowDates);
			values.push(rowValues);
		}
		const maximum = Math.max(1, ...values.flat());
		const labels = this.monthLabels(start, last, visible, offset, step);

		const out = [truncateToWidth(`   ${theme.dim(labels.join(""))}`.trimEnd(), width)];
		const spacer = " ".repeat(step - 1);
		for (let row = 0; row < 7; row++) {
			const cells = dates.map((week, col) => {
				const day = week[row];
				if (!day) return " ".repeat(step);
				const level = intensity(values[col]![row]!, maximum);
				const glyph = level <= 0 ? theme.border("■") : theme.activity("■", level / 4, 0);
				return `${glyph}${spacer}`;
			});
			out.push(`${theme.dim(WEEKDAYS[row]!.padEnd(2))} ${cells.join("")}`.trimEnd());
		}
		const legend = [theme.border("■"), ...[0.25, 0.5, 0.75, 1].map((level) => theme.activity("■", level, 0))].join(" ");
		out.push("");
		out.push(`${theme.dim("Less")} ${legend}${theme.dim(" More")}`);
		return out;
	}

	private barChart(report: UsageReportResult, width: number): string[] {
		const { start, last, columns, byDate } = this.weeks(report);
		const weekly: number[] = [];
		for (let col = 0; col < columns; col++) {
			let total = 0;
			for (let row = 0; row < 7; row++) {
				const date = new Date(start);
				date.setDate(start.getDate() + col * 7 + row);
				if (date > last) continue;
				total += byDate.get(dayKey(date))?.tokens ?? 0;
			}
			weekly.push(total);
		}
		const values = weekly;
		const maximum = Math.max(1, ...values);
		const step = width >= 109 ? 2 : 1;
		const visible = Math.min(columns, Math.max(1, Math.floor((width - 4) / step)));
		const offset = columns - visible;
		const shown = values.slice(offset);
		const labels = this.monthLabels(start, last, visible, offset, step);
		const out: string[] = [`   ${theme.dim(labels.join(""))}`.trimEnd()];
		const spacer = " ".repeat(step - 1);
		for (let row = 0; row < CHART_ROWS; row++) {
			const axis = row === 0 ? "max" : row === CHART_ROWS - 1 ? "0" : "";
			const cells = shown
				.map((value) => {
					const height = value > 0 ? Math.max(1, Math.round((value / maximum) * CHART_ROWS)) : 0;
					const glyph = height > CHART_ROWS - row - 1 ? theme.activity("■", 0.6, 0) : " ";
					return `${glyph}${spacer}`;
				})
				.join("");
			out.push(`${theme.dim(axis.padEnd(3))} ${cells}`.trimEnd());
		}
		out.push("");
		out.push(theme.dim(`Each column = 1 week · tallest ${compact(maximum)}`));
		return out;
	}

	private insightColumns(report: UsageReportResult, width: number): string[] {
		const gap = 2;
		const left = Math.max(24, Math.floor((width - gap) * 0.5));
		const right = Math.max(12, width - left - gap);
		const topModel = report.models[0];
		const rows: Array<[string, string]> = [
			["Most used model", topModel ? topModel.model : "—"],
			["Total chats", compact(report.totalChats)],
			["Total requests", compact(report.totalRequests)],
			["Skills explored", String(report.skills.explored)],
			["Skill runs", compact(report.skills.uses)],
		];
		const metrics = rows.map(([label, value]) => {
			const room = Math.max(3, left - label.length - 1);
			const shown = truncateToWidth(value, room);
			return `${theme.dim(label)}${" ".repeat(Math.max(1, left - label.length - visibleWidth(shown)))}${shown}`;
		});
		const used = [
			...report.models.slice(0, 3).map((model) => ({
				label: `${model.provider}/${model.model}`,
				runs: model.requests,
			})),
			...report.skills.top.slice(0, 3).map((skill) => ({ label: `@${skill.name}`, runs: skill.uses })),
		].slice(0, 5);
		const modelLines = used.map((entry) => this.usedRow(entry.label, entry.runs, right));
		if (!modelLines.length) modelLines.push(theme.dim("No model or skill calls recorded"));

		const out = [
			`${padRight(theme.bold("Activity insights"), left)}${" ".repeat(gap)}${theme.bold("Models & skills used")}`,
			"",
		];
		const count = Math.max(metrics.length, modelLines.length);
		for (let index = 0; index < count; index++) {
			out.push(`${padRight(metrics[index] ?? "", left)}${" ".repeat(gap)}${modelLines[index] ?? ""}`.trimEnd());
		}
		return out;
	}

	private usedRow(label: string, runs: number, width: number): string {
		const suffix = `${compact(runs)} runs`;
		const room = Math.max(4, width - suffix.length - 1);
		const shown = truncateToWidth(label, room);
		return `${shown}${" ".repeat(Math.max(1, width - visibleWidth(shown) - suffix.length))}${theme.dim(suffix)}`;
	}

	private skillsBody(width: number): string[] {
		const report = this.report;
		if (!report) {
			return [this.error ? theme.error(this.error) : theme.dim("Reading skills…")];
		}
		const skills = report.skills;
		const lines: string[] = [
			center(theme.bold("Skills"), width),
			center(
				theme.dim(
					`${skills.total} installed · ${skills.enabled} enabled · ${skills.explored} explored · ${skills.neverUsed} never used`,
				),
				width,
			),
			center(theme.dim(`${compact(skills.uses)} runs · ${compact(skills.views)} views`), width),
			"",
		];
		lines.push(this.skillsHeader(width));
		lines.push(theme.dim("─".repeat(Math.max(0, width))));
		if (!skills.items.length) {
			lines.push(theme.dim("No skills installed"));
			return lines;
		}
		for (const skill of skills.items) lines.push(this.skillRow(skill, width));
		return lines;
	}

	private skillWidths(width: number): { name: number; uses: number; views: number; source: number } {
		const uses = 7;
		const views = 7;
		const source = Math.max(10, Math.min(20, Math.floor(width * 0.22)));
		const name = Math.max(10, width - uses - views - source - 6);
		return { name, uses, views, source };
	}

	private skillsHeader(width: number): string {
		const w = this.skillWidths(width);
		return [
			padRight(theme.dim("Skill"), w.name),
			padLeft(theme.dim("Uses"), w.uses),
			padLeft(theme.dim("Views"), w.views),
			padLeft(theme.dim("Source"), w.source),
		].join("  ");
	}

	private skillRow(skill: UsageSkillRow, width: number): string {
		const w = this.skillWidths(width);
		const name = skill.enabled ? skill.name : `${skill.name} (off)`;
		return [
			padRight(truncateToWidth(name, w.name), w.name),
			padLeft(compact(skill.uses), w.uses),
			padLeft(compact(skill.views), w.views),
			padLeft(theme.dim(truncateToWidth(skill.sourceType || "—", w.source)), w.source),
		].join("  ");
	}

	private footer(width: number): string[] {
		return [
			truncateToWidth(theme.dim("esc back"), width),
			truncateToWidth(
				theme.dim("tab switch · 1/2 tabs · g daily→weekly→cumulative · r refresh · ↑↓ scroll"),
				width,
			),
		];
	}

	render(width: number): string[] {
		const content = Math.max(20, width);
		const height = Math.max(12, this.options.tui.terminal.rows);
		const innerWidth = Math.min(CONTENT_MAX, Math.max(48, content - 8));
		const inset = Math.max(0, Math.floor((content - innerWidth) / 2));
		const frame = 7; // header, tabs, rule, mode, blank, two footer rows
		const viewport = Math.max(1, height - frame);
		this.viewport = viewport;

		const body = this.tab === "usage" ? this.usageBody(innerWidth) : this.skillsBody(innerWidth);
		let shown: string[];
		if (body.length <= viewport) {
			const top = Math.floor((viewport - body.length) / 2);
			shown = [...Array.from({ length: top }, () => ""), ...body];
			while (shown.length < viewport) shown.push("");
			this.scroll = 0;
		} else {
			this.scroll = Math.max(0, Math.min(this.scroll, body.length - viewport));
			shown = body.slice(this.scroll, this.scroll + viewport);
			while (shown.length < viewport) shown.push("");
		}

		const modeLine = this.tab === "usage"
			? `${theme.accentBold("g")} ${theme.bold(MODES[this.mode]!)}`
			: "";
		const out = [
			this.header(content),
			this.tabs(content),
			theme.dim("─".repeat(content)),
			modeLine,
			"",
			...shown.map((line) => `${" ".repeat(inset)}${truncateToWidth(line, innerWidth)}`),
			...this.footer(content),
		];
		return out.map((line) => truncateToWidth(line, content));
	}
}
