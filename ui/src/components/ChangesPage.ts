// Full-screen prompt-centric change review. Each block is one agent turn, not
// a commit: the prompt that asked for the change, the files that turn touched,
// the stable diff for those files, and the review run attached to that prompt.
// One numbered timeline replaces the old list/inspector split; selecting a
// prompt expands its evidence inline. The page is read-only; starting a review
// is the only action and it never edits or re-runs the work itself.
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
import { theme } from "../theme.ts";
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

interface ScreenLine {
	text: string;
	right?: string;
	key?: string;
	kind: "blank" | "prompt" | "turn" | "detail" | "file";
	turnId?: string;
	filePath?: string;
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

function oneLine(text: string, width: number): string {
	const flat = text.split(/\r?\n/).map(part => part.trim()).filter(Boolean).join(" ");
	return truncateToWidth(flat || "(prompt not recorded)", width, "…");
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

/** First line after the selected prompt's block, or the end of the page. */
function nextTurnStart(body: ScreenLine[], selectedStart: number, selectedId: string | undefined): number {
	for (let index = selectedStart + 1; index < body.length; index++) {
		const line = body[index];
		if (line?.kind === "prompt" && line.key !== selectedId) return index;
	}
	return body.length;
}

/**
 * Keep the whole selected block on screen. A block taller than the viewport
 * anchors at its first line; a smaller block is centred with its detail.
 */
function windowStartForBlock(start: number, end: number, height: number, length: number): number {
	if (length <= height) return 0;
	const blockHeight = Math.max(1, end - start);
	if (blockHeight >= height) return Math.max(0, Math.min(start, length - height));
	const preferred = start - Math.floor((height - blockHeight) / 2);
	return Math.max(0, Math.min(length - height, preferred));
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
	private viewportRows: number | undefined;
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

	/** The shell charges the command strip against the viewport. */
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
		const rows = Math.max(8, this.viewportRows ?? this.tui.terminal.rows ?? 24);
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
			` ${theme.accentBold("CHANGES")}  ${theme.dim(`· ${sanitizeDisplay(snap.project || "global")}`)}`,
			`${live}   ${theme.dim(snap.updatedAt || "refreshing…")} `,
			width,
		));
		const session = snap.scope === "session";
		push(between(
			` ${theme.accentBold(session ? "THIS CHAT" : "ALL CHATS")} ${theme.dim("·")} ${theme.dim(`${turns.length} ${turns.length === 1 ? "prompt" : "prompts"}`)} ${theme.dim("·")} ${theme.dim("w switch")}`,
			`${theme.text(`${totals.files} files`)} ${theme.success(`+${totals.additions}`)} ${theme.error(`−${totals.deletions}`)} `,
			width,
		));
		if (snap.error) {
			push(truncateToWidth(` ${theme.warning("!")} ${theme.warning(sanitizeDisplay(snap.error))}`, width, ".", true));
		}
		push("");

		const headerRows = out.length;
		const bodyHeight = Math.max(1, rows - headerRows - 1);
		if (!turns.length) {
			const guidance = [
				theme.accentBold("No prompt changes recorded yet."),
				"",
				theme.dim("Each completed agent turn that changes a workspace file appears here."),
				theme.dim("The block keeps the prompt, the files, the stable diff, and its review."),
				"",
				theme.dim("Send a prompt that edits a file, then reopen this screen."),
			];
			const top = Math.max(0, Math.floor((bodyHeight - guidance.length) / 2));
			for (let index = 0; index < bodyHeight; index++) {
				const line = guidance[index - top];
				push(line ? centered(line, width) : "");
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

		const body: ScreenLine[] = [];
		const contentBudget = Math.max(3, Math.min(20, bodyHeight - 10));
		let selectedStart = 0;
		for (const [index, turn] of turns.entries()) {
			if (index > 0) body.push({ text: "", kind: "blank" });
			const selected = turn.id === this.selectedTurnId;
			if (selected) selectedStart = body.length;
			const number = String(index + 1).padStart(2, "0");
			const stats = `${theme.text(`${turn.stats.files} file${turn.stats.files === 1 ? "" : "s"}`)} ${theme.success(`+${turn.stats.additions}`)} ${theme.error(`−${turn.stats.deletions}`)}`;
			body.push({
				text: ` ${selected ? theme.accentBold(number) : theme.dim(number)} ${selected ? theme.accent("❯") : theme.muted("❯")} ${theme.text(oneLine(turn.prompt, Math.max(16, width - 34)))}  ${theme.dim("·")} ${stats}`,
				key: turn.id,
				kind: "prompt",
				turnId: turn.id,
			});
			const files = turn.files.slice(0, 3).map(file => sanitizeDisplay(file.path)).join(" · ");
			const more = turn.files.length > 3 ? `  +${turn.files.length - 3}` : "";
			body.push({
				text: `     ${theme.dim(`${files || "no files"}${more}`)}  ${theme.dim("·")} ${reviewChip(turn.review)}`,
				key: turn.id,
				kind: "turn",
				turnId: turn.id,
			});
			if (selected) this.detailLines(turn, body, width, contentBudget);
		}

		const selectedEnd = nextTurnStart(body, selectedStart, this.selectedTurnId);
		let offset = windowStartForBlock(selectedStart, selectedEnd, bodyHeight, body.length);
		// Never open the viewport on the second line of an unselected prompt.
		// Prefer the next prompt boundary when the selected block still fits;
		// otherwise move back to the previous boundary.
		if (offset > 0 && body[offset]?.kind !== "prompt") {
			let forward = offset;
			while (forward < selectedStart && body[forward]?.kind !== "prompt") forward++;
			let backward = offset;
			while (backward > 0 && body[backward]?.kind !== "prompt") backward--;
			if (forward < selectedStart && selectedEnd - forward <= bodyHeight) offset = forward;
			else if (selectedEnd - backward <= bodyHeight) offset = backward;
		}
		this.hitboxes = [];
		for (let index = 0; index < bodyHeight; index++) {
			const line = body[offset + index];
			if (!line) {
				push("");
				continue;
			}
			const content = line.right ? between(line.text, line.right, width) : fit(line.text, width);
			push(content);
			if (line.key && (line.kind === "prompt" || line.kind === "turn" || line.kind === "file")) {
				this.hitboxes.push({
					x: 0,
					width,
					y: headerRows + index,
					kind: line.kind === "file" ? "file" : "turn",
					value: line.key,
				});
			}
		}
		push(truncateToWidth(
			` ${theme.dim("↑↓ prompt · 1/2/3 view · [ ] file · n/p finding · Enter fix · r review · w scope · Esc close")}`,
			width,
			".",
			true,
		));
		return out.slice(0, rows);
	}

	private detailLines(
		turn: ChangeTurnInfo,
		body: ScreenLine[],
		width: number,
		available: number,
	): void {
		const content = Math.max(8, width - 6);
		const route = [turn.provider, turn.model].filter(Boolean).join(" / ");
		const meta = [route, age(turn.createdAt)].filter(Boolean).join("  ·  ");
		if (meta) body.push({ text: `     ${theme.dim(meta)}`, kind: "detail" });
		if (this.promptExpanded) {
			for (const line of wrapDisplay(sanitizeDisplay(turn.prompt || "(prompt not recorded)"), content).slice(0, 8)) {
				body.push({ text: `     ${theme.text(line)}`, kind: "detail" });
			}
		}
		const findings = turn.review
			? (turn.review.findingsCount ?? turn.review.findings.length)
			: 0;
		const tabs: Array<{ id: InspectorTab; label: string }> = [
			{ id: "files", label: `FILES ${turn.files.length}` },
			{ id: "diff", label: "DIFF" },
			{ id: "review", label: findings ? `REVIEW ${findings}` : "REVIEW" },
		];
		body.push({
			text: `     ${tabs.map(tab => tab.id === this.tab
				? theme.accentBold(tab.label)
				: theme.dim(tab.label)).join("   ")}`,
			kind: "detail",
		});
		body.push({ text: "", kind: "blank" });
		if (this.tab === "files") {
			this.fileLines(turn, body, width, available);
		} else if (this.tab === "diff") {
			this.diffLines(turn, body, width, available);
		} else {
			this.reviewLines(turn, body, width, available);
		}
		body.push({
			text: `     ${theme.dim(this.tab === "files"
				? "Enter opens the diff · [ ] changes file · Space shows the full prompt"
				: this.tab === "diff"
					? "PgUp/PgDn scrolls · [ ] changes file · Space shows the full prompt"
					: "Enter / a asks to implement · n/p chooses · Space shows the full prompt")}`,
			kind: "detail",
		});
	}

	private fileLines(
		turn: ChangeTurnInfo,
		body: ScreenLine[],
		width: number,
		available: number,
	): void {
		if (!turn.files.length) {
			body.push({ text: `     ${theme.dim("No files were captured for this prompt.")}`, kind: "detail" });
			return;
		}
		const content = Math.max(8, width - 24);
		const selectedIndex = Math.max(0, turn.files.findIndex(file => file.path === this.selectedFilePath));
		const start = windowStart(selectedIndex, available, turn.files.length);
		for (const file of turn.files.slice(start, start + available)) {
			const selected = file.path === this.selectedFilePath;
			const tone = fileTone(file.change);
			const stats = file.binary ? theme.dim("binary") : `${theme.success(`+${file.additions}`)} ${theme.error(`−${file.deletions}`)}`;
			body.push({
				text: `     ${selected ? theme.accent("▸") : " "} ${tone(fileLetter(file))} ${theme.text(truncateToWidth(sanitizeDisplay(file.path), content, "…"))}`,
				right: stats,
				key: file.path,
				kind: "file",
				filePath: file.path,
			});
		}
		if (start > 0 || start + available < turn.files.length) {
			body.push({
				text: `     ${theme.dim(`${start + 1}-${Math.min(turn.files.length, start + available)} of ${turn.files.length} files · [ ] selects`)}`,
				kind: "detail",
			});
		}
	}

	private diffLines(
		turn: ChangeTurnInfo,
		body: ScreenLine[],
		width: number,
		available: number,
	): void {
		const file = turn.files.find(item => item.path === this.selectedFilePath) ?? turn.files[0];
		if (!file) {
			body.push({ text: `     ${theme.dim("No file selected.")}`, kind: "detail" });
			return;
		}
		const content = Math.max(8, width - 6);
		const state = this.diff(turn.id);
		const diffFile = state.files.find(item => item.path === file.path);
		body.push({
			text: `     ${theme.accent("◂")} ${theme.text(truncateToWidth(sanitizeDisplay(file.path), content, "…"))} ${theme.accent("▸")}`,
			right: theme.dim(`${this.fileIndex(turn, file.path)}/${turn.files.length}`),
			kind: "detail",
		});
		if (file.binary || diffFile?.binary) {
			body.push({ text: `     ${theme.dim("Binary file changed; no text diff.")}`, kind: "detail" });
			return;
		}
		if (state.error) {
			body.push({ text: `     ${theme.error(state.error)}`, kind: "detail" });
			return;
		}
		if (state.loading && !diffFile) {
			body.push({ text: `     ${theme.dim("Loading the stable diff for this prompt…")}`, kind: "detail" });
			return;
		}
		if (!diffFile || !diffFile.text.trim()) {
			body.push({ text: `     ${theme.dim("No textual diff recorded for this file.")}`, kind: "detail" });
			return;
		}
		const diffLines = diffFile.text.replace(/\r\n/g, "\n").split("\n");
		if (diffLines.at(-1) === "") diffLines.pop();
		const start = Math.min(this.scroll, Math.max(0, diffLines.length - 1));
		const shown = diffLines.slice(start, start + available);
		for (const line of shown) {
			body.push({ text: `     ${truncateToWidth(styleDiffLine(line), content, "…")}`, kind: "detail" });
		}
		if (start + shown.length < diffLines.length || diffFile.truncated || state.truncated) {
			body.push({
				text: `     ${theme.dim(`… ${Math.max(0, diffLines.length - start - shown.length)} more diff lines${diffFile.truncated || state.truncated ? " (truncated)" : ""} · PgDn scrolls`)}`,
				kind: "detail",
			});
		}
	}

	private reviewLines(
		turn: ChangeTurnInfo,
		body: ScreenLine[],
		width: number,
		available: number,
	): void {
		const content = Math.max(10, width - 6);
		const review = turn.review;
		if (!review) {
			body.push({ text: `     ${theme.dim("No review run for this prompt.")}`, kind: "detail" });
			body.push({ text: `     ${theme.text("Press r to run the local rules review.")}`, kind: "detail" });
			body.push({ text: `     ${theme.dim("It reads this prompt's stable diff only; it never edits files.")}`, kind: "detail" });
			return;
		}
		const tone = reviewTone(review);
		body.push({
			text: `     ${tone(`● ${review.status}`)} ${theme.dim("·")} ${theme.text(review.engine)} ${theme.dim("·")} ${reviewChip(review)}`,
			right: theme.dim(age(review.finishedAt || review.startedAt || review.createdAt)),
			kind: "detail",
		});
		if (review.summary) {
			for (const line of wrapDisplay(sanitizeDisplay(review.summary), content)) {
				body.push({ text: `     ${theme.text(line)}`, kind: "detail" });
			}
		}
		if (review.error) {
			body.push({ text: `     ${theme.error(truncateToWidth(review.error, content, "…"))}`, kind: "detail" });
		}
		if (!review.findings.length) {
			body.push({ text: `     ${theme.success("No findings in this run.")}`, kind: "detail" });
			return;
		}
		this.selectedFindingIndex = Math.max(
			0,
			Math.min(review.findings.length - 1, this.selectedFindingIndex),
		);
		const start = Math.min(this.scroll, Math.max(0, review.findings.length - 1));
		let rendered = 0;
		let index = start;
		for (; index < review.findings.length; index++) {
			if (rendered >= available && index !== this.selectedFindingIndex) break;
			rendered += this.pushFinding(review.findings[index]!, body, content, index === this.selectedFindingIndex);
		}
		if (index < review.findings.length) {
			body.push({ text: `     ${theme.dim("… more findings · PgDn scrolls")}`, kind: "detail" });
		}
		const total = review.findingsCount ?? review.findings.length;
		if (total > review.findings.length) {
			body.push({
				text: `     ${theme.dim(`Top ${review.findings.length} of ${total} findings shown in severity order.`)}`,
				kind: "detail",
			});
		}
		body.push({
			text: review.status === "running" || review.status === "queued"
				? `     ${theme.dim("x cancels · the run reads only this prompt's diff")}`
				: `     ${theme.dim("r re-runs the local rules review")}`,
			kind: "detail",
		});
	}

	private pushFinding(
		finding: ReviewFindingInfo,
		body: ScreenLine[],
		content: number,
		selected = false,
	): number {
		const tone = severityTone(finding.severity);
		const location = finding.file
			? `${finding.file}${finding.line ? `:${finding.line}` : ""}`
			: "prompt";
		body.push({
			text: `     ${selected ? theme.accent("▸") : " "} ${tone(finding.severity.toUpperCase().padEnd(8))} ${theme.text(truncateToWidth(finding.title, Math.max(10, content - 12), "…"))}`,
			right: theme.dim(truncateToWidth(location, 30, "…")),
			kind: "detail",
		});
		let used = 1;
		if (finding.body) {
			for (const line of wrapDisplay(sanitizeDisplay(finding.body), Math.max(10, content - 4)).slice(0, 2)) {
				body.push({ text: `       ${theme.dim(line)}`, kind: "detail" });
				used++;
			}
		}
		body.push({ text: "", kind: "blank" });
		return used + 1;
	}

	private fileIndex(turn: ChangeTurnInfo, path: string): number {
		const index = turn.files.findIndex(file => file.path === path);
		return index < 0 ? 0 : index + 1;
	}
}
