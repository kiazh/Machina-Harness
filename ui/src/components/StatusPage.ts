// A live work index with a separate evidence view for the selected item.
import {
	Key,
	matchesKey,
	truncateToWidth,
	visibleWidth,
	type Component,
	type TUI,
	type TuiMouseEvent,
} from "@earendil-works/pi-tui";
import { inspectLayout, selectionWindow } from "../design/inspect.ts";
import { sanitizeDisplay, wrapDisplay } from "../design/text.ts";
import { selectionBar, theme } from "../theme.ts";
import { describeError, uiLog } from "../debug.ts";

const log = uiLog.scope("status");

export interface StatusRow {
	/** Stable identity so refresh does not lose selection/expansion. */
	id?: string;
	label: string;
	detail?: string;
	state?: string;
	/** ISO timestamp used for the updated column. */
	updatedAt?: string;
	/** Bounded local evidence shown under the selected row. */
	logs?: string[];
	/** Actual output when the retained log also contains setup and bookkeeping. */
	preview?: string[];
}

export interface StatusSnapshot {
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
}

type StatusFilter = "all" | "sessions" | "agents" | "shells" | "tools" | "mcp" | "harnesses";

const FILTERS: ReadonlyArray<{ id: StatusFilter; label: string }> = [
	{ id: "all", label: "All" },
	{ id: "sessions", label: "Sessions" },
	{ id: "agents", label: "Agents" },
	{ id: "shells", label: "Shells" },
	{ id: "tools", label: "Tools" },
	{ id: "mcp", label: "MCP" },
	{ id: "harnesses", label: "Harnesses" },
];

interface ActivityEntry {
	key: string;
	row: StatusRow;
	section: string;
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
	if (/(error|failed|denied|disconnected|offline)/.test(value)) return theme.error;
	if (/(pending|queued|paused|starting)/.test(value)) return theme.warning;
	if (/\b(running|connected|ok|ready|active|approved|live|passed)\b/.test(value) && !/\bnot\b/.test(value)) return theme.success;
	return theme.muted;
}

function between(left: string, right: string, width: number): string {
	const room = Math.max(0, width - visibleWidth(right) - 1);
	const first = truncateToWidth(left, room, "…");
	const gap = Math.max(0, room - visibleWidth(first));
	return `${first}${" ".repeat(gap)} ${truncateToWidth(right, width, "…")}`;
}

function lineText(text: string): string {
	return sanitizeDisplay(text).replace(/\s+/g, " ").trim();
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
	private readonly onOpen?: (row: StatusRow, section: string | undefined) => boolean;
	private filter: StatusFilter = "all";
	private viewportRows: number | undefined;
	private selectedKey: string | undefined;
	private logScroll = 0;
	private logPage = { size: 1, max: 0 };
	private readonly expanded = new Set<string>();
	private hitboxes: Hitbox[] = [];
	private wheelRegions: Array<{ x: number; width: number; y0: number; y1: number; kind: "list" | "detail" }> = [];

	constructor(
		tui: TUI,
		snapshot: () => StatusSnapshot,
		onOpen?: (row: StatusRow, section: string | undefined) => boolean,
	) {
		this.tui = tui;
		this.snapshot = snapshot;
		this.onOpen = onOpen;
	}

	setViewportRows(rows: number): void {
		this.viewportRows = rows;
	}

	dispose(): void {
		this.invalidate();
	}

	invalidate(): void {
		this.hitboxes = [];
		this.wheelRegions = [];
	}

	handleInput(data: string): boolean {
		const digit = /^[1-7]$/.test(data) ? Number(data) - 1 : -1;
		if (digit >= 0) {
			const entry = FILTERS[digit];
			if (entry) {
				this.filter = entry.id;
				this.selectedKey = undefined;
				this.selectFirst();
				this.tui.requestRender();
				return true;
			}
		}
		if (matchesKey(data, Key.tab) || data === "\x1b[Z") {
			this.cycleFilter(data === "\x1b[Z" ? -1 : 1);
			return true;
		}
		if (matchesKey(data, Key.pageUp) || matchesKey(data, Key.pageDown)) {
			if (this.scrollSelectedLogs(matchesKey(data, Key.pageUp) ? -1 : 1)) {
				this.tui.requestRender();
				return true;
			}
		}
		if (matchesKey(data, Key.up) || matchesKey(data, Key.down)
			|| matchesKey(data, Key.pageUp) || matchesKey(data, Key.pageDown)
			|| matchesKey(data, Key.home) || matchesKey(data, Key.end)) {
			this.moveSelection(data);
			return true;
		}
		if (matchesKey(data, Key.enter)) {
			if (!this.selectedKey) this.selectFirst();
			const selected = this.rows().find(line => line.key === this.selectedKey);
			if (selected?.row && this.onOpen?.(selected.row, selected.section)) {
				this.tui.requestRender();
				return true;
			}
			if (this.selectedKey) {
				if (this.expanded.has(this.selectedKey)) this.expanded.delete(this.selectedKey);
				else this.expanded.add(this.selectedKey);
				this.logScroll = 0;
			}
			this.tui.requestRender();
			return true;
		}
		if (data === " ") {
			if (!this.selectedKey) this.selectFirst();
			else if (this.expanded.has(this.selectedKey)) this.expanded.delete(this.selectedKey);
			else this.expanded.add(this.selectedKey);
			this.logScroll = 0;
			this.tui.requestRender();
			return true;
		}
		return false;
	}

	handleMouse(event: TuiMouseEvent): { handled: boolean; focus?: boolean; render?: boolean } | undefined {
		if (event.type === "wheel") {
			const delta = event.wheelDelta ?? 0;
			if (!delta) return undefined;
			const region = this.wheelRegions.find(area =>
				event.x >= area.x && event.x < area.x + area.width && event.y >= area.y0 && event.y < area.y1);
			if (!region) return undefined;
			if (region.kind === "list") {
				this.moveSelection(delta > 0 ? "\x1b[B" : "\x1b[A");
				return { handled: true, render: true };
			}
			if (this.selectedKey && !this.expanded.has(this.selectedKey) && delta > 0) {
				this.expanded.add(this.selectedKey);
				this.logScroll = 0;
			} else if (this.scrollSelectedLogs(delta > 0 ? 1 : -1)) {
				// Paged the retained output.
			} else {
				return undefined;
			}
			this.tui.requestRender();
			return { handled: true, render: true };
		}
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
			if (this.selectedKey !== hit.key) {
				this.selectedKey = hit.key;
				this.logScroll = 0;
			}
			this.tui.requestRender();
			return { handled: true, focus: true, render: true };
		}
		return undefined;
	}

	render(width: number): string[] {
		const columns = Math.max(1, width);
		const rows = Math.max(1, this.viewportRows ?? this.tui.terminal.rows ?? 24);
		let lines: string[];
		try {
			lines = this.build(columns, rows);
		} catch (error) {
			log.error("render.failed", { error: describeError(error) });
			lines = [
				theme.error("Activity is unavailable."),
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
		push(between(
			`  ${theme.bold(theme.text("Recent work"))}`,
			width >= 62 ? theme.muted(snap.updatedAt ? `Updated ${lineText(snap.updatedAt)}  ` : "Refreshing…  ") : "",
			width,
		));

		const filters = FILTERS.map((entry, index) => {
			const count = this.filterCount(snap, entry.id);
			const active = entry.id === this.filter;
			const short = width < 52 && rows < 14;
			const name = short && entry.label.length > 5 ? entry.label.slice(0, 4) : entry.label;
			const label = `${index + 1} ${name}${width >= 65 && count ? ` ${count}` : ""}`;
			const plain = active ? `[${label}]` : ` ${label} `;
			return {
				entry,
				plain,
				text: active ? selectionBar(plain, visibleWidth(plain)) : theme.text(plain),
			};
		});
		const filterRows: Array<{
			text: string;
			pieces: Array<{ entry: StatusFilter; x: number; width: number }>;
			plainWidth: number;
		}> = [];
		for (const piece of filters) {
			const pieceWidth = visibleWidth(piece.plain);
			let row = filterRows.at(-1);
			const nextWidth = row ? row.plainWidth + 1 + pieceWidth : pieceWidth;
			if (!row || nextWidth > Math.max(1, width - 1)) {
				row = { text: "", pieces: [], plainWidth: 0 };
				filterRows.push(row);
			}
			const x = 1 + (row.pieces.length ? row.plainWidth + 1 : 0);
			row.text += `${row.pieces.length ? " " : ""}${piece.text}`;
			row.plainWidth += (row.pieces.length ? 1 : 0) + pieceWidth;
			row.pieces.push({ entry: piece.entry.id, x, width: pieceWidth });
		}
		this.hitboxes = [];
		this.wheelRegions = [];
		const filterTop = out.length;
		for (const [rowIndex, row] of filterRows.entries()) {
			push(truncateToWidth(` ${row.text}`, width, "…", true));
			for (const piece of row.pieces) {
				this.hitboxes.push({
					x: piece.x,
					width: piece.width,
					y: filterTop + rowIndex,
					filter: piece.entry,
				});
			}
		}
		if (snap.error) {
			push(truncateToWidth(` ${theme.warning("!")} ${theme.warning(lineText(snap.error))}`, width, "…", true));
		}
		if (rows >= 12) push("");

		const bottom = Math.max(out.length, rows - 1);
		const bodyTop = out.length;
		const height = Math.max(1, bottom - out.length);
		const layout = inspectLayout(width, height);
		const entries = this.rows(snap);
		const selected = entries.find(item => item.key === this.selectedKey) ?? entries[0];
		if (this.selectedKey !== selected?.key) this.logScroll = 0;
		this.selectedKey = selected?.key;
		if (!selected) {
			push(`  ${theme.text(this.filter === "all" ? "No recent activity." : "No activity in this category.")}`);
			push(`  ${theme.dim(this.filter === "all" ? "New sessions, tools and shell work appear here." : "Press 1 to see all activity.")}`);
		} else {
			const index: Array<ActivityEntry | string> = [];
			for (const entry of entries) {
				const previous = index.at(-1);
				if (layout.split && (typeof previous === "string" || previous?.section !== entry.section)) index.push(entry.section);
				index.push(entry);
			}
			const start = selectionWindow(index.indexOf(selected), layout.listRows, index.length);
			const list = index.slice(start, start + layout.listRows);
			const gap = layout.split || height <= 5 ? 0 : 1;
			const detailHeight = Math.max(1, height - (layout.split ? 0 : list.length + gap));
			const detail = this.detail(selected, layout.detailWidth, detailHeight, !layout.split);
			const listRow = (offset: number, y: number): string => {
				const line = list[offset];
				if (!line) return "";
				if (typeof line === "string") return theme.bold(theme.text(` ${line}`));
				this.hitboxes.push({ x: layout.inset, width: layout.listWidth, y, key: line.key });
				const current = line === selected;
				const state = lineText(line.row.state ?? "");
				const label = between(`${current ? "❯" : " "} ${lineText(line.row.label)}`,
					current ? state : stateStyle(line.row.state)(state), layout.listWidth);
				return current ? selectionBar(label, layout.listWidth) : theme.text(label);
			};
			const listBottom = layout.split ? bottom : Math.min(bottom, bodyTop + list.length);
			this.wheelRegions = layout.split
				? [
					{ x: layout.inset, width: layout.listWidth, y0: bodyTop, y1: bottom, kind: "list" },
					{ x: layout.inset + layout.listWidth + 3, width: layout.detailWidth, y0: bodyTop, y1: bottom, kind: "detail" },
				]
				: [
					{ x: layout.inset, width: layout.listWidth, y0: bodyTop, y1: listBottom, kind: "list" },
					{ x: layout.inset, width: layout.listWidth, y0: listBottom, y1: bottom, kind: "detail" },
				];
			if (layout.split) {
				for (let i = 0; i < height; i++) push(layout.combine(listRow(i, out.length), detail[i]));
			} else {
				for (let i = 0; i < list.length; i++) push(layout.combine(listRow(i, out.length)));
				if (gap) push("");
				for (const line of detail) if (out.length < bottom) push(layout.combine("", line));
			}
		}
		while (out.length < bottom) push("");
		const help = selected?.row.logs?.length ? "↑↓ Select   Space Output   PgUp/PgDn Read" : "↑↓ Select   Enter Open";
		push(`  ${theme.muted(`${help}   ? Keys`)}`);
		return out;
	}

	private detail(entry: ActivityEntry, width: number, height: number, stacked: boolean): string[] {
		const row = entry.row;
		const updated = age(row.updatedAt);
		const name = lineText(row.label);
		const titles = wrapDisplay(name, Math.min(width, 80)).slice(0, height >= 8 ? 2 : 1);
		const lines = titles.map(title => theme.bold(theme.text(title)));
		if (height >= 3) {
			const state = stateStyle(row.state)(lineText(row.state ?? "unknown"));
			lines.push(`${stacked ? `${theme.muted(entry.section)} · ` : ""}${state}${updated ? theme.muted(` · ${updated}`) : ""}`);
		}
		if (row.detail && height >= 6) lines.push(theme.text(lineText(row.detail)));
		const logs = row.logs ?? [];
		if (!logs.length) {
			this.logPage = { size: 1, max: 0 };
			if (height >= 5) lines.push("", theme.muted("No additional details."));
			return lines;
		}
		const expanded = this.expanded.has(entry.key);
		const output = Boolean(row.preview) || entry.section === "Shells";
		const heading = output ? (expanded ? "Retained output" : "Output preview") : "Details";
		if (height - lines.length >= 5) lines.push("", theme.bold(theme.text(heading)));
		const source = !expanded && row.preview ? row.preview : logs;
		const capacity = Math.max(1, height - lines.length);
		const wrapped: string[] = [];
		for (const raw of source) {
			wrapped.push(...wrapDisplay(raw, width));
			if (!expanded && wrapped.length > Math.min(3, capacity)) break;
		}
		if (!wrapped.length) {
			lines.push(theme.muted("No output yet. Space shows details."));
			return lines;
		}
		const more = wrapped.length > Math.min(3, capacity) || Boolean(row.preview);
		const spaceForLogs = Math.max(1, capacity - Number(expanded ? wrapped.length > Math.min(40, capacity) : more));
		const size = expanded ? Math.min(40, spaceForLogs) : Math.min(3, spaceForLogs);
		const max = expanded ? Math.max(0, wrapped.length - size) : 0;
		const first = expanded ? Math.min(this.logScroll, max) : 0;
		this.logPage = { size, max };
		lines.push(...wrapped.slice(first, first + size).map(line => theme.text(line)));
		if (capacity > size && (expanded ? max > 0 : more)) lines.push(theme.muted(expanded
			? `${first + 1}–${Math.min(first + size, wrapped.length)} of ${wrapped.length} lines · PgUp/PgDn`
			: row.preview ? "Space shows full details" : "More lines · Space to expand"));
		return lines;
	}

	/** Selectable rows in reading order (shared by keys and the viewport). */
	private rows(snap = this.snapshot()): ActivityEntry[] {
		const groups = [
			["sessions", "Sessions", snap.sessions ?? []],
			["agents", "Agents", snap.agents],
			["shells", "Shells", snap.shells],
			["tools", "Tools", snap.tools],
			["mcp", "MCP", snap.mcp],
			["harnesses", "Harnesses", snap.harnesses],
		] as const;
		const entries: ActivityEntry[] = groups.flatMap(([filter, section, items]) => this.filter === "all" || this.filter === filter
			? items.map((row, i) => ({ key: row.id ?? `${filter}:${i}:${row.label}`, row, section }))
			: []);
		if (this.filter === "all" && (snap.queued || snap.approvals)) {
			entries.push({ key: "queue:summary", section: "Queue", row: {
				label: "queued guidance", state: "queued",
				logs: [`Queued guidance: ${snap.queued}`, `Pending approvals: ${snap.approvals}`],
			} });
		}
		return entries;
	}

	selectDefault(): void {
		const rows = this.rows();
		this.selectedKey = rows.find(line => line.key && !line.key.startsWith("queue:"))?.key
			?? rows.find(line => line.key)?.key;
		this.logScroll = 0;
		this.tui.requestRender();
	}

	private selectFirst(): void {
		this.selectedKey = this.rows().find(line => line.key)?.key;
		this.logScroll = 0;
	}

	private scrollSelectedLogs(direction: 1 | -1): boolean {
		const selected = this.rows().find(line => line.key === this.selectedKey);
		if (!selected?.row.logs?.length) return false;
		if (!this.expanded.has(selected.key)) {
			if (direction === 1) {
				this.expanded.add(selected.key);
				return true;
			}
			return false;
		}
		this.logScroll = Math.max(0, Math.min(this.logPage.max, this.logScroll + direction * this.logPage.size));
		return true;
	}

	private moveSelection(data: string): void {
		const keys = this.rows().flatMap(line => line.key ? [line.key] : []);
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
		this.logScroll = 0;
		this.tui.requestRender();
	}

	private cycleFilter(direction: 1 | -1): void {
		const current = FILTERS.findIndex(entry => entry.id === this.filter);
		const next = (current + direction + FILTERS.length) % FILTERS.length;
		this.filter = FILTERS[next]!.id;
		this.selectedKey = undefined;
		this.logScroll = 0;
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
