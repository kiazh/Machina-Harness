// Full-screen live command center. Categories are real cards, not table
// sections: each card owns its border, title and rows, and the selected row
// expands its retained logs inside the same card. One column, centered on wide
// terminals, so the page reads like a stack of bounded evidence panels instead
// of a spreadsheet.
import {
	Key,
	matchesKey,
	truncateToWidth,
	visibleWidth,
	type Component,
	type TUI,
	type TuiMouseEvent,
} from "@earendil-works/pi-tui";
import { renderCard } from "../design/box.ts";
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

interface ScreenLine {
	text: string;
	right?: string;
	key?: string;
	row?: StatusRow;
	section?: string;
	kind: "row" | "detail" | "empty";
}

interface StatusCard {
	title: string;
	/** Content lines as authored (before right-alignment). */
	content: ScreenLine[];
	/** Rendered card rows, already indented. */
	rendered: string[];
	/** Parallel to rendered: the content line for content rows. */
	meta: Array<ScreenLine | undefined>;
	/** First selectable key in the card (for border clicks). */
	firstKey?: string;
	hasSelection: boolean;
	left: number;
	width: number;
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

function between(left: string, right: string, width: number): string {
	const room = Math.max(0, width - visibleWidth(right) - 1);
	const first = truncateToWidth(left, room, "…");
	const gap = Math.max(0, room - visibleWidth(first));
	return `${first}${" ".repeat(gap)} ${truncateToWidth(right, width, "…")}`;
}

/** Keep a state chip beside the content, truncating the content first. */
function withState(left: string, state: string, width: number): string {
	const separator = `  ${theme.dim("·")} `;
	const room = Math.max(0, width - visibleWidth(separator) - visibleWidth(state));
	const first = truncateToWidth(left, room, "…");
	return `${first}${separator}${state}`;
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

function windowStartForBlock(start: number, end: number, height: number, length: number): number {
	if (length <= height) return 0;
	const blockHeight = Math.max(1, end - start);
	if (blockHeight >= height) return Math.max(0, Math.min(start, length - height));
	const preferred = start - Math.floor((height - blockHeight) / 2);
	return Math.max(0, Math.min(length - height, preferred));
}

export class StatusPage implements Component {
	private readonly tui: TUI;
	private readonly snapshot: () => StatusSnapshot;
	private readonly onOpen?: (row: StatusRow, section: string | undefined) => boolean;
	private filter: StatusFilter = "all";
	private viewportRows: number | undefined;
	private selectedKey: string | undefined;
	private logScroll = 0;
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

	setViewportRows(rows: number): void {
		this.viewportRows = rows;
	}

	enter(_direction?: 1 | -1): void {
		this.tui.requestRender();
	}

	dispose(): void {
		this.hitboxes = [];
	}

	invalidate(): void {
		this.hitboxes = [];
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
		let cards = this.buildCards(snap, width, bodyHeight);
		const rowsInCards = cards.flatMap(card => card.content.filter(line => line.kind === "row" && line.key));
		const before = this.selectedKey;
		this.ensureSelection(rowsInCards);
		if (this.selectedKey !== before) cards = this.buildCards(snap, width, bodyHeight);

		if (!cards.length || !rowsInCards.length) {
			const card = this.emptyCard(width, rowsInCards.length === 0 && cards.length > 0);
			const top = Math.max(0, Math.floor((bodyHeight - card.length) / 2));
			for (let index = 0; index < bodyHeight; index++) {
				const line = card[index - top];
				push(line ?? "");
			}
			push(truncateToWidth(
				` ${theme.dim("1–7 filter · ↑↓ move · Enter open · Space detail · PgUp/PgDn logs · Esc chat")}`,
				width,
				"…",
				true,
			));
			return out.slice(0, rows);
		}

		const flat: string[] = [];
		const meta: Array<{ line?: ScreenLine; key?: string; left: number; width: number }> = [];
		const ranges: Array<{ start: number; end: number; key?: string }> = [];
		for (const [cardIndex, card] of cards.entries()) {
			if (cardIndex > 0) {
				flat.push("");
				meta.push({ left: 0, width });
			}
			const start = flat.length;
			for (const [lineIndex, line] of card.rendered.entries()) {
				flat.push(line);
				const content = card.meta[lineIndex];
				meta.push({
					line: content,
					key: content?.key ?? card.firstKey,
					left: card.left,
					width: card.width,
				});
			}
			ranges.push({ start, end: flat.length, key: card.firstKey });
		}

		const selectedFlat = meta.findIndex(entry => entry.line?.kind === "row" && entry.line.key === this.selectedKey);
		const selectedCard = ranges.find(range => selectedFlat >= range.start && selectedFlat < range.end)
			?? ranges.find(range => range.key === cards.find(card => card.hasSelection)?.firstKey)
			?? ranges[0]!;
		const offset = windowStartForBlock(selectedCard.start, selectedCard.end, bodyHeight, flat.length);
		for (let index = 0; index < bodyHeight; index++) {
			const lineIndex = offset + index;
			const line = flat[lineIndex];
			if (line === undefined) {
				push("");
				continue;
			}
			push(truncateToWidth(line, width));
			const info = meta[lineIndex];
			if (info?.key) {
				this.hitboxes.push({
					x: info.left,
					width: info.width,
					y: headerRows + index,
					key: info.key,
				});
			}
		}
		push(truncateToWidth(
			` ${theme.dim("1–7 filter · ↑↓ move · Enter open · Space detail · PgUp/PgDn logs · Esc chat")}`,
			width,
			"…",
			true,
		));
		return out.slice(0, rows);
	}

	private buildCards(snap: StatusSnapshot, width: number, bodyHeight?: number): StatusCard[] {
		const all = this.filter === "all";
		const sections: Array<{ title: string; entries: StatusRow[]; empty: string; category: StatusFilter }> = [];
		if (all || this.filter === "sessions") {
			sections.push({ title: "Sessions", entries: snap.sessions ?? [], empty: "No persisted sessions yet.", category: "sessions" });
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
					logs: [`Queued guidance: ${snap.queued}`, `Pending approvals: ${snap.approvals}`],
				}],
				empty: "No queued guidance.",
				category: "all",
			});
		}

		const cardWidth = Math.max(24, width - 4);
		const left = 2;
		const inner = Math.max(12, cardWidth - 6);
		const cards: StatusCard[] = [];
		for (const section of sections) {
			if (all && section.entries.length === 0) continue;
			const content: ScreenLine[] = [];
			if (!section.entries.length) {
				content.push({ text: `${theme.dim(section.empty)}`, kind: "empty" });
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
				content.push({
					text: withState(
						` ${selected ? theme.accent("❯") : marker} ${theme.text(sanitizeDisplay(entry.label))}${detail}${arrow}`,
						stateText,
						inner,
					),
					key,
					row: entry,
					section: section.title,
					kind: "row",
				});
				if (!selected) continue;
				const logs = entry.logs ?? [];
				if (expanded) {
					const budget = bodyHeight === undefined
						? 40
						: Math.max(1, Math.min(40, bodyHeight - 8 - (section.entries.length - 1)));
					const maxStart = Math.max(0, logs.length - budget);
					this.logScroll = Math.min(this.logScroll, maxStart);
					const start = this.logScroll;
					const shown = logs.slice(start, start + budget);
					if (start > 0) {
						content.push({ text: `  ${theme.dim(`↑ ${start} earlier · PgUp`)}`, kind: "detail" });
					}
					for (const log of shown) {
						for (const part of sanitizeDisplay(log).split("\n")) {
							content.push({
								text: `  ${theme.dim(truncateToWidth(part, Math.max(8, inner - 2), "…"))}`,
								kind: "detail",
							});
						}
					}
					const later = logs.length - start - shown.length;
					if (later > 0) {
						content.push({ text: `  ${theme.dim(`↓ ${later} later · PgDn`)}`, kind: "detail" });
					}
				} else {
					const shown = logs.slice(0, 1);
					for (const log of shown) {
						for (const part of sanitizeDisplay(log).split("\n")) {
							content.push({
								text: `  ${theme.dim(truncateToWidth(part, Math.max(8, inner - 2), "…"))}`,
								kind: "detail",
							});
						}
					}
					const remaining = logs.length - shown.length;
					if (remaining > 0) {
						const last = content.at(-1);
						if (last) last.text += `  ${theme.dim(`· +${remaining} more`)}`;
					}
				}
			}
			const hasSelection = content.some(line => line.kind === "row" && line.key === this.selectedKey);
			const renderedCard = renderCard(
				content.map(line => line.right ? between(line.text, line.right, inner) : line.text),
				{
					width: cardWidth,
					title: `${section.title} · ${section.entries.length}`,
					paddingX: 2,
					paddingY: false,
					fill: hasSelection ? "toolBg" : "assistantBg",
					titleTone: hasSelection ? theme.accentBold : theme.muted,
				},
			);
			const rendered = renderedCard.map(line => `${" ".repeat(left)}${line}`);
			const meta: Array<ScreenLine | undefined> = [undefined, ...content, undefined];
			cards.push({
				title: section.title,
				content,
				rendered,
				meta,
				firstKey: content.find(line => line.key)?.key,
				hasSelection,
				left,
				width: cardWidth,
			});
		}
		return cards;
	}

	private emptyCard(width: number, filtered: boolean): string[] {
		const cardWidth = Math.max(24, Math.min(76, width - 4));
		const left = Math.max(0, Math.floor((width - cardWidth) / 2));
		const lines = filtered
			? [theme.dim("No activity matches this filter."), "", theme.dim("Choose another category, or press 1 for everything.")]
			: [theme.text("Nothing running."), "", theme.dim("Send a prompt, start a shell, or connect an MCP server.")];
		return renderCard(lines.map(line => centered(line, cardWidth - 6)), {
			width: cardWidth,
			title: "Activity",
			paddingX: 2,
			paddingY: true,
			fill: "assistantBg",
		}).map(line => `${" ".repeat(left)}${line}`);
	}

	/** Selectable rows in reading order (shared by keys and the viewport). */
	private rows(): ScreenLine[] {
		return this.buildCards(this.snapshot(), this.tui.terminal.columns ?? 80)
			.flatMap(card => card.content)
			.filter(line => line.kind === "row" && line.key);
	}

	private ensureSelection(rows: ScreenLine[]): void {
		const keys = rows.flatMap(line => line.key ? [line.key] : []);
		if (!keys.length) {
			this.selectedKey = undefined;
			return;
		}
		if (!this.selectedKey || !keys.includes(this.selectedKey)) this.selectedKey = keys[0];
	}

	selectDefault(): void {
		const rows = this.rows();
		this.selectedKey = rows.find(line => line.key && !line.key.startsWith("queue:"))?.key
			?? rows.find(line => line.key)?.key;
		this.tui.requestRender();
	}

	private selectFirst(): void {
		this.selectedKey = this.rows().find(line => line.key)?.key;
		this.logScroll = 0;
	}

	private logBudget(): number {
		return Math.max(1, Math.min(40, (this.viewportRows ?? this.tui.terminal.rows ?? 24) - 12));
	}

	private scrollSelectedLogs(direction: 1 | -1): boolean {
		const selected = this.rows().find(line => line.key === this.selectedKey);
		const logs = selected?.row?.logs ?? [];
		if (!selected?.key || !this.expanded.has(selected.key) || logs.length <= this.logBudget()) return false;
		const max = Math.max(0, logs.length - this.logBudget());
		this.logScroll = Math.max(0, Math.min(max, this.logScroll + direction * this.logBudget()));
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
