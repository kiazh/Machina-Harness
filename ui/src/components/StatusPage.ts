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
	// Always keep one clear column between content and the right-aligned
	// status/time columns, so a long label never runs into its own state.
	const room = Math.max(0, width - visibleWidth(right) - 1);
	const first = truncateToWidth(left, room, "…", true);
	const gap = Math.max(0, room - visibleWidth(first));
	return `${first}${" ".repeat(gap)} ${truncateToWidth(right, width, "…")}`;
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
			const selected = this.buildPanels(this.snapshot())
				.left.lines.find(line => line.key === this.selectedKey);
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
		const live = snap.running
			? `${theme.success("●")} ${theme.success("live")}`
			: `${theme.dim("○")} ${theme.dim("idle")}`;
		const project = sanitizeDisplay(snap.project || "global");
		push(between(
			` ${theme.accentBold("Machina")} ${theme.dim("·")} ${theme.text("Activity")}  ${theme.dim(`· ${project}`)}`,
			`${live}   ${theme.dim(snap.updatedAt || "refreshing…")} `,
			width,
		));
		// One pill per filter: the active one carries a filled background so the
		// eye lands on it immediately; counts stay muted so labels lead.
		const tabPieces = FILTERS.map((entry) => {
			const count = this.filterCount(snap, entry.id);
			const base = `${entry.label}${entry.id === "all" ? "" : ` ${count}`}`;
			const active = entry.id === this.filter;
			return {
				entry,
				width: visibleWidth(` ${base} `),
				label: active
					? theme.userBg(theme.accentBold(` ${base} `))
					: theme.muted(` ${base} `),
			};
		});
		push(truncateToWidth(` ${tabPieces.map(piece => piece.label).join(" ")}`, width, "…", true));
		if (snap.error) {
			push(truncateToWidth(` ${theme.warning("!")} ${theme.warning(sanitizeDisplay(snap.error))}`, width, "…", true));
		}
		push(theme.border("─".repeat(width)));
		const headerRows = out.length;
		const bodyHeight = Math.max(1, rows - headerRows - 1);
		const panels = this.buildPanels(snap);
		this.ensureSelection(panels);
		const selected = panels.left.lines.find(line => line.key === this.selectedKey);
		const detail = this.buildDetail(selected, snap);
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
			const isSelected = Boolean(line.key && line.key === this.selectedKey);
			const text = isSelected && line.text.startsWith("  ")
				? `${theme.accentBold("❯")} ${line.text.slice(2)}`
				: line.text;
			const content = line.right ? between(text, line.right, panelWidth) : fit(text, panelWidth);
			return isSelected ? selectionBar(content, panelWidth) : content;
		};
		this.hitboxes = [];
		if (wide) {
			const leftWidth = Math.max(38, Math.floor((width - 3) * 0.62));
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
			// Narrow terminals: keep the list and the inspector both on screen
			// instead of stacking one long column where the detail can scroll
			// away from the selected row.
			const listHeight = Math.max(6, Math.floor(bodyHeight * 0.55));
			const detailHeight = Math.max(3, bodyHeight - listHeight - 1);
			const listOffset = windowStart(leftSelected, listHeight, panels.left.lines.length);
			for (let index = 0; index < listHeight; index++) {
				const line = panels.left.lines[listOffset + index];
				push(formatPanelLine(line, width));
				if (line?.key) this.hitboxes.push({ x: 0, width, y: headerRows + index, key: line.key });
			}
			const label = " Inspector ";
			push(truncateToWidth(
				`${theme.border("─".repeat(Math.max(1, Math.floor((width - label.length) / 2))))}${theme.accentBold(label)}${theme.border("─".repeat(Math.max(1, width - label.length - Math.floor((width - label.length) / 2))))}`,
				width,
			));
			// The divider already names the inspector, so skip its duplicate
			// panel title and give the metadata/logs the extra row.
			const detailLines = detail.lines.slice(1);
			for (let index = 0; index < detailHeight; index++) {
				push(formatPanelLine(detailLines[index], width));
			}
		}
		// Filter pills are clickable at their rendered positions.
		let tabX = 1;
		for (const piece of tabPieces) {
			this.hitboxes.push({ x: tabX, width: piece.width, y: 1, filter: piece.entry.id });
			tabX += piece.width + 1;
		}
		while (out.length < rows - 1) push(" ".repeat(width));
		push(truncateToWidth(
			` ${theme.dim("1–7 filter · ↑↓ move · Enter open · Space logs · Esc chat")}`,
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
		return { left: this.panel("Activity", sections) };
	}

	private panel(title: string, sections: Array<{ title: string; entries: StatusRow[]; empty: string; category: StatusFilter }>): PanelModel {
		const stateColumn = 10;
		const ageColumn = 7;
		const columns = (state: string, updated: string): string =>
			`${state.padEnd(stateColumn)} ${updated.padStart(ageColumn)}`.trimEnd();
		const lines: PanelLine[] = [{
			text: ` ${theme.accentBold(title.toUpperCase())}`,
			right: theme.dim(columns("STATUS", "UPDATED")),
		}];
		const keys: string[] = [];
		for (const section of sections) {
			// One clear row between groups; the first group sits under the
			// panel header without an extra gap.
			if (lines.length > 1) lines.push({ text: "" });
			lines.push({
				text: `  ${theme.accent("▏")}${theme.accentBold(section.title.toUpperCase())}`,
				right: theme.dim(String(section.entries.length)),
			});
			if (!section.entries.length) {
				lines.push({ text: `    ${theme.dim(`— ${section.empty}`)}` });
				continue;
			}
			for (const [index, entry] of section.entries.entries()) {
				const key = entry.id ?? `${section.category}:${index}:${entry.label}`;
				const marker = entry.running ? theme.success("●") : theme.dim("○");
				const detail = entry.detail ? theme.muted(`  ${sanitizeDisplay(entry.detail)}`) : "";
				const state = sanitizeDisplay(entry.state || "—");
				const updated = age(entry.updatedAt);
				const right = updated
					? `${stateStyle(entry.state)(state.padEnd(stateColumn))} ${theme.dim(updated.padStart(ageColumn))}`
					: stateStyle(entry.state)(state);
				const arrow = entry.logs?.length
					? (this.expanded.has(key) ? theme.accent("▾") : theme.dim("▸"))
					: "";
				lines.push({
					text: `  ${marker} ${theme.text(sanitizeDisplay(entry.label))}${detail}${arrow ? ` ${arrow}` : ""}`,
					right,
					key,
					row: entry,
					section: section.title,
				});
				keys.push(key);
			}
		}
		return { lines, keys };
	}

	private buildDetail(selected: PanelLine | undefined, snap: StatusSnapshot): PanelModel {
		const row = selected?.row;
		const lines: PanelLine[] = [{ text: ` ${theme.accentBold("INSPECTOR")}` }];
		if (!row) {
			lines.push({ text: "" });
			lines.push({ text: `  ${theme.dim("Select an activity row to inspect it.")}` });
			lines.push({ text: "" });
			lines.push({ text: `  ${theme.dim("↑↓ move between rows · 1–7 switch category")}` });
			return { lines, keys: [] };
		}
		lines.push({ text: "" });
		lines.push({ text: `  ${theme.accentBold(sanitizeDisplay(row.label))}` });
		const chips = [
			stateStyle(row.state)(sanitizeDisplay(row.state || "unknown")),
			theme.dim(selected.section || "activity"),
			theme.dim(age(row.updatedAt) || "unknown"),
		];
		lines.push({ text: `  ${chips.join(theme.dim("  ·  "))}` });
		lines.push({ text: "" });
		const meta: Array<[string, string]> = [
			["Project", row.project || snap.project || "global"],
			["Detail", row.detail || "—"],
		];
		for (const [label, value] of meta) {
			lines.push({ text: `  ${theme.dim(label.padEnd(9))} ${theme.text(sanitizeDisplay(value))}` });
		}
		lines.push({ text: "" });
		const onlineMcp = snap.mcp.filter(entry => entry.running).length;
		const liveHarnesses = snap.harnesses.filter(entry => entry.running).length;
		lines.push({
			text: `  ${theme.dim("Runtime".padEnd(9))} ${theme.text(`MCP ${onlineMcp}/${snap.mcp.length}`)} ${theme.dim("·")} ${theme.text(`harnesses ${liveHarnesses}/${snap.harnesses.length}`)}`,
		});
		lines.push({
			text: `  ${theme.dim("Queue".padEnd(9))} ${theme.text(String(snap.queued))} ${theme.dim("queued")} ${theme.dim("·")} ${theme.text(String(snap.approvals))} ${theme.dim(snap.approvals === 1 ? "approval" : "approvals")}`,
		});
		lines.push({ text: "" });
		lines.push({ text: `  ${theme.border("─".repeat(34))}` });
		const expanded = this.expanded.has(selected.key ?? "");
		const logCount = row.logs?.length ?? 0;
		lines.push({
			text: `  ${theme.accentBold("LOGS")}`,
			right: theme.dim(logCount ? `${logCount} ${expanded ? "shown" : "retained"}` : ""),
		});
		if (row.logs?.length) {
			const shown = expanded ? row.logs.slice(0, 40) : row.logs.slice(0, 3);
			for (const log of shown) {
				for (const part of sanitizeDisplay(log).split("\n")) {
					lines.push({ text: `  ${theme.border("│")} ${theme.dim(truncateToWidth(part, 92, "…"))}` });
				}
			}
			if (!expanded && row.logs.length > 3) {
				lines.push({ text: `  ${theme.dim(`… ${row.logs.length - 3} more · Space to expand`)}` });
			}
		} else {
			lines.push({ text: `  ${theme.dim("No retained logs for this row.")}` });
		}
		lines.push({ text: "" });
		const section = selected.section ?? "";
		const openable = section === "Agents"
			|| section === "Sessions"
			|| (section === "Harnesses" && (selected.key ?? "").startsWith("harness-job:"));
		lines.push({
			text: `  ${theme.dim(openable ? "Enter opens this conversation" : "Enter toggles the log preview")}`,
		});
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
