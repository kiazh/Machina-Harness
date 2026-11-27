// Prompt history on one side, the selected prompt's evidence on the other.
// The unit is an agent turn, not a commit.
import {
	Key,
	matchesKey,
	truncateToWidth,
	visibleWidth,
	wrapTextWithAnsi,
	type Component,
	type TUI,
	type TuiMouseEvent,
} from "@earendil-works/pi-tui";
import { inspectLayout, selectionWindow } from "../design/inspect.ts";
import { sanitizeDisplay, wrapDisplay } from "../design/text.ts";
import type {
	ChangeDiffFileInfo,
	ChangeFileInfo,
	ChangeTurnInfo,
	ReviewFindingInfo,
	ReviewRunInfo,
} from "../protocol.ts";
import { selectionBar, theme } from "../theme.ts";
import { describeError, uiLog } from "../debug.ts";
import { styleDiffLine } from "./DiffBlock.ts";

const log = uiLog.scope("changes");

export interface ChangesDiffState {
	loading: boolean;
	files: ChangeDiffFileInfo[];
	truncated: boolean;
	error?: string;
}

export interface ChangesSnapshot {
	turns: ChangeTurnInfo[];
	hasChat?: boolean;
	error?: string;
	canFix?: boolean;
	/** Failures already handed to the chat by the Fix action. */
	handledErrors?: ReadonlySet<string>;
	/** Findings already handed to the chat, keyed `turnId:fingerprint`. */
	sentFindings?: ReadonlyMap<string, { turnId: string; finding: ReviewFindingInfo; cleared?: boolean; queued?: boolean }>;
}

type InspectorTab = "files" | "diff" | "review";
const INSPECTOR_TABS: readonly InspectorTab[] = ["files", "diff", "review"];
type HitKind = "turn" | "file" | "finding" | "fix" | "failure" | "clear" | "tab";

interface ScreenLine {
	text: string;
	right?: string;
	key?: string;
	kind: "detail" | "file" | "finding" | "fix" | "failure" | "clear";
	hitWidth?: number;
	tabs?: Array<{ x: number; width: number; tab: InspectorTab }>;
}

interface Hitbox {
	x: number;
	width: number;
	y: number;
	kind: HitKind;
	value: string;
}

function between(left: string, right: string, width: number): string {
	const room = Math.max(0, width - visibleWidth(right) - 1);
	const first = truncateToWidth(left, room, ".", true);
	const gap = Math.max(0, room - visibleWidth(first));
	return `${first}${" ".repeat(gap)} ${truncateToWidth(right, width, ".")}`;
}

/** Strip the legacy indentation used by the individual evidence formatters. */
function unindent(text: string): string {
	return text.startsWith("     ") ? text.slice(5) : text;
}

function oneLine(text: string, width: number): string {
	const flat = sanitizeDisplay(text).replace(/\s+/g, " ").trim();
	return truncateToWidth(flat || "(prompt not recorded)", width, "…");
}

/** If a directory is too long to fit, keep the filename instead. */
function pathLabel(path: string, width: number): string {
	const clean = sanitizeDisplay(path).replace(/\s*[\r\n]+\s*/g, " ");
	const name = clean.split(/[/\\]/).at(-1) ?? clean;
	return visibleWidth(clean) <= width ? clean : truncateToWidth(name === clean ? clean : `…/${name}`, width, "…");
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
	if (!review) return theme.muted("Not reviewed");
	if (review.status === "running" || review.status === "queued") {
		return theme.warning(`Review ${review.status}`);
	}
	if (review.status === "failed") return theme.error("Review failed");
	if (review.status === "cancelled") return theme.muted("Review cancelled");
	const count = review.findingsCount ?? review.findings.length;
	if (count === 0) return theme.success("No findings");
	const tone = severityTone(highestSeverity(review));
	return tone(`${count} ${count === 1 ? "finding" : "findings"}`);
}

function coverageLabel(review: ReviewRunInfo): string {
	const engine = review.engine === "model" ? "Model" : "Rules";
	const coverage = review.coverage;
	if (!coverage) return `${engine} review`;
	const files = `${coverage.reviewed}/${coverage.files} ${coverage.files === 1 ? "file" : "files"}`;
	const parts = [`${engine} reviewed ${files}`];
	if (coverage.truncated) parts.push("partial diff");
	if (coverage.invalid) parts.push(`${coverage.invalid} omitted`);
	return parts.join(" · ");
}

function preferredFile(files: ChangeFileInfo[]): string | undefined {
	const file = files.find(item => item.change === "modified")
		?? files.find(item => item.change === "added")
		?? files.find(item => item.change !== "deleted")
		?? files[0];
	return file?.path;
}

export class ChangesPage implements Component {
	private readonly tui: TUI;
	private readonly snapshot: () => ChangesSnapshot;
	private readonly diff: (turnId: string | undefined) => ChangesDiffState;
	private readonly onOpenDiff?: () => void;
	private readonly onReview?: (turnId: string) => void;
	private readonly onReviewModel?: (turnId: string) => void;
	private readonly onCancelReview?: (turnId: string) => void;
	private readonly onFixFinding?: (turnId: string, finding: ReviewFindingInfo) => void;
	private readonly onFixFailure?: (turnId: string, code: string, reason: string) => void;
	private readonly onClearHandled?: (turnId: string) => void;
	private viewportRows: number | undefined;
	private selectedTurnId: string | undefined;
	private selectedFilePath: string | undefined;
	private tab: InspectorTab = "files";
	private promptExpanded = false;
	private scroll = 0;
	private pageSize = 1;
	private selectedFindingIndex = 0;
	private hitboxes: Hitbox[] = [];
	private wheelRegions: Array<{ x: number; width: number; y0: number; y1: number; kind: "list" | "detail" }> = [];
	private wrappedDiff?: { file: ChangeDiffFileInfo; width: number; rows: string[] };

	constructor(
		tui: TUI,
		snapshot: () => ChangesSnapshot,
		diff: (turnId: string | undefined) => ChangesDiffState,
		options: {
			onOpenDiff?: () => void;
			onReview?: (turnId: string) => void;
			onReviewModel?: (turnId: string) => void;
			onCancelReview?: (turnId: string) => void;
			onFixFinding?: (turnId: string, finding: ReviewFindingInfo) => void;
			onFixFailure?: (turnId: string, code: string, reason: string) => void;
			onClearHandled?: (turnId: string) => void;
		} = {},
	) {
		this.tui = tui;
		this.snapshot = snapshot;
		this.diff = diff;
		this.onOpenDiff = options.onOpenDiff;
		this.onReview = options.onReview;
		this.onReviewModel = options.onReviewModel;
		this.onCancelReview = options.onCancelReview;
		this.onFixFinding = options.onFixFinding;
		this.onFixFailure = options.onFixFailure;
		this.onClearHandled = options.onClearHandled;
	}

	/** The shell charges the command strip against the viewport. */
	setViewportRows(rows: number): void {
		this.viewportRows = rows;
	}

	dispose(): void {
		this.invalidate();
	}

	invalidate(): void {
		this.hitboxes = [];
		this.wheelRegions = [];
		this.wrappedDiff = undefined;
	}

	get selectedDiffTurn(): string | undefined {
		return this.tab === "diff" ? this.selectedTurnId : undefined;
	}

	/** The file whose diff the Diff view is showing (undefined elsewhere). */
	get selectedDiffFile(): string | undefined {
		return this.tab === "diff" ? this.selectedFilePath : undefined;
	}

	/** Keep the selection inside the current data without firing callbacks. */
	syncSelection(turns = this.snapshot().turns): void {
		if (this.selectedTurnId && !turns.some(turn => turn.id === this.selectedTurnId)) {
			this.selectedTurnId = undefined;
			this.selectedFilePath = undefined;
			this.promptExpanded = false;
			this.scroll = 0;
			this.selectedFindingIndex = 0;
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
		const turns = snap.turns;
		this.syncSelection(turns);
		if (!turns.length) return false;
		const index = Math.max(0, turns.findIndex(turn => turn.id === this.selectedTurnId));
		if ((data === "f" || matchesKey(data, Key.enter)) && this.fixFailure()) return true;
		if (data === "1" || data === "2" || data === "3") {
			this.showTab(INSPECTOR_TABS[Number(data) - 1]!);
			return true;
		}
		if (matchesKey(data, Key.tab) || data === "\x1b[Z") {
			const current = INSPECTOR_TABS.indexOf(this.tab);
			const next = (current + (data === "\x1b[Z" ? -1 : 1) + INSPECTOR_TABS.length) % INSPECTOR_TABS.length;
			this.showTab(INSPECTOR_TABS[next]!);
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
			this.scroll = Math.max(0, this.scroll + (matchesKey(data, Key.pageUp) ? -this.pageSize : this.pageSize));
			this.tui.requestRender();
			return true;
		}
		if (data === "n" || data === "p") {
			this.moveFinding(data === "p" ? -1 : 1);
			return true;
		}
		if (data === "c" && this.tab === "review") {
			const turn = turns[index];
			if (turn && this.handledFindings(turn).length) {
				this.onClearHandled?.(turn.id);
				this.selectedFindingIndex = 0;
				this.scroll = 0;
				this.tui.requestRender();
			}
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
			this.scroll = 0;
			this.tui.requestRender();
			return true;
		}
		if (matchesKey(data, Key.enter) || data === "a") {
			if (this.tab === "review") {
				const turn = turns[index];
				const active = turn ? this.activeFindings(turn) : [];
				const finding = active[Math.min(this.selectedFindingIndex, active.length - 1)];
				if (turn && finding) this.fixSelectedFinding(`${turn.id}:${finding.fingerprint}`);
				return true;
			}
			if (this.tab === "files" && matchesKey(data, Key.enter)) {
				this.showTab("diff");
				return true;
			}
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
				this.showTab("review");
				this.onReview?.(turn.id);
			}
			return true;
		}
		if (data === "m") {
			const turn = turns[index];
			if (turn) {
				this.showTab("review");
				this.onReviewModel?.(turn.id);
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
				const turns = this.snapshot().turns;
				const index = Math.max(0, turns.findIndex(turn => turn.id === this.selectedTurnId));
				this.selectIndex(Math.max(0, Math.min(turns.length - 1, index + (delta > 0 ? 1 : -1))));
			} else if (this.tab === "files" && !this.promptExpanded) {
				this.cycleFile(delta > 0 ? 1 : -1);
			} else {
				const step = Math.max(1, Math.floor(this.pageSize / 2));
				this.scroll = Math.max(0, this.scroll + (delta > 0 ? step : -step));
				this.tui.requestRender();
			}
			return { handled: true, focus: true, render: true };
		}
		if (event.type !== "click" || event.button !== "left") return undefined;
		const hit = this.hitboxes.find(candidate =>
			event.y === candidate.y && event.x >= candidate.x && event.x < candidate.x + candidate.width,
		);
		if (!hit) return undefined;
		if (hit.kind === "turn") {
			this.selectIndex(this.snapshot().turns.findIndex(turn => turn.id === hit.value));
		} else if (hit.kind === "failure") {
			this.fixFailure(hit.value);
		} else if (hit.kind === "tab") {
			const tab = INSPECTOR_TABS.find(candidate => candidate === hit.value);
			if (tab) this.showTab(tab);
		} else if (hit.kind === "finding") {
			this.selectedFindingIndex = Number(hit.value);
			this.scroll = 0;
			this.tui.requestRender();
		} else if (hit.kind === "fix") {
			this.fixSelectedFinding(hit.value);
		} else if (hit.kind === "clear") {
			this.onClearHandled?.(hit.value);
			this.selectedFindingIndex = 0;
			this.scroll = 0;
			this.tui.requestRender();
		} else {
			const turn = this.snapshot().turns.find(item => item.id === this.selectedTurnId);
			if (turn?.files.some(file => file.path === hit.value)) {
				this.selectedFilePath = hit.value;
				this.showTab("diff");
			}
		}
		return { handled: true, focus: true, render: true };
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
			this.wrappedDiff = undefined;
			if (this.tab === "diff") this.onOpenDiff?.();
		}
		this.tui.requestRender();
	}

	private showTab(tab: InspectorTab): void {
		if (tab === "review" && this.tab !== "review") this.selectedFindingIndex = 0;
		this.tab = tab;
		this.promptExpanded = false;
		this.scroll = 0;
		if (tab === "diff") this.onOpenDiff?.();
		this.tui.requestRender();
	}

	private failureCode(turn: ChangeTurnInfo, reason: string): string {
		return `${turn.id}\0${this.tab}\0${reason}`;
	}

	private reviewFailure(turn: ChangeTurnInfo | undefined): string | undefined {
		const review = turn?.review;
		if (!review || review.status !== "failed") return undefined;
		const error = review.error?.trim();
		if (error) return error;
		if (typeof review.summary === "string" && review.summary.trim()) return review.summary.trim();
		return "Review failed without an error message.";
	}

	private fixFailure(expected?: string): boolean {
		if (this.promptExpanded || this.tab === "files") return false;
		const snap = this.snapshot();
		const turn = snap.turns.find(item => item.id === this.selectedTurnId);
		if (!turn || snap.canFix === false) return false;
		const reason = this.tab === "review" ? this.reviewFailure(turn) : this.diff(turn.id).error;
		if (!reason) return false;
		const code = this.failureCode(turn, reason);
		if ((expected !== undefined && code !== expected) || snap.handledErrors?.has(code)) return false;
		this.onFixFailure?.(turn.id, code, reason);
		return true;
	}

	/** A failure the user can hand to the chat, with the reason in full. */
	private failureBody(turn: ChangeTurnInfo, title: string, message: string, body: ScreenLine[], width: number, height: number): void {
		const snap = this.snapshot();
		const code = this.failureCode(turn, message);
		body.push({ text: theme.error(title), kind: "detail" });
		if (snap.canFix === false) {
			body.push({ text: theme.muted(snap.hasChat === false
				? "Open a chat to hand this failure to the agent."
				: "Subagent chats are for inspection. Return to the main chat to fix this."), kind: "detail" });
		} else if (snap.handledErrors?.has(code)) {
			body.push({ text: theme.muted("Sent to chat. Press Esc to see the reply."), kind: "detail" });
		} else {
			const action = "Enter  Fix this";
			body.push({ text: selectionBar(action, visibleWidth(action)), kind: "failure", key: code, hitWidth: Math.min(width, visibleWidth(action)) });
		}
		const lines = wrapDisplay(message, width);
		this.pageSize = Math.max(1, height - body.length);
		const start = this.scroll = Math.min(this.scroll, Math.max(0, lines.length - this.pageSize));
		for (const line of lines.slice(start, start + this.pageSize)) body.push({ text: theme.text(line), kind: "detail" });
	}

	private cycleFile(direction: 1 | -1): void {
		const turn = this.snapshot().turns.find(item => item.id === this.selectedTurnId);
		if (!turn || !turn.files.length) return;
		const current = Math.max(0, turn.files.findIndex(file => file.path === this.selectedFilePath));
		const next = (current + direction + turn.files.length) % turn.files.length;
		this.selectedFilePath = turn.files[next]!.path;
		this.scroll = 0;
		if (this.tab === "diff") this.onOpenDiff?.();
		this.tui.requestRender();
	}

	private fixSelectedFinding(expected: string): void {
		const snap = this.snapshot();
		const turn = snap.turns.find(item => item.id === this.selectedTurnId);
		if (this.tab !== "review" || this.promptExpanded || !turn || snap.canFix === false || turn.review?.status !== "complete") return;
		const active = this.activeFindings(turn, snap);
		const finding = active[Math.min(this.selectedFindingIndex, active.length - 1)];
		if (finding && `${turn.id}:${finding.fingerprint}` === expected) this.onFixFinding?.(turn.id, finding);
	}

	private moveFinding(direction: 1 | -1): void {
		if (this.tab !== "review") return;
		const turn = this.snapshot().turns.find(item => item.id === this.selectedTurnId);
		const findings = turn ? this.activeFindings(turn) : [];
		if (!findings.length) return;
		this.selectedFindingIndex = Math.max(
			0,
			Math.min(findings.length - 1, this.selectedFindingIndex + direction),
		);
		this.scroll = 0;
		this.tui.requestRender();
	}

	private build(width: number, rows: number): string[] {
		const snap = this.snapshot();
		this.syncSelection(snap.turns);
		const out: string[] = [];
		const push = (line = ""): void => { out.push(line); };
		const turns = snap.turns;
		const title = snap.hasChat === false ? "Changes" : "Changes in this chat";
		push(between(
			`  ${theme.bold(theme.text(title))}`,
			width >= 60 && turns.length
				? theme.muted(`${turns.length} ${turns.length === 1 ? "prompt" : "prompts"}  `)
				: "",
			width,
		));
		if (snap.error) {
			push(truncateToWidth(` ${theme.warning("!")} ${theme.warning(oneLine(snap.error, width))}`, width, ".", true));
		}
		if (rows >= 12) push("");

		this.hitboxes = [];
		this.wheelRegions = [];
		const bottom = Math.max(out.length, rows - 1);
		const bodyTop = out.length;
		const height = Math.max(1, bottom - out.length);
		const selectedIndex = turns.findIndex(turn => turn.id === this.selectedTurnId);
		const turn = turns[selectedIndex];
		if (!turn) {
			if (snap.hasChat === false) {
				push(`  ${theme.text("No chat yet.")}`);
				push(`  ${theme.muted("Changes belong to a chat; send a message to start one.")}`);
			} else {
				push(`  ${theme.text("No changes in this chat yet.")}`);
				push(`  ${theme.muted("Each prompt in this chat that changes a file appears here.")}`);
			}
		} else {
			const layout = inspectLayout(width, height);
			const heading = layout.split ? 1 : 0;
			const listSize = Math.max(1, layout.listRows - heading);
			const start = selectionWindow(selectedIndex, listSize, turns.length);
			const visible = turns.slice(start, start + listSize);
			const stackedGap = !layout.split && height >= 8 ? 1 : 0;
			const detailHeight = Math.max(1, height - (layout.split ? heading : visible.length + stackedGap));
			const detail: ScreenLine[] = [];
			const inner = layout.detailWidth;
			this.detailLines(turn, detail, inner, layout.split ? height : detailHeight);
			const detailX = layout.inset + (layout.split ? layout.listWidth + 3 : 0);
			const listRow = (offset: number): string => {
				const entry = visible[offset];
				if (!entry) return "";
				const index = start + offset;
				const isCurrent = entry === turn;
				const prefix = `${isCurrent ? "❯" : " "} ${String(index + 1).padStart(2, "0")}  `;
				const label = `${prefix}${oneLine(entry.prompt, Math.max(1, layout.listWidth - visibleWidth(prefix)))}`;
				this.hitboxes.push({ x: layout.inset, width: layout.listWidth, y: out.length, kind: "turn", value: entry.id });
				return isCurrent ? selectionBar(label, layout.listWidth) : theme.text(label);
			};
			const detailRow = (offset: number): string => {
				const line = detail[offset];
				if (!line) return "";
				if (line.kind === "fix" || line.kind === "failure" || line.kind === "clear" || line.kind === "finding" || line.kind === "file") {
					this.hitboxes.push({ x: detailX, width: line.hitWidth ?? inner, y: out.length, kind: line.kind, value: line.key ?? "" });
				}
				for (const tab of line.tabs ?? []) {
					const visible = Math.min(tab.width, inner - tab.x);
					if (visible > 0) this.hitboxes.push({ x: detailX + tab.x, width: visible, y: out.length, kind: "tab", value: tab.tab });
				}
				const text = unindent(line.text);
				return truncateToWidth(line.right ? between(text, line.right, inner) : text, inner, "…");
			};
			if (layout.split) {
				push(layout.combine(theme.bold(theme.text(` Prompts  ${selectedIndex + 1}/${turns.length}`)), detailRow(0)));
				for (let index = 0; index < height - heading; index++) push(layout.combine(listRow(index), detailRow(index + 1)));
				this.wheelRegions = [
					{ x: layout.inset, width: layout.listWidth, y0: bodyTop, y1: bottom, kind: "list" },
					{ x: layout.inset + layout.listWidth + 3, width: layout.detailWidth, y0: bodyTop, y1: bottom, kind: "detail" },
				];
			} else {
				for (let index = 0; index < visible.length; index++) push(layout.combine(listRow(index)));
				if (stackedGap) push("");
				for (let index = 0; index < detailHeight; index++) push(layout.combine("", detailRow(index)));
				this.wheelRegions = [
					{ x: 0, width, y0: bodyTop, y1: bodyTop + visible.length, kind: "list" },
					{ x: 0, width, y0: bodyTop + visible.length, y1: bottom, kind: "detail" },
				];
			}
		}
		while (out.length < bottom) push("");
		const keys = this.promptExpanded ? "Space Back   PgUp/PgDn Read" : this.tab === "review"
			? "N/P Finding   PgUp/PgDn Read   R Rules   M Model"
			: "↑↓ Prompt   [ ] File   Space Full prompt";
		push(truncateToWidth(`  ${theme.muted(`${keys}   ? Keys`)}`, width, "…", true));
		return out.slice(0, rows);
	}

	private detailLines(
		turn: ChangeTurnInfo,
		body: ScreenLine[],
		width: number,
		height: number,
	): void {
		const available = Math.max(1, height);
		if (this.promptExpanded) {
			body.push({ text: theme.bold(theme.text("Full prompt")), kind: "detail" });
			const lines = wrapDisplay(sanitizeDisplay(turn.prompt || "(prompt not recorded)"), width);
			this.pageSize = Math.max(1, available - (lines.length > available ? 1 : 0));
			const start = this.scroll = Math.min(this.scroll, Math.max(0, lines.length - this.pageSize));
			const end = Math.min(lines.length, start + this.pageSize);
			for (const line of lines.slice(start, end)) body.push({ text: theme.text(line), kind: "detail" });
			if (lines.length > available) body.push({ text: theme.dim(`${start + 1}–${end}/${lines.length} · PgUp/PgDn`), kind: "detail" });
			return;
		}
		const findings = turn.review
			? (turn.review.findingsCount ?? turn.review.findings.length)
			: 0;
		const tabs: Array<{ id: InspectorTab; label: string }> = [
			{ id: "files", label: `1 Files ${turn.files.length}` },
			{ id: "diff", label: "2 Diff" },
			{ id: "review", label: findings ? `3 Review ${findings}` : "3 Review" },
		];
		const tabHits: NonNullable<ScreenLine["tabs"]> = [];
		let x = 0;
		const labels = tabs.map(tab => {
			const active = tab.id === this.tab;
			const label = active ? `[${tab.label}]` : ` ${tab.label} `;
			const width = visibleWidth(label);
			tabHits.push({ x, width, tab: tab.id });
			x += width + 1;
			return active ? selectionBar(label, width) : theme.text(label);
		});
		body.push({ text: labels.join(" "), tabs: tabHits, kind: "detail" });
		// One breathing row between the view switch and its evidence.
		if (available >= 8) body.push({ text: "", kind: "detail" });
		if (this.tab === "files") {
			if (available >= 6) {
				const heading = wrapDisplay(turn.prompt || "(prompt not recorded)", width).slice(0, 2);
				for (const line of heading) body.push({ text: theme.bold(theme.text(line)), kind: "detail" });
				const stats = `${theme.success(`+${turn.stats.additions}`)} ${theme.error(`−${turn.stats.deletions}`)}`;
				body.push({ text: `${stats}   ${reviewChip(turn.review)}`, kind: "detail" }, { text: "", kind: "detail" });
			}
			this.fileLines(turn, body, width, Math.max(1, available - body.length));
		} else if (this.tab === "diff") {
			this.diffLines(turn, body, width, height);
		} else {
			this.reviewLines(turn, body, width, available);
		}
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
		const selectedIndex = Math.max(0, turn.files.findIndex(file => file.path === this.selectedFilePath));
		const start = selectionWindow(selectedIndex, available, turn.files.length);
		for (const file of turn.files.slice(start, start + available)) {
			const selected = file.path === this.selectedFilePath;
			const tone = fileTone(file.change);
			const stats = file.binary ? theme.dim("binary") : `${theme.success(`+${file.additions}`)} ${theme.error(`−${file.deletions}`)}`;
			const content = Math.max(1, width - 5 - visibleWidth(stats));
			body.push({
				text: `     ${selected ? theme.accent("▸") : " "} ${tone(fileLetter(file))} ${theme.text(pathLabel(file.path, content))}`,
				right: stats,
				key: file.path,
				kind: "file",
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
		height: number,
	): void {
		const file = turn.files.find(item => item.path === this.selectedFilePath) ?? turn.files[0];
		if (!file) {
			body.push({ text: `     ${theme.dim("No file selected.")}`, kind: "detail" });
			return;
		}
		const state = this.diff(turn.id);
		const diffFile = state.files.find(item => item.path === file.path);
		const position = `${this.fileIndex(turn, file.path)}/${turn.files.length}`;
		body.push({
			text: theme.bold(theme.text(pathLabel(file.path, Math.max(1, width - position.length - 1)))),
			right: theme.muted(position),
			kind: "detail",
		});
		if (file.binary || diffFile?.binary) {
			body.push({ text: `     ${theme.dim("Binary file changed; no text diff.")}`, kind: "detail" });
			return;
		}
		if (state.error) {
			this.failureBody(turn, "Could not load this diff", state.error, body, width, height);
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
		if (this.wrappedDiff?.file !== diffFile || this.wrappedDiff.width !== width) {
			const lines = sanitizeDisplay(diffFile.text).split("\n");
			if (lines.at(-1) === "") lines.pop();
			this.wrappedDiff = { file: diffFile, width, rows: lines.flatMap(line =>
				wrapTextWithAnsi(line.startsWith(" ") ? theme.text(line) : styleDiffLine(line), width)) };
		}
		const diffLines = this.wrappedDiff.rows;
		const available = Math.max(1, height - body.length);
		const paged = diffLines.length > available || diffFile.truncated || state.truncated;
		this.pageSize = Math.max(1, available - Number(paged));
		const start = this.scroll = Math.min(this.scroll, Math.max(0, diffLines.length - this.pageSize));
		const shown = diffLines.slice(start, start + this.pageSize);
		for (const line of shown) {
			body.push({ text: `     ${line}`, kind: "detail" });
		}
		if (paged) {
			body.push({
				text: `     ${theme.muted(`${start + 1}–${start + shown.length} of ${diffLines.length} rows${diffFile.truncated || state.truncated ? " · Truncated" : ""}`)}`,
				kind: "detail",
			});
		}
	}

	private activeFindings(turn: ChangeTurnInfo, snap = this.snapshot()): ReviewFindingInfo[] {
		return (turn.review?.findings ?? []).filter(finding => !snap.sentFindings?.has(`${turn.id}:${finding.fingerprint}`));
	}

	private handledFindings(turn: ChangeTurnInfo): Array<{ finding: ReviewFindingInfo; queued: boolean }> {
		const sent = this.snapshot().sentFindings;
		return sent
			? [...sent.values()]
				.filter(item => item.turnId === turn.id && !item.cleared)
				.map(item => ({ finding: item.finding, queued: item.queued === true }))
			: [];
	}

	/** A snapshot review cannot verify subsequent working-tree fixes. */
	private handledLines(
		turn: ChangeTurnInfo,
		handled: Array<{ finding: ReviewFindingInfo; queued: boolean }>,
		body: ScreenLine[],
		width: number,
	): void {
		if (!handled.length) return;
		const clear = `C  Clear handled · ${handled.length}`;
		body.push({ text: theme.muted(clear), kind: "clear", key: turn.id, hitWidth: Math.min(width, visibleWidth(clear)) });
		for (const item of handled.slice(0, 4)) {
			const tag = item.queued ? "Queued" : "Sent";
			body.push({
				text: `${theme.muted(tag)}  ${theme.text(oneLine(item.finding.title, Math.max(1, width - tag.length - 2)))}`,
				kind: "detail",
			});
		}
		if (handled.length > 4) body.push({ text: theme.muted(`+${handled.length - 4} more handled`), kind: "detail" });
	}

	private reviewLines(
		turn: ChangeTurnInfo,
		body: ScreenLine[],
		width: number,
		available: number,
	): void {
		const review = turn.review;
		if (!review) {
			body.push({ text: theme.text("No review yet. Press R for rules or M for a model review."), kind: "detail" });
			return;
		}
		const coverage = coverageLabel(review);
		const failure = this.reviewFailure(turn);
		const findings = review.findings;
		const handled = this.handledFindings(turn);
		const active = this.activeFindings(turn);
		if (review.status !== "complete" || !findings.length) {
			if (failure) {
				this.failureBody(turn, "Review failed", failure, body, width, available);
				return;
			}
			const pending = review.status === "running" || review.status === "queued";
			const text = review.status === "complete" ? theme.success("No findings in this run.")
				: pending ? theme.warning(`Review ${review.status} · X cancels`) : theme.dim(`Review ${review.status}`);
			body.push({ text, kind: "detail" });
			body.push({ text: theme.muted(coverage), kind: "detail" });
			this.handledLines(turn, handled, body, width);
			return;
		}
		if (!active.length) {
			body.push({ text: theme.muted("All displayed findings were handed to chat."), kind: "detail" });
			this.handledLines(turn, handled, body, width);
			return;
		}
		const selected = this.selectedFindingIndex = Math.min(this.selectedFindingIndex, active.length - 1);
		const finding = active[selected]!;
		const canFix = this.snapshot().canFix !== false;
		const action = "Enter  Fix in Chat";
		const compact = available <= 4;
		const count = `${selected + 1}/${active.length}${(review.findingsCount ?? 0) > findings.length ? ` shown (${review.findingsCount} total)` : ""}`;
		const position = active.length > 1 ? ` · ${width >= 38 ? "Finding " : ""}${count}` : "";
		const severity = finding.severity.toUpperCase();
		const lineNumber = finding.line ? `:${finding.line}` : "";
		const pathWidth = Math.max(1, width - visibleWidth(severity + position + lineNumber) - 3);
		const location = finding.file ? `${pathLabel(finding.file, pathWidth)}${lineNumber}` : "Changed files";
		const title = wrapDisplay(finding.title || "Review finding", width).slice(0, available >= 7 ? 2 : 1);
		const actionLine: ScreenLine = {
			text: canFix ? selectionBar(action, visibleWidth(action))
				: theme.text(this.snapshot().hasChat === false
					? "Open a chat to fix this finding"
					: "Subagent chat: return to the main chat to fix"),
			kind: canFix ? "fix" : "detail",
			key: canFix ? `${turn.id}:${finding.fingerprint}` : undefined,
			hitWidth: canFix ? Math.min(width, visibleWidth(action)) : undefined,
		};
		// A short list above the reading area beats a third column of clipped titles.
		const showOther = available >= 12 && active.length > 1;
		const other: Array<{ index: number; entry: ReviewFindingInfo }> = [];
		if (showOther) {
			const size = Math.min(3, active.length);
			const start = selectionWindow(selected, size, active.length);
			for (const [offset, entry] of active.slice(start, start + size).entries()) {
				if (start + offset !== selected) other.push({ index: start + offset, entry });
			}
			body.push({ text: theme.muted("Other findings"), kind: "detail" });
			for (const { index, entry } of other) {
				const text = `  ${index + 1}. ${oneLine(entry.title, Math.max(1, width - 6))}`;
				body.push({ text: theme.text(text), kind: "finding", key: String(index) });
			}
			body.push({ text: "", kind: "detail" });
		}
		if (available >= 8) body.push({ text: theme.muted(coverage), kind: "detail" });
		for (const line of title) body.push({ text: theme.bold(theme.text(line)), kind: "detail" });
		body.push({
			text: `${severityTone(finding.severity)(severity)} · ${theme.text(location)}${theme.muted(position)}`,
			kind: "detail",
		});
		const gap = available >= 9 ? 1 : 0;
		if (gap) body.push({ text: "", kind: "detail" });
		const explanation = wrapDisplay(finding.body || "No additional detail.", width);
		// Reserve the action plus handled lines so paging never pushes the fix
		// off the pane; at degenerate heights the explanation yields first.
		const handledRows = handled.length ? 1 + Math.min(4, handled.length) + (handled.length > 4 ? 1 : 0) : 0;
		const reserveHandled = Math.min(handledRows, Math.max(0, available - body.length - 2));
		let page = Math.max(0, available - body.length - 1 - reserveHandled);
		const pages = explanation.length > page;
		// A pager line is optional; never trade the last explanation line for it.
		const pager = pages && !compact && page > 1;
		if (pager) page -= 1;
		this.pageSize = Math.max(1, page);
		const top = this.scroll = Math.min(this.scroll, Math.max(0, explanation.length - page));
		for (const line of explanation.slice(top, top + page)) {
			body.push({ text: theme.text(line), kind: "detail" });
		}
		if (pager) {
			body.push({ text: theme.muted(`${top + 1}–${Math.min(top + page, explanation.length)} of ${explanation.length} lines`), kind: "detail" });
		}
		body.push(actionLine);
		this.handledLines(turn, handled, body, width);
	}

	private fileIndex(turn: ChangeTurnInfo, path: string): number {
		const index = turn.files.findIndex(file => file.path === path);
		return index < 0 ? 0 : index + 1;
	}
}
