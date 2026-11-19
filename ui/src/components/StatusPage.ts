// Full-screen live command center. A compact grouped activity list on the
// left and a selected-item inspector on the right (stacked on narrow
// terminals). Every row is selectable; Enter/click expands its retained local
// logs. The page reads only bounded local stores and status RPCs and never
// launches or re-runs work.
import {
	Key,
	matchesKey,
	truncateToWidth,
	visibleWidth,
	type Component,
	type TUI,
	type TuiMouseEvent,
} from "@earendil-works/pi-tui";
import { sanitizeDisplay } from "../design/text.ts";
import { selectionBar, theme } from "../theme.ts";

export interface StatusRow {
	/** Stable identity so refresh does not lose selection/expansion. */
	id?: string;
	label: string;
	detail?: string;
	state?: string;
	running?: boolean;
	/** ISO timestamp used for the Updated column. */
	updatedAt?: string;
	project?: string;
	/** Bounded local evidence shown in the inspector after expansion. */
	logs?: string[];
}

export interface StatusSnapshot {
	session: string;
	route: string;
	agents: StatusRow[];
	/** Recent persisted sessions (may include the current session). */
	sessions?: StatusRow[];
	shells: StatusRow[];
	tools: StatusRow[];
	mcp: StatusRow[];
	harnesses: StatusRow[];
	queued: number;
	approvals: number;
	updatedAt: string;
	error?: string;
	project?: string;
	running?: boolean;
	mode?: string;
	tokens?: number;
	contextPercent?: number | null;
}

type StatusFilter = "all" | "sessions" | "agents" | "shells" | "tools" | "mcp" | "harnesses";

const FILTERS: ReadonlyArray<{ id: StatusFilter; label: string }> = [
	{ id: "all", label: "ALL" },
	{ id: "sessions", label: "Sessions" },
	{ id: "agents", label: "Agents" },
	{ id: "shells", label: "Shells" },
	{ id: "tools", label: "Tools" },
	{ id: "mcp", label: "MCP" },
	{ id: "harnesses", label: "Harnesses" },
];

interface PanelLine {
	text: string;
	/** Right-aligned state/count column. */
	right?: string;
	selected?: boolean;
	/** Present only for selectable rows. */
	key?: string;
	row?: StatusRow;
	section?: string;
}

interface PanelModel {
	lines: PanelLine[];
	keys: string[];
}

interface Hitbox {
	x: number;
	width: number;
	y: number;
	key?: string;
	filter?: StatusFilter;
}

function stateStyle(state: string | undefined): (text: string) => string {
	const value = (state ?? "").toLowerCase();
	if (/(running|connected|ok|ready|active|approved|live)/.test(value)) return theme.success;
	if (/(error|failed|denied|disconnected|offline)/.test(value)) return theme.error;
	if (/(pending|queued|paused|starting|idle|inactive|disabled|installed)/.test(value)) return theme.warning;
	return theme.dim;
}

function fit(text: string, width: number): string {
	const clipped = truncateToWidth(text, width, "…", true);
	return clipped + " ".repeat(Math.max(0, width - visibleWidth(clipped)));
}

function between(left: string, right: string, width: number): string {
	const room = Math.max(0, width - visibleWidth(right));
	const first = truncateToWidth(left, room, "…", true);
	const gap = Math.max(0, room - visibleWidth(first));
	return `${first}${" ".repeat(gap)}${truncateToWidth(right, width, "…")}`;
}

function age(iso: string | undefined): string {
	if (!iso) return "";
	const then = new Date(iso).getTime();
	if (!Number.isFinite(then)) return "";
	const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
	if (seconds < 60) return "now";
	if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
	if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
	return `${Math.floor(seconds / 86_400)}d ago`;
}

export class StatusPage implements Component {
	private readonly tui: TUI;
	private readonly snapshot: () => StatusSnapshot;
	private filter: StatusFilter = "all";
	private selectedKey: string | undefined;
	private readonly expanded = new Set<string>();
	private hitboxes: Hitbox[] = [];

	constructor(tui: TUI, snapshot: () => StatusSnapshot) {
		this.tui = tui;
		this.snapshot = snapshot;
	}

	/** Kept as the tab-switch entry point; page swaps are intentionally
	 *  instant now, with no transition timer or intermediate frame. */
	enter(_direction?: 1 | -1): void {
		this.tui.requestRender();
	}

	dispose(): void {
		this.hitboxes = [];
	}

	invalidate(): void {
		this.hitboxes = [];
	}

	/**
	 * Keyboard interaction for the focused page. Returns true when the key
	 * belongs to the page; App still owns Esc and the left/right tab switch.
	 */
	handleInput(data: string): boolean {
		if (matchesKey(data, Key.tab) || data === "\x1b[Z") {
			this.cycleFilter(data === "\x1b[Z" ? -1 : 1);
			return true;
		}
		if (matchesKey(data, Key.up) || matchesKey(data, Key.down)
			|| matchesKey(data, Key.pageUp) || matchesKey(data, Key.pageDown)
			|| matchesKey(data, Key.home) || matchesKey(data, Key.end)) {
			this.moveSelection(data);
			return true;
		}
		if (matchesKey(data, Key.enter) || data === " ") {
			if (!this.selectedKey) this.selectFirst();
			else if (this.expanded.has(this.selectedKey)) this.expanded.delete(this.selectedKey);
			else this.expanded.add(this.selectedKey);
			this.tui.requestRender();
			return true;
		}
		return false;
	}

	handleMouse(event: TuiMouseEvent): { handled: boolean; focus?: boolean; render?: boolean } | undefined {
		if (event.type !== "click" || event.button !== "left") return undefined;
		const hit = this.hitboxes.find(candidate =>
			event.y === candidate.y && event.x >= candidate.x && event.x < candidate.x + candidate.width,
		);
		if (!hit) return undefined;
		if (hit.filter) {
			this.filter = hit.filter;
			this.selectedKey = undefined;
			this.selectFirst();
			this.tui.requestRender();
			return { handled: true, focus: true, render: true };
		}
		if (hit.key) {
			this.selectedKey = hit.key;
			if (this.expanded.has(hit.key)) this.expanded.delete(hit.key);
			else this.expanded.add(hit.key);
			this.tui.requestRender();
			return { handled: true, focus: true, render: true };
		}
		return { handled: true, focus: true };
	}

	render(width: number): string[] {
		const columns = Math.max(1, width);
		const rows = Math.max(8, this.tui.terminal.rows ?? 24);
		let lines: string[];
		try {
			lines = this.build(columns, rows);
		} catch (error) {
			lines = [
				theme.error("Status is unavailable."),
				theme.dim(` ${error instanceof Error ? error.message : String(error)}`),
			];
		}
		while (lines.length < rows) lines.push(" ".repeat(columns));
		return lines.slice(0, rows).map(line => truncateToWidth(line, columns, "", true));
	}

	private build(width: number, rows: number): string[] {
		const snap = this.snapshot();
		const out: string[] = [];
		const push = (line = ""): void => { out.push(line); };
		const live = snap.running ? theme.success("● live") : theme.dim("○ idle");
		push(between(
			` ${theme.accentBold("Machina status")}  ${theme.dim(`Project: ${sanitizeDisplay(snap.project || "global")}`)}`,
			`${live}  ${theme.dim("tab/shift+tab filter")}  ${theme.dim(snap.updatedAt || "refreshing…")} `,
			width,
		));
		const tabs = FILTERS.map((entry) => {
			const count = this.filterCount(snap, entry.id);
			const label = `${entry.label}${entry.id === "all" ? "" : ` ${count}`}`;
			return entry.id === this.filter ? theme.accentBold(label) : theme.dim(label);
		}).join("  ");
		push(truncateToWidth(` ${tabs}`, width, "…", true));
		if (snap.error) push(truncateToWidth(` ${theme.warning(sanitizeDisplay(snap.error))}`, width, "…", true));
		push(theme.border("─".repeat(width)));
		const headerRows = out.length;
		const bodyHeight = Math.max(1, rows - headerRows - 1);
		let panels = this.buildPanels(snap);
		this.ensureSelection(panels);
		// Rebuild after selection so the row marker/selection bar is painted on
		// the first frame, not only after the first navigation key.
		if (this.selectedKey && panels.left.keys.includes(this.selectedKey)) panels = this.buildPanels(snap);
		const selected = panels.left.lines.find(line => line.key === this.selectedKey);
		const detail = this.buildDetail(selected);
		const leftSelected = panels.left.lines.findIndex(line => line.key === this.selectedKey);
		const hasDataRows = panels.left.lines.some(line => line.key && !line.key.startsWith("queue:"));
		const leftOffset = hasDataRows
			? windowStart(leftSelected, bodyHeight, panels.left.lines.length)
			: 0;
		const detailOffset = windowStart(
			detail.lines.findIndex(line => line.key === this.selectedKey),
			bodyHeight,
			detail.lines.length,
		);
		const wide = width >= 84 && bodyHeight >= 10;
		const formatPanelLine = (line: PanelLine | undefined, panelWidth: number): string => {
			if (!line) return " ".repeat(panelWidth);
			const content = line.right ? between(line.text, line.right, panelWidth) : fit(line.text, panelWidth);
			return line.selected ? selectionBar(content, panelWidth) : content;
		};
		this.hitboxes = [];
		if (wide) {
			const leftWidth = Math.max(34, Math.floor((width - 3) * 0.66));
			const rightWidth = Math.max(24, width - leftWidth - 3);
			for (let index = 0; index < bodyHeight; index++) {
				const left = panels.left.lines[leftOffset + index];
				const right = detail.lines[detailOffset + index];
				const leftText = formatPanelLine(left, leftWidth);
				const rightText = formatPanelLine(right, rightWidth);
				push(`${leftText}${theme.border(" │ ")}${rightText}`);
				if (left?.key) this.hitboxes.push({ x: 0, width: leftWidth, y: headerRows + index, key: left.key });
			}
		} else {
			const stacked = [...panels.left.lines, ...detail.lines];
			const selectedAt = stacked.findIndex(line => line.key === this.selectedKey);
			const offset = windowStart(selectedAt, bodyHeight, stacked.length);
			for (let index = 0; index < bodyHeight; index++) {
				const line = stacked[offset + index];
				push(formatPanelLine(line, width));
				if (line?.key) this.hitboxes.push({ x: 0, width, y: headerRows + index, key: line.key });
			}
		}
		// Filter tabs are clickable.
		let tabX = 1;
		for (const entry of FILTERS) {
			const count = this.filterCount(snap, entry.id);
			const label = `${entry.label}${entry.id === "all" ? "" : ` ${count}`}`;
			this.hitboxes.push({ x: tabX, width: visibleWidth(label), y: 1, filter: entry.id });
			tabX += visibleWidth(label) + 2;
		}
		while (out.length < rows - 1) push(" ".repeat(width));
		push(truncateToWidth(
			` ${theme.dim("↑↓ move · Enter inspect/logs · Tab filter · Esc chat · refresh 3s")}`,
			width,
			"…",
			true,
		));
		return out.slice(0, rows);
	}

	private buildPanels(snap: StatusSnapshot): { left: PanelModel } {
		const all = this.filter === "all";
		const sections: Array<{ title: string; entries: StatusRow[]; empty: string; category: StatusFilter }> = [];
		if (all || this.filter === "sessions") {
			sections.push({
				title: "Sessions",
				entries: snap.sessions ?? [],
				empty: "No persisted sessions yet.",
				category: "sessions",
			});
		}
		if (all || this.filter === "agents") {
			sections.push({ title: "Agents", entries: snap.agents, empty: "No active agents.", category: "agents" });
		}
		if (all || this.filter === "shells") {
			sections.push({ title: "Shells", entries: snap.shells, empty: "No foreground or background shells.", category: "shells" });
		}
		if (all || this.filter === "tools") {
			sections.push({ title: "Tools / MCP", entries: snap.tools, empty: "No recent tool calls.", category: "tools" });
		}
		if (all || this.filter === "harnesses") {
			sections.push({ title: "Harnesses", entries: snap.harnesses, empty: "No external harnesses detected.", category: "harnesses" });
		}
		if (all || this.filter === "mcp") {
			sections.push({ title: "MCP servers", entries: snap.mcp, empty: "No MCP servers configured.", category: "mcp" });
		}
		if (all) {
			sections.push({
				title: "Queues",
				entries: [{
					id: "queue:summary",
					label: "queued guidance",
					detail: `${snap.queued} · pending approvals ${snap.approvals}`,
					state: snap.queued ? "queued" : "idle",
					logs: [
						`Queued guidance: ${snap.queued}`,
						`Pending approvals: ${snap.approvals}`,
					],
				}],
				empty: "No queued guidance.",
				category: "all",
			});
		}
		return { left: this.panel("Activity / Sessions", sections) };
	}

	private panel(title: string, sections: Array<{ title: string; entries: StatusRow[]; empty: string; category: StatusFilter }>): PanelModel {
		const lines: PanelLine[] = [{ text: theme.accentBold(title), right: theme.dim("Status      Updated") }];
		const keys: string[] = [];
		for (const section of sections) {
			lines.push({
				text: theme.accentBold(`~${section.title}`),
				right: theme.dim(String(section.entries.length)),
			});
			if (!section.entries.length) {
				lines.push({ text: `  ${theme.dim(section.empty)}` });
				continue;
			}
			for (const [index, entry] of section.entries.entries()) {
				const key = entry.id ?? `${section.category}:${index}:${entry.label}`;
				const selected = key === this.selectedKey;
				const marker = entry.running ? theme.success("●") : theme.dim("○");
				const detail = entry.detail ? theme.muted(` · ${sanitizeDisplay(entry.detail)}`) : "";
				const state = entry.state ? stateStyle(entry.state)(sanitizeDisplay(entry.state)) : "";
				const updated = age(entry.updatedAt);
				const right = [state, updated ? theme.dim(updated) : ""].filter(Boolean).join("  ");
				const arrow = entry.logs?.length
					? (this.expanded.has(key) ? theme.accent("▾") : theme.dim("▸"))
					: " ";
				lines.push({
					text: `${selected ? theme.accent("›") : " "} ${marker} ${theme.text(sanitizeDisplay(entry.label))}${detail} ${arrow}`,
					right,
					selected,
					key,
					row: entry,
					section: section.title,
				});
				keys.push(key);
			}
		}
		return { lines, keys };
	}

	private buildDetail(selected: PanelLine | undefined): PanelModel {
		const row = selected?.row;
		const lines: PanelLine[] = [{ text: theme.accentBold("Task details") }];
		if (!row) {
			lines.push({ text: theme.dim("Select an activity row to inspect it.") });
			return { lines, keys: [] };
		}
		lines.push({ text: theme.accent(sanitizeDisplay(row.label)) });
		const meta: Array<[string, string]> = [
			["Status", row.state || "unknown"],
			["Updated", age(row.updatedAt) || "unknown"],
			["Section", selected.section || "activity"],
			["Project", row.project || this.snapshot().project || "global"],
			["Detail", row.detail || "none"],
		];
		lines.push({ text: "" });
		for (const [label, value] of meta) {
			lines.push({ text: `${theme.dim(label.padEnd(8))} ${sanitizeDisplay(value)}` });
		}
		lines.push({ text: "" });
		const expanded = this.expanded.has(selected.key ?? "");
		if (!expanded) {
			const snap = this.snapshot();
			const onlineMcp = snap.mcp.filter(entry => entry.running).length;
			const liveHarnesses = snap.harnesses.filter(entry => entry.running).length;
			lines.push({
				text: `${theme.dim("Runtime")}  MCP ${onlineMcp}/${snap.mcp.length} · harnesses ${liveHarnesses}/${snap.harnesses.length}`,
			});
			lines.push({
				text: `         ${snap.queued} queued · ${snap.approvals} approval${snap.approvals === 1 ? "" : "s"}`,
			});
			lines.push({ text: "" });
		}
		lines.push({ text: theme.border("─".repeat(32)) });
		lines.push({ text: theme.accentBold("Logs") });
		if (row.logs?.length) {
			const shown = expanded ? row.logs.slice(0, 40) : row.logs.slice(0, 2);
			for (const log of shown) {
				for (const part of sanitizeDisplay(log).split("\n")) {
					lines.push({ text: `  ${theme.dim(truncateToWidth(part, 96, "…"))}` });
				}
			}
			if (!expanded && row.logs.length > 2) {
				lines.push({ text: theme.dim("  … Enter or click for full logs") });
			}
		} else {
			lines.push({ text: theme.dim("No retained logs for this row.") });
		}
		return { lines, keys: [] };
	}

	private ensureSelection(panels: { left: PanelModel }): void {
		const keys = panels.left.keys;
		if (!keys.length) {
			this.selectedKey = undefined;
			return;
		}
		if (!this.selectedKey || !keys.includes(this.selectedKey)) this.selectedKey = keys[0];
	}

	/** Choose the first real activity row, falling back to the queue summary. */
	selectDefault(): void {
		const panels = this.buildPanels(this.snapshot());
		this.selectedKey = panels.left.keys.find(key => !key.startsWith("queue:")) ?? panels.left.keys[0];
		this.tui.requestRender();
	}

	private selectFirst(): void {
		const panels = this.buildPanels(this.snapshot());
		this.selectedKey = panels.left.keys[0];
	}

	private moveSelection(data: string): void {
		const panels = this.buildPanels(this.snapshot());
		const keys = panels.left.keys;
		if (!keys.length) return;
		const current = this.selectedKey ? keys.indexOf(this.selectedKey) : -1;
		let next = current;
		if (matchesKey(data, Key.up)) next -= 1;
		else if (matchesKey(data, Key.down)) next += 1;
		else if (matchesKey(data, Key.pageUp)) next -= 10;
		else if (matchesKey(data, Key.pageDown)) next += 10;
		else if (matchesKey(data, Key.home)) next = 0;
		else if (matchesKey(data, Key.end)) next = keys.length - 1;
		this.selectedKey = keys[Math.max(0, Math.min(keys.length - 1, next))];
		this.tui.requestRender();
	}

	private cycleFilter(direction: 1 | -1): void {
		const current = FILTERS.findIndex(entry => entry.id === this.filter);
		const next = (current + direction + FILTERS.length) % FILTERS.length;
		this.filter = FILTERS[next]!.id;
		this.selectedKey = undefined;
		this.selectFirst();
		this.tui.requestRender();
	}

	private filterCount(snap: StatusSnapshot, filter: StatusFilter): number {
		switch (filter) {
			case "sessions": return snap.sessions?.length ?? 0;
			case "agents": return snap.agents.length;
			case "shells": return snap.shells.length;
			case "tools": return snap.tools.length;
			case "mcp": return snap.mcp.length;
			case "harnesses": return snap.harnesses.length;
			case "all": return (snap.sessions?.length ?? 0) + snap.agents.length + snap.shells.length + snap.tools.length + snap.mcp.length + snap.harnesses.length;
		}
	}
}

function windowStart(selected: number, height: number, length: number): number {
	if (length <= height) return 0;
	if (selected < 0) return 0;
	return Math.max(0, Math.min(length - height, selected - Math.floor(height / 2)));
}
