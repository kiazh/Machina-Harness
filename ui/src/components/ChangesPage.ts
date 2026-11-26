// Full-screen prompt-centric change review. Each row is one agent turn, not a
// commit: the prompt that asked for the change, the files that turn touched,
// the stable diff for those files, and the review run attached to that prompt.
// The page is read-only; starting a review is the only action and it never
// edits or re-runs the work itself.
import {
	Key,
	matchesKey,
	truncateToWidth,
	visibleWidth,
	type Component,
	type TUI,
	type TuiMouseEvent,
} from "@earendil-works/pi-tui";
import { sanitizeDisplay, wrapDisplay } from "../design/text.ts";
import type {
	ChangeDiffFileInfo,
	ChangeFileInfo,
	ChangeTurnInfo,
	ReviewFindingInfo,
	ReviewRunInfo,
} from "../protocol.ts";
import { selectionBar, theme } from "../theme.ts";
import { styleDiffLine } from "./DiffBlock.ts";

export interface ChangesDiffState {
	turnId?: string;
	loading: boolean;
	files: ChangeDiffFileInfo[];
	truncated: boolean;
	error?: string;
}

export interface ChangesSnapshot {
	turns: ChangeTurnInfo[];
	scope: "session" | "workspace";
	project?: string;
	updatedAt: string;
	error?: string;
	running?: boolean;
}

type InspectorTab = "files" | "diff" | "review";
type HitKind = "turn" | "file";

interface PanelLine {
	text: string;
	right?: string;
	key?: string;
	kind?: HitKind;
	turnId?: string;
	filePath?: string;
	row?: ChangeTurnInfo;
}

interface Hitbox {
	x: number;
	width: number;
	y: number;
	kind: HitKind;
	value: string;
}

function fit(text: string, width: number): string {
	const clipped = truncateToWidth(text, width, ".", true);
	return clipped + " ".repeat(Math.max(0, width - visibleWidth(clipped)));
}

function between(left: string, right: string, width: number): string {
	const room = Math.max(0, width - visibleWidth(right) - 1);
	const first = truncateToWidth(left, room, ".", true);
	const gap = Math.max(0, room - visibleWidth(first));
	return `${first}${" ".repeat(gap)} ${truncateToWidth(right, width, ".")}`;
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

function oneLine(text: string, width: number): string {
	const flat = text.split(/\r?\n/).map(part => part.trim()).filter(Boolean).join(" ");
	return truncateToWidth(flat || "(prompt not recorded)", width, "…", true);
}

function fileTone(change: string): (text: string) => string {
	switch (change) {
		case "added": return theme.success;
		case "deleted": return theme.error;
		case "modified": return theme.warning;
		default: return theme.dim;
	}
}

function fileLetter(file: ChangeFileInfo): string {
	if (file.binary) return "B";
	switch (file.change) {
		case "added": return "A";
		case "deleted": return "D";
		case "ignored": return "!";
		default: return "M";
	}
}

function severityTone(severity: string): (text: string) => string {
	switch (severity) {
		case "critical":
		case "high": return theme.error;
		case "medium": return theme.warning;
		case "low": return theme.info;
		default: return theme.dim;
	}
}

function highestSeverity(review: ReviewRunInfo): string {
	for (const severity of ["critical", "high", "medium", "low", "info"]) {
		if (review.findings.some(finding => finding.severity === severity)) return severity;
	}
	return "";
}

function reviewChip(review: ReviewRunInfo | null | undefined): string {
	if (!review) return theme.dim("review —");
	if (review.status === "running" || review.status === "queued") {
		return theme.warning(`review ${review.status}`);
	}
	if (review.status === "failed") return theme.error("review failed");
	if (review.status === "cancelled") return theme.dim("review cancelled");
	const count = review.findingsCount ?? review.findings.length;
	if (count === 0) return theme.success("review clean");
	const tone = severityTone(highestSeverity(review));
	return tone(`review ${count}`);
}

function reviewTone(review: ReviewRunInfo | null | undefined): (text: string) => string {
	if (!review) return theme.dim;
	if (review.status === "running" || review.status === "queued") return theme.warning;
	if (review.status === "failed") return theme.error;
	if (review.status === "cancelled") return theme.dim;
	return review.findings.length ? severityTone(highestSeverity(review)) : theme.success;
}

function preferredFile(files: ChangeFileInfo[]): string | undefined {
	const file = files.find(item => item.change === "modified")
		?? files.find(item => item.change === "added")
		?? files.find(item => item.change !== "deleted")
		?? files[0];
	return file?.path;
}

function windowStart(selected: number, height: number, length: number): number {
	if (length <= height) return 0;
	if (selected < 0) return 0;
	return Math.max(0, Math.min(length - height, selected - Math.floor(height / 2)));
}

export class ChangesPage implements Component {
	private readonly tui: TUI;
	private readonly snapshot: () => ChangesSnapshot;
	private readonly diff: (turnId: string | undefined) => ChangesDiffState;
	private readonly onSelectTurn?: (turnId: string) => void;
	private readonly onReview?: (turnId: string) => void;
	private readonly onCancelReview?: (turnId: string) => void;
	private readonly onToggleScope?: () => void;
	private readonly onFixFinding?: (turnId: string, finding: ReviewFindingInfo) => void;
	private selectedTurnId: string | undefined;
	private selectedFilePath: string | undefined;
	private tab: InspectorTab = "files";
	private promptExpanded = false;
	private scroll = 0;
	private selectedFindingIndex = 0;
	private hitboxes: Hitbox[] = [];

	constructor(
		tui: TUI,
		snapshot: () => ChangesSnapshot,
		diff: (turnId: string | undefined) => ChangesDiffState,
		options: {
			onSelectTurn?: (turnId: string) => void;
			onReview?: (turnId: string) => void;
			onCancelReview?: (turnId: string) => void;
			onToggleScope?: () => void;
			onFixFinding?: (turnId: string, finding: ReviewFindingInfo) => void;
		} = {},
	) {
		this.tui = tui;
		this.snapshot = snapshot;
		this.diff = diff;
		this.onSelectTurn = options.onSelectTurn;
		this.onReview = options.onReview;
		this.onCancelReview = options.onCancelReview;
		this.onToggleScope = options.onToggleScope;
		this.onFixFinding = options.onFixFinding;
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

	get selectedTurn(): string | undefined {
		return this.selectedTurnId;
	}

	get selectedFile(): string | undefined {
		return this.selectedFilePath;
	}

	/** Keep the selection inside the current data without firing callbacks. */
	syncSelection(): void {
		const turns = this.snapshot().turns;
		if (this.selectedTurnId && !turns.some(turn => turn.id === this.selectedTurnId)) {
			this.selectedTurnId = undefined;
			this.selectedFilePath = undefined;
			this.scroll = 0;
		}
		if (!this.selectedTurnId && turns.length) this.selectedTurnId = turns[0]!.id;
		const turn = turns.find(item => item.id === this.selectedTurnId);
		if (turn && (!this.selectedFilePath || !turn.files.some(file => file.path === this.selectedFilePath))) {
			this.selectedFilePath = preferredFile(turn.files);
		}
		if (!turn) this.selectedFilePath = undefined;
	}

	handleInput(data: string): boolean {
		const snap = this.snapshot();
		this.syncSelection();
		const turns = snap.turns;
		if (!turns.length) return false;
		const index = Math.max(0, turns.findIndex(turn => turn.id === this.selectedTurnId));
		if (data === "1" || data === "2" || data === "3") {
			const next = (["files", "diff", "review"] as const)[Number(data) - 1]!;
			if (next === "review" && this.tab !== "review") this.selectedFindingIndex = 0;
			this.tab = next;
			this.scroll = 0;
			this.tui.requestRender();
			return true;
		}
		if (matchesKey(data, Key.tab) || data === "\x1b[Z") {
			const order: InspectorTab[] = ["files", "diff", "review"];
			const current = order.indexOf(this.tab);
			const next = order[(current + (data === "\x1b[Z" ? -1 : 1) + order.length) % order.length]!;
			if (next === "review" && this.tab !== "review") this.selectedFindingIndex = 0;
			this.tab = next;
			this.scroll = 0;
			this.tui.requestRender();
			return true;
		}
		if (matchesKey(data, Key.up) || matchesKey(data, Key.down)) {
			const delta = matchesKey(data, Key.up) ? -1 : 1;
			this.selectIndex(Math.max(0, Math.min(turns.length - 1, index + delta)));
			return true;
		}
		if (matchesKey(data, Key.home)) {
			this.selectIndex(0);
			return true;
		}
		if (matchesKey(data, Key.end)) {
			this.selectIndex(turns.length - 1);
			return true;
		}
		if (matchesKey(data, Key.pageUp) || matchesKey(data, Key.pageDown)) {
			this.scroll = Math.max(0, this.scroll + (matchesKey(data, Key.pageUp) ? -10 : 10));
			this.tui.requestRender();
			return true;
		}
		if (data === "n" || data === "p") {
			this.moveFinding(data === "p" ? -1 : 1);
			return true;
		}
		if (data === "[") {
			this.cycleFile(-1);
			return true;
		}
		if (data === "]") {
			this.cycleFile(1);
			return true;
		}
		if (data === " ") {
			this.promptExpanded = !this.promptExpanded;
			this.tui.requestRender();
			return true;
		}
		if (matchesKey(data, Key.enter) || data === "a") {
			if (this.tab === "review") {
				const turn = turns[index];
				const finding = this.currentFinding(turn);
				if (turn && finding) {
					this.onFixFinding?.(turn.id, finding);
					return true;
				}
			}
			if (this.tab === "files") this.tab = "diff";
			this.scroll = 0;
			this.tui.requestRender();
			return true;
		}
		if (data === "g") {
			this.scroll = 0;
			this.tui.requestRender();
			return true;
		}
		if (data === "G") {
			this.scroll = Number.MAX_SAFE_INTEGER;
			this.tui.requestRender();
			return true;
		}
		if (data === "r") {
			const turn = turns[index];
			if (turn) {
				this.tab = "review";
				this.scroll = 0;
				this.onReview?.(turn.id);
				this.tui.requestRender();
			}
			return true;
		}
		if (data === "x") {
			const turn = turns[index];
			if (turn) {
				this.onCancelReview?.(turn.id);
				this.tui.requestRender();
			}
			return true;
		}
		if (data === "w") {
			this.onToggleScope?.();
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
		if (hit.kind === "turn") {
			this.selectIndex(this.snapshot().turns.findIndex(turn => turn.id === hit.value));
		} else {
			const turn = this.snapshot().turns.find(item => item.id === this.selectedTurnId);
			if (turn?.files.some(file => file.path === hit.value)) {
				this.selectedFilePath = hit.value;
				this.tab = "diff";
				this.scroll = 0;
				this.tui.requestRender();
			}
		}
		return { handled: true, focus: true, render: true };
	}

	render(width: number): string[] {
		const columns = Math.max(1, width);
		const rows = Math.max(8, this.tui.terminal.rows ?? 24);
		let lines: string[];
		try {
			lines = this.build(columns, rows);
		} catch (error) {
			lines = [
				theme.error("Changes are unavailable."),
				theme.dim(` ${error instanceof Error ? error.message : String(error)}`),
			];
		}
		while (lines.length < rows) lines.push(" ".repeat(columns));
		return lines.slice(0, rows).map(line => truncateToWidth(line, columns, "", true));
	}

	private selectIndex(index: number): void {
		const turns = this.snapshot().turns;
		const turn = turns[index];
		if (!turn) return;
		if (turn.id !== this.selectedTurnId) {
			this.selectedTurnId = turn.id;
			this.selectedFilePath = preferredFile(turn.files);
			this.scroll = 0;
			this.selectedFindingIndex = 0;
			this.promptExpanded = false;
			this.onSelectTurn?.(turn.id);
		}
		this.tui.requestRender();
	}

	private cycleFile(direction: 1 | -1): void {
		const turn = this.snapshot().turns.find(item => item.id === this.selectedTurnId);
		if (!turn || !turn.files.length) return;
		const current = Math.max(0, turn.files.findIndex(file => file.path === this.selectedFilePath));
		const next = (current + direction + turn.files.length) % turn.files.length;
		this.selectedFilePath = turn.files[next]!.path;
		this.scroll = 0;
		this.tui.requestRender();
	}

	private currentFinding(turn: ChangeTurnInfo | undefined): ReviewFindingInfo | undefined {
		const findings = turn?.review?.findings ?? [];
		if (!findings.length) return undefined;
		return findings[Math.max(0, Math.min(findings.length - 1, this.selectedFindingIndex))];
	}

	private moveFinding(direction: 1 | -1): void {
		if (this.tab !== "review") return;
		const turn = this.snapshot().turns.find(item => item.id === this.selectedTurnId);
		const findings = turn?.review?.findings ?? [];
		if (!findings.length) return;
		this.selectedFindingIndex = Math.max(
			0,
			Math.min(findings.length - 1, this.selectedFindingIndex + direction),
		);
		this.scroll = Math.max(0, this.selectedFindingIndex - 1);
		this.tui.requestRender();
	}

	private build(width: number, rows: number): string[] {
		const snap = this.snapshot();
		this.syncSelection();
		const out: string[] = [];
		const push = (line = ""): void => { out.push(line); };
		const turns = snap.turns;
		const selected = turns.find(turn => turn.id === this.selectedTurnId);
		const totals = turns.reduce(
			(total, turn) => ({
				files: total.files + turn.stats.files,
				additions: total.additions + turn.stats.additions,
				deletions: total.deletions + turn.stats.deletions,
			}),
			{ files: 0, additions: 0, deletions: 0 },
		);
		const live = snap.running
			? `${theme.success("●")} ${theme.success("live")}`
			: `${theme.dim("○")} ${theme.dim("idle")}`;
		push(between(
			` ${theme.accentBold("Machina")} ${theme.dim("·")} ${theme.text("Changes")}  ${theme.dim(`· ${sanitizeDisplay(snap.project || "global")}`)}`,
			`${live}   ${theme.dim(snap.updatedAt || "refreshing…")} `,
			width,
		));
		push(between(
			` ${theme.dim("← Activity")} ${theme.dim("│")} ${theme.userBg(theme.accentBold(" CHANGES "))} ${theme.dim("→")} ${theme.muted(snap.scope === "session" ? "this chat" : "all chats")}`,
			`${theme.dim(`${turns.length} prompts`)} ${theme.dim("·")} ${theme.text(`${totals.files} files`)} ${theme.success(`+${totals.additions}`)} ${theme.error(`−${totals.deletions}`)} `,
			width,
		));
		if (snap.error) {
			push(truncateToWidth(` ${theme.warning("!")} ${theme.warning(sanitizeDisplay(snap.error))}`, width, ".", true));
		}
		push(theme.border("─".repeat(width)));
		const headerRows = out.length;
		const bodyHeight = Math.max(1, rows - headerRows - 1);
		if (!turns.length) {
			const guidance = [
				theme.accentBold("No prompt changes recorded yet."),
				"",
				theme.dim("Each completed agent turn that changes a workspace file appears here."),
				theme.dim("The row keeps the prompt, the files, the stable diff, and its review run."),
				"",
				theme.dim("Send a prompt that edits a file, then reopen this screen."),
			];
			const top = Math.max(0, Math.floor((bodyHeight - guidance.length) / 2));
			for (let index = 0; index < bodyHeight; index++) {
				const line = guidance[index - top];
				push(line ? `  ${line}` : "");
			}
			push(truncateToWidth(
				` ${theme.dim("← Activity · Ctrl+N → · Esc chat")}`,
				width,
				".",
				true,
			));
			this.hitboxes = [];
			return out;
		}

		const wide = width >= 90 && bodyHeight >= 12;
		const leftWidth = Math.max(38, Math.floor((width - 3) * 0.52));
		const rightWidth = Math.max(24, width - leftWidth - 3);
		const listHeight = Math.max(6, Math.floor(bodyHeight * 0.44));
		const detailHeight = Math.max(3, bodyHeight - listHeight - 1);
		const left = this.buildList(turns, selected, snap);
		const detail = this.buildDetail(selected, snap, wide ? rightWidth : width, wide ? bodyHeight : detailHeight);
		const selectedLine = left.lines.findIndex(line => line.key === this.selectedTurnId && line.kind === "turn");
		const format = (line: PanelLine | undefined, panelWidth: number, selectedRow = false): string => {
			if (!line) return " ".repeat(panelWidth);
			// The row already carries its own styled marker; never slice ANSI
			// text to prepend another one.
			const content = line.right ? between(line.text, line.right, panelWidth) : fit(line.text, panelWidth);
			return selectedRow ? selectionBar(content, panelWidth) : content;
		};
		this.hitboxes = [];
		if (wide) {
			const leftOffset = windowStart(selectedLine, bodyHeight, left.lines.length);
			for (let index = 0; index < bodyHeight; index++) {
				const leftLine = left.lines[leftOffset + index];
				const rightLine = detail.lines[index];
				const selectedRow = leftLine?.key === this.selectedTurnId && leftLine.kind === "turn";
				push(`${format(leftLine, leftWidth, selectedRow)}${theme.border(" │ ")}${format(rightLine, rightWidth)}`);
				if (leftLine?.key && leftLine.kind) {
					this.hitboxes.push({
						x: 0,
						width: leftWidth,
						y: headerRows + index,
						kind: leftLine.kind,
						value: leftLine.key,
					});
				}
				if (rightLine?.key && rightLine.kind) {
					this.hitboxes.push({
						x: leftWidth + 3,
						width: rightWidth,
						y: headerRows + index,
						kind: rightLine.kind,
						value: rightLine.key,
					});
				}
			}
		} else {
			const leftOffset = windowStart(selectedLine, listHeight, left.lines.length);
			for (let index = 0; index < listHeight; index++) {
				const line = left.lines[leftOffset + index];
				const selectedRow = line?.key === this.selectedTurnId && line.kind === "turn";
				push(format(line, width, selectedRow));
				if (line?.key && line.kind) {
					this.hitboxes.push({
						x: 0,
						width,
						y: headerRows + index,
						kind: line.kind,
						value: line.key,
					});
				}
			}
			const dividerLabel = " Inspector ";
			push(truncateToWidth(
				`${theme.border("─".repeat(Math.max(1, Math.floor((width - dividerLabel.length) / 2))))}${theme.accentBold(dividerLabel)}${theme.border("─".repeat(Math.max(1, width - dividerLabel.length - Math.floor((width - dividerLabel.length) / 2))))}`,
				width,
			));
			const detailOffset = windowStart(0, detailHeight, detail.lines.length);
			for (let index = 0; index < detailHeight; index++) {
				push(format(detail.lines[detailOffset + index], width));
			}
		}
		push(truncateToWidth(
			` ${theme.dim("↑↓ prompt · 1/2/3 view · [ ] file · n/p finding · Enter fix · r review · w scope · Esc/←/→ close")}`,
			width,
			".",
			true,
		));
		return out;
	}

	private buildList(
		turns: ChangeTurnInfo[],
		_selected: ChangeTurnInfo | undefined,
		snap: ChangesSnapshot,
	): { lines: PanelLine[] } {
		const lines: PanelLine[] = [{
			text: ` ${theme.accentBold("PROMPT CHANGES")}`,
			right: theme.dim(String(turns.length)),
		}];
		let section = "";
		for (const turn of turns) {
			if (snap.scope === "workspace") {
				const next = turn.sessionId.slice(0, 8) || "unknown";
				if (next !== section) {
					section = next;
					lines.push({ text: "" });
					lines.push({
						text: `  ${theme.accent("▏")}${theme.accentBold(`SESSION ${section.toUpperCase()}`)}`,
						right: theme.dim(age(turn.createdAt)),
					});
				}
			}
			const marker = turn.id === this.selectedTurnId ? theme.accent("▸") : theme.dim("·");
			const review = reviewChip(turn.review);
			lines.push({
				text: `  ${marker} ${theme.dim(age(turn.createdAt).padStart(7))} ${theme.text(oneLine(turn.prompt, 160))}`,
				right: review,
				key: turn.id,
				kind: "turn",
				turnId: turn.id,
				row: turn,
			});
			const model = [turn.provider, turn.model].filter(Boolean).join("/");
			lines.push({
				text: `      ${theme.muted(`${turn.stats.files} file${turn.stats.files === 1 ? "" : "s"}`)} ${theme.success(`+${turn.stats.additions}`)} ${theme.error(`−${turn.stats.deletions}`)}${model ? ` ${theme.dim(`· ${model}`)}` : ""}`,
				key: turn.id,
				kind: "turn",
				turnId: turn.id,
				row: turn,
			});
		}
		return { lines };
	}

	private buildDetail(
		turn: ChangeTurnInfo | undefined,
		_snap: ChangesSnapshot,
		panelWidth: number,
		height: number,
	): { lines: PanelLine[] } {
		const lines: PanelLine[] = [];
		if (!turn) {
			lines.push({ text: ` ${theme.accentBold("INSPECTOR")}` });
			lines.push({ text: "" });
			lines.push({ text: `  ${theme.dim("Select a prompt to inspect its changes.")}` });
			return { lines };
		}
		const content = Math.max(8, panelWidth - 4);
		lines.push({
			text: ` ${theme.accentBold("PROMPT")}`,
			right: theme.dim(age(turn.createdAt)),
		});
		const prompt = sanitizeDisplay(turn.prompt || "(prompt not recorded)");
		const wrapped = wrapDisplay(prompt, content);
		const shown = this.promptExpanded ? wrapped : wrapped.slice(0, 3);
		for (const line of shown) lines.push({ text: `  ${theme.text(line)}` });
		if (!this.promptExpanded && wrapped.length > 3) {
			lines.push({ text: `  ${theme.dim(`… ${wrapped.length - 3} more lines · Space expands`)}` });
		}
		lines.push({ text: "" });
		const route = [turn.provider, turn.model].filter(Boolean).join(" / ");
		lines.push({
			text: `  ${theme.text(`${turn.stats.files} file${turn.stats.files === 1 ? "" : "s"}`)} ${theme.success(`+${turn.stats.additions}`)} ${theme.error(`−${turn.stats.deletions}`)}${route ? ` ${theme.dim("·")} ${theme.dim(route)}` : ""}`,
			right: reviewChip(turn.review),
		});
		lines.push({ text: "" });
		const findings = turn.review
			? (turn.review.findingsCount ?? turn.review.findings.length)
			: 0;
		const tabs: Array<{ id: InspectorTab; label: string }> = [
			{ id: "files", label: `FILES ${turn.files.length}` },
			{ id: "diff", label: "DIFF" },
			{ id: "review", label: findings ? `REVIEW ${findings}` : "REVIEW" },
		];
		lines.push({
			text: ` ${tabs.map(tab => tab.id === this.tab
				? theme.userBg(theme.accentBold(` ${tab.label} `))
				: theme.muted(` ${tab.label} `)).join(" ")}`,
		});
		lines.push({ text: "" });

		// The prompt block above consumes roughly twelve rows (more when it
		// wraps). Reserve that honestly instead of generating a whole diff and
		// letting the viewport silently discard it.
		const available = Math.max(4, height - 14);
		if (this.tab === "files") {
			this.buildFiles(turn, lines, content, available);
		} else if (this.tab === "diff") {
			this.buildDiff(turn, lines, content, available);
		} else {
			this.buildReview(turn, lines, content, available);
		}
		return { lines };
	}

	private buildFiles(
		turn: ChangeTurnInfo,
		lines: PanelLine[],
		content: number,
		available: number,
	): void {
		if (!turn.files.length) {
			lines.push({ text: `  ${theme.dim("No files were captured for this prompt.")}` });
			return;
		}
		const selectedIndex = Math.max(0, turn.files.findIndex(file => file.path === this.selectedFilePath));
		const start = windowStart(selectedIndex, available, turn.files.length);
		for (const file of turn.files.slice(start, start + available)) {
			const selected = file.path === this.selectedFilePath;
			const tone = fileTone(file.change);
			const stats = file.binary ? theme.dim("binary") : `${theme.success(`+${file.additions}`)} ${theme.error(`−${file.deletions}`)}`;
			lines.push({
				text: `  ${selected ? theme.accent("▸") : " "} ${tone(fileLetter(file))} ${theme.text(truncateToWidth(sanitizeDisplay(file.path), Math.max(8, content - 18), "…"))}`,
				right: stats,
				key: file.path,
				kind: "file",
				filePath: file.path,
			});
		}
		if (start > 0 || start + available < turn.files.length) {
			lines.push({
				text: `  ${theme.dim(`${start + 1}-${Math.min(turn.files.length, start + available)} of ${turn.files.length} files · [ ] selects`)}`,
			});
		}
		lines.push({ text: "" });
		lines.push({ text: `  ${theme.dim("Enter opens the diff · [ ] changes file")}` });
	}

	private buildDiff(
		turn: ChangeTurnInfo,
		lines: PanelLine[],
		content: number,
		available: number,
	): void {
		const file = turn.files.find(item => item.path === this.selectedFilePath) ?? turn.files[0];
		if (!file) {
			lines.push({ text: `  ${theme.dim("No file selected.")}` });
			return;
		}
		const state = this.diff(turn.id);
		const diffFile = state.files.find(item => item.path === file.path);
		lines.push({
			text: `  ${theme.accent("◂")} ${theme.text(truncateToWidth(sanitizeDisplay(file.path), Math.max(8, content - 8), "…"))} ${theme.accent("▸")}`,
			right: theme.dim(`${this.fileIndex(turn, file.path)}/${turn.files.length}`),
		});
		if (file.binary || diffFile?.binary) {
			lines.push({ text: "" });
			lines.push({ text: `  ${theme.dim("Binary file changed; no text diff.")}` });
			return;
		}
		if (state.error) {
			lines.push({ text: "" });
			lines.push({ text: `  ${theme.error(state.error)}` });
			return;
		}
		if (state.loading && !diffFile) {
			lines.push({ text: "" });
			lines.push({ text: `  ${theme.dim("Loading the stable diff for this prompt…")}` });
			return;
		}
		if (!diffFile || !diffFile.text.trim()) {
			lines.push({ text: "" });
			lines.push({ text: `  ${theme.dim("No textual diff recorded for this file.")}` });
			return;
		}
		const diffLines = diffFile.text.replace(/\r\n/g, "\n").split("\n");
		if (diffLines.at(-1) === "") diffLines.pop();
		const start = Math.min(this.scroll, Math.max(0, diffLines.length - 1));
		const shown = diffLines.slice(start, start + available);
		lines.push({ text: "" });
		for (const line of shown) {
			lines.push({ text: `  ${truncateToWidth(styleDiffLine(line), Math.max(8, content), "…")}` });
		}
		if (start + shown.length < diffLines.length || diffFile.truncated || state.truncated) {
			lines.push({ text: `  ${theme.dim(`… ${Math.max(0, diffLines.length - start - shown.length)} more diff lines${diffFile.truncated || state.truncated ? " (truncated)" : ""} · PgDn scrolls`)}` });
		}
	}

	private buildReview(
		turn: ChangeTurnInfo,
		lines: PanelLine[],
		content: number,
		available: number,
	): void {
		const review = turn.review;
		if (!review) {
			lines.push({ text: `  ${theme.dim("No review run for this prompt.")}` });
			lines.push({ text: "" });
			lines.push({ text: `  ${theme.text("Press r to run the local rules review.")}` });
			lines.push({ text: `  ${theme.dim("It reads this prompt's stable diff only; it never edits files.")}` });
			return;
		}
		const tone = reviewTone(review);
		lines.push({
			text: `  ${tone(`● ${review.status}`)} ${theme.dim("·")} ${theme.text(review.engine)} ${theme.dim("·")} ${reviewChip(review)}`,
			right: theme.dim(age(review.finishedAt || review.startedAt || review.createdAt)),
		});
		if (review.summary) {
			for (const line of wrapDisplay(sanitizeDisplay(review.summary), content)) {
				lines.push({ text: `  ${theme.text(line)}` });
			}
		}
		if (review.error) {
			lines.push({ text: `  ${theme.error(truncateToWidth(review.error, content, "…"))}` });
		}
		lines.push({ text: "" });
		if (!review.findings.length) {
			lines.push({ text: `  ${theme.success("No findings in this run.")}` });
		} else {
			this.selectedFindingIndex = Math.max(
				0,
				Math.min(review.findings.length - 1, this.selectedFindingIndex),
			);
			const start = Math.min(this.scroll, Math.max(0, review.findings.length - 1));
			let rendered = 0;
			let index = start;
			for (; index < review.findings.length; index++) {
				if (rendered >= available && index !== this.selectedFindingIndex) break;
				rendered += this.pushFinding(
					review.findings[index]!,
					lines,
					content,
					index === this.selectedFindingIndex,
				);
			}
			if (index < review.findings.length) {
				lines.push({ text: `  ${theme.dim(`… more findings · PgDn scrolls`)}` });
			}
		}
		const total = review.findingsCount ?? review.findings.length;
		if (total > review.findings.length) {
			lines.push({
				text: `  ${theme.dim(`Top ${review.findings.length} of ${total} findings shown in severity order.`)}`,
			});
		}
		lines.push({ text: "" });
		if (review.findings.length) {
			lines.push({
				text: `  ${theme.accentBold("Enter / a")} ${theme.text("ask to implement the selected fix")} ${theme.dim("· n/p chooses · it asks before sending anything")}`,
			});
		}
		lines.push({
			text: review.status === "running" || review.status === "queued"
				? `  ${theme.dim("x cancels · the run reads only this prompt's diff")}`
				: `  ${theme.dim("r re-runs the local rules review")}`,
		});
	}

	private pushFinding(
		finding: ReviewFindingInfo,
		lines: PanelLine[],
		content: number,
		selected = false,
	): number {
		const tone = severityTone(finding.severity);
		const location = finding.file
			? `${finding.file}${finding.line ? `:${finding.line}` : ""}`
			: "prompt";
		lines.push({
			text: `  ${selected ? theme.accent("▸") : " "} ${tone(finding.severity.toUpperCase().padEnd(8))} ${theme.text(truncateToWidth(finding.title, Math.max(10, content - 12), "…"))}`,
			right: theme.dim(truncateToWidth(location, 30, "…")),
		});
		let used = 1;
		if (finding.body) {
			for (const line of wrapDisplay(sanitizeDisplay(finding.body), Math.max(10, content - 4)).slice(0, 2)) {
				lines.push({ text: `    ${theme.dim(line)}` });
				used++;
			}
		}
		lines.push({ text: "" });
		return used + 1;
	}

	private fileIndex(turn: ChangeTurnInfo, path: string): number {
		const index = turn.files.findIndex(file => file.path === path);
		return index < 0 ? 0 : index + 1;
	}
}
