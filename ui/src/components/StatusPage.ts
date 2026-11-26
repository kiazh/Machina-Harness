// Full-screen live command center. One unscrolled column: a live header, a
// text filter strip, then grouped activity rows. Selecting a row opens its
// retained detail inline beneath it, so there is no second panel and no
// nested chrome. The page reads only bounded local stores and status RPCs and
// never launches or re-runs work.
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
import { theme } from "../theme.ts";

export interface StatusRow {
	/** Stable identity so refresh does not lose selection/expansion. */
	id?: string;
	label: string;
	detail?: string;
	state?: string;
	running?: boolean;
	/** ISO timestamp used for the updated column. */
	updatedAt?: string;
	project?: string;
	/** Bounded local evidence shown under the selected row. */
	logs?: string[];
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
	project?: string;
	running?: boolean;
}

type StatusFilter = "all" | "sessions" | "agents" | "shells" | "tools" | "mcp" | "harnesses";

const FILTERS: ReadonlyArray<{ id: StatusFilter; label: string }> = [
	{ id: "all", label: "ALL" },
	{ id: "sessions", label: "SESSIONS" },
	{ id: "agents", label: "AGENTS" },
	{ id: "shells", label: "SHELLS" },
	{ id: "tools", label: "TOOLS" },
	{ id: "mcp", label: "MCP" },
	{ id: "harnesses", label: "HARNESSES" },
];

type LineKind = "section" | "empty" | "row" | "detail" | "blank";

interface ScreenLine {
	text: string;
	/** Present only for selectable rows. */
	key?: string;
	row?: StatusRow;
	section?: string;
	kind: LineKind;
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
	// Always keep one clear column between content and the right-aligned
	// status/time columns, so a long label never runs into its own state.
	const room = Math.max(0, width - visibleWidth(right) - 1);
	const first = truncateToWidth(left, room, "…", true);
	const gap = Math.max(0, room - visibleWidth(first));
	return `${first}${" ".repeat(gap)} ${truncateToWidth(right, width, "…")}`;
}

function centered(text: string, width: number): string {
	const pad = Math.max(0, Math.floor((width - visibleWidth(text)) / 2));
	return `${" ".repeat(pad)}${text}`;
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
	private readonly expanded = new Set<string>();
	private hitboxes: Hitbox[] = [];

	constructor(
		tui: TUI,
		snapshot: () => StatusSnapshot,
		onOpen?: (row: StatusRow, section: string | undefined) => boolean,
	) {
		this.tui = tui;
		this.snapshot = snapshot;
		this.onOpen = onOpen;
	}

	/** The shell charges the command strip against the viewport. */
	setViewportRows(rows: number): void {
		this.viewportRows = rows;
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
			}
			this.tui.requestRender();
			return true;
		}
		if (data === " ") {
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
		const rows = Math.max(8, this.viewportRows ?? this.tui.terminal.rows ?? 24);
		let lines: string[];
		try {
			lines = this.build(columns, rows);
		} catch (error) {
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
		const live = snap.running
			? `${theme.success("●")} ${theme.success("live")}`
			: `${theme.dim("○")} ${theme.dim("idle")}`;
		const project = sanitizeDisplay(snap.project || "global");
		push(between(
			` ${theme.accentBold("ACTIVITY")}  ${theme.dim(`· ${project}`)}`,
			`${live}   ${theme.dim(snap.updatedAt || "refreshing…")} `,
			width,
		));

		// Text filters, not pills: the active category carries the only
		// accent mark in this row. The strip wraps before it truncates, so a
		// narrow terminal still names every category.
		const filters = FILTERS.map((entry) => {
			const count = this.filterCount(snap, entry.id);
			const active = entry.id === this.filter;
			const plain = `${active ? "● " : "  "}${entry.label} ${count}`;
			return {
				entry,
				plain,
				text: active
					? `${theme.accent("●")} ${theme.accentBold(entry.label)} ${theme.dim(String(count))}`
					: `  ${theme.muted(entry.label)} ${theme.dim(String(count))}`,
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
			const nextWidth = row ? row.plainWidth + 3 + pieceWidth : pieceWidth;
			if (!row || nextWidth > Math.max(1, width - 1)) {
				row = { text: "", pieces: [], plainWidth: 0 };
				filterRows.push(row);
			}
			const x = 1 + (row.pieces.length ? row.plainWidth + 3 : 0);
			row.text += `${row.pieces.length ? "   " : ""}${piece.text}`;
			row.plainWidth += (row.pieces.length ? 3 : 0) + pieceWidth;
			row.pieces.push({ entry: piece.entry.id, x, width: pieceWidth });
		}
		this.hitboxes = [];
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
			push(truncateToWidth(` ${theme.warning("!")} ${theme.warning(sanitizeDisplay(snap.error))}`, width, "…", true));
		}
		push("");

		const headerRows = out.length;
		const bodyHeight = Math.max(1, rows - headerRows - 1);
		let body = this.bodyLines(snap, width);
		let rowLines = body.filter(line => line.kind === "row" && line.key);
		const previousSelection = this.selectedKey;
		this.ensureSelection(rowLines);
		// The selected row expands inline. If ensureSelection chose a row for
		// the first time, rebuild so its detail is part of this frame.
		if (this.selectedKey !== previousSelection) {
			body = this.bodyLines(snap, width);
			rowLines = body.filter(line => line.kind === "row" && line.key);
		}
		const selectedLine = body.findIndex(line => line.kind === "row" && line.key === this.selectedKey);
		const offset = rowLines.length ? windowStart(selectedLine, bodyHeight, body.length) : 0;

		if (!rowLines.length) {
			const guidance = [
				theme.text("Nothing to show here."),
				"",
				theme.dim("Choose another filter, or send a prompt to start activity."),
			];
			const top = Math.max(0, Math.floor((bodyHeight - guidance.length) / 2));
			for (let index = 0; index < bodyHeight; index++) {
				const line = guidance[index - top];
				push(line ? centered(line, width) : "");
			}
		} else {
			for (let index = 0; index < bodyHeight; index++) {
				const line = body[offset + index];
				if (!line) {
					push("");
					continue;
				}
				push(fit(line.text, width));
				if (line.kind === "row" && line.key) {
					this.hitboxes.push({ x: 0, width, y: headerRows + index, key: line.key });
				}
			}
		}
		push(truncateToWidth(
			` ${theme.dim("1–7 filter · ↑↓ move · Enter open · Space detail · Esc chat")}`,
			width,
			"…",
			true,
		));
		return out.slice(0, rows);
	}

	/** Flat body lines in reading order; the selected row expands inline. */
	private bodyLines(snap: StatusSnapshot, width: number): ScreenLine[] {
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
			sections.push({ title: "Tools", entries: snap.tools, empty: "No recent tool calls.", category: "tools" });
		}
		if (all || this.filter === "mcp") {
			sections.push({ title: "MCP", entries: snap.mcp, empty: "No MCP servers configured.", category: "mcp" });
		}
		if (all || this.filter === "harnesses") {
			sections.push({ title: "Harnesses", entries: snap.harnesses, empty: "No external harnesses detected.", category: "harnesses" });
		}
		if (all && (snap.queued > 0 || snap.approvals > 0)) {
			sections.push({
				title: "Queue",
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

		const lines: ScreenLine[] = [];
		let renderedSections = 0;
		for (const section of sections) {
			if (all && section.entries.length === 0) continue;
			if (renderedSections > 0) lines.push({ text: "", kind: "blank" });
			renderedSections++;
			lines.push({
				text: ` ${theme.muted(section.title.toUpperCase())}  ${theme.dim(String(section.entries.length))}`,
				kind: "section",
			});
			if (!section.entries.length) {
				lines.push({ text: `   ${theme.dim(section.empty)}`, kind: "empty" });
				continue;
			}
			for (const [index, entry] of section.entries.entries()) {
				const key = entry.id ?? `${section.category}:${index}:${entry.label}`;
				const selected = key === this.selectedKey;
				const expanded = this.expanded.has(key);
				const marker = entry.running ? theme.success("●") : theme.dim("○");
				const detail = entry.detail ? theme.muted(`  ${sanitizeDisplay(entry.detail)}`) : "";
				const state = sanitizeDisplay(entry.state || "—");
				const updated = age(entry.updatedAt);
				const stateText = `${stateStyle(entry.state)(state)}${updated ? ` ${theme.dim(`· ${updated}`)}` : ""}`;
				const arrow = entry.logs?.length
					? (expanded ? theme.accent(" ▾") : theme.dim(" ▸"))
					: "";
				lines.push({
					text: ` ${selected ? theme.accent("❯") : marker} ${theme.text(sanitizeDisplay(entry.label))}${detail}  ${theme.dim("·")} ${stateText}${arrow}`,
					key,
					row: entry,
					section: section.title,
					kind: "row",
				});
				if (!selected) continue;
				const logs = entry.logs ?? [];
				// One evidence row while collapsed keeps the page dense; Space
				// expands to the retained tail. Never reserve rows for an empty
				// log list.
				const shown = expanded ? logs.slice(0, 40) : logs.slice(0, 1);
				for (const log of shown) {
					for (const part of sanitizeDisplay(log).split("\n")) {
						lines.push({
							text: `    ${theme.dim(truncateToWidth(part, Math.max(8, width - 6), "…"))}`,
							kind: "detail",
						});
					}
				}
				const remaining = logs.length - shown.length;
				if (remaining > 0) {
					if (expanded) {
						lines.push({
							text: `    ${theme.dim(`… ${remaining} more retained`)}`,
							kind: "detail",
						});
					} else {
						const last = lines.at(-1);
						if (last) last.text += `  ${theme.dim(`· +${remaining} more`)}`;
					}
				}
			}
		}
		return lines;
	}

	/** Selectable rows in reading order (shared by keys and windowing). */
	private rows(): ScreenLine[] {
		return this.bodyLines(this.snapshot(), 0);
	}

	private ensureSelection(rows: ScreenLine[]): void {
		const keys = rows.flatMap(line => line.key ? [line.key] : []);
		if (!keys.length) {
			this.selectedKey = undefined;
			return;
		}
		if (!this.selectedKey || !keys.includes(this.selectedKey)) this.selectedKey = keys[0];
	}

	/** Choose the first real activity row, falling back to the queue summary. */
	selectDefault(): void {
		const rows = this.rows();
		this.selectedKey = rows.find(line => line.key && !line.key.startsWith("queue:"))?.key
			?? rows.find(line => line.key)?.key;
		this.tui.requestRender();
	}

	private selectFirst(): void {
		const rows = this.rows();
		this.selectedKey = rows.find(line => line.key)?.key;
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
