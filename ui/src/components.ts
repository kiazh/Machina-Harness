// Transcript and chrome widgets built from pi-tui primitives.

import { basename } from "node:path";
import {
	Box,
	type Component,
	CURSOR_MARKER,
	Editor,
    Key,
    matchesKey,
	type EditorOptions,
	type EditorTheme,
	type SelectItem,
	stripTerminalSequences,
	truncateToWidth,
	type TuiMouseEvent,
	visibleWidth,
	type TUI,
} from "@earendil-works/pi-tui";
import { ACTIVITY_INTERVAL_MS, type AnimationCoordinator, type AnimationSubscription } from "./design/motion.ts";
import { ICONS } from "./design/icons.ts";
import { hasTerminalPasteResidue, sanitizeDisplay, sanitizePastedText } from "./design/text.ts";
import { LIMITS } from "./design/tokens.ts";
import { transcriptInset } from "./design/spacing.ts";
import { Menu } from "./components/Menu.ts";
import { isOpaqueBackground, styleComposerText, surfaceLine, theme } from "./theme.ts";

export { Footer } from "./components/Footer.ts";

/**
 * Coordinator-driven thinking indicator: fixed-width dot frames plus an
 * elapsed counter derived from timestamps at render. No private ticker;
 * the shared coordinator owns the single render timer. Implements the same
 * start/stop/setMessage shape the transcript expects from a spinner.
 */
export class ThinkingIndicator implements Component {
	private sub: AnimationSubscription | undefined;
	private startedAt = 0;
	private baseMessage = "Thinking…";
	private readonly activity: AnimationCoordinator;

	constructor(activity: AnimationCoordinator) {
		this.activity = activity;
	}

	start(): void {
		if (this.sub) return;
		this.startedAt = Date.now();
		this.sub = this.activity.subscribe("thinking", ACTIVITY_INTERVAL_MS);
	}

	stop(): void {
		this.sub?.stop();
		this.sub = undefined;
	}

	setMessage(message: string): void {
		if (message) this.baseMessage = message.replace(/\s+\d+s$/, "");
	}

	render(width: number): string[] {
		if (!this.sub) return [];
		const dot = theme.accent(this.activity.frame(ICONS.workingFrames, ACTIVITY_INTERVAL_MS, this.startedAt));
		const secs = Math.floor((Date.now() - this.startedAt) / 1000);
		const base = this.baseMessage.replace(/[.\s]+$/, "");
		const msg = secs > 0 ? `${base} (${secs}s · esc to interrupt)` : base;
		const gutter = " ".repeat(transcriptInset(width));
		return [truncateToWidth(`${gutter}${dot} ${theme.muted(msg)}`, width)];
	}

	invalidate(): void {}
}

// Compact transcript widgets live in components/*. New code imports them
// directly; this facade keeps the shared transcript surface.
export { AssistantTurn } from "./components/AssistantTurn.ts";
export { ApprovalCard } from "./components/ApprovalCard.ts";
export type { ApprovalDecision, ApprovalDetails } from "./components/ApprovalCard.ts";
export { DiffBlock, styleDiffLine } from "./components/DiffBlock.ts";
export { Notice } from "./components/Notice.ts";
export { OutputStore, OutputViewer } from "./components/OutputViewer.ts";
export type { OutputRecord, OutputViewerOptions } from "./components/OutputViewer.ts";
export { formatToolArgs, ToolTurn } from "./components/ToolTurn.ts";
export type { ToolOutcome, ToolResultView } from "./components/ToolTurn.ts";
export { TurnSummary } from "./components/TurnSummary.ts";
export type { TurnSummaryInfo } from "./components/TurnSummary.ts";
export { UserTurn } from "./components/UserTurn.ts";


/** Actual session metadata supplied by the gateway. */
export interface SessionHeaderInfo {
	model: string;
	provider: string;
	cwd: string;
	branch: string;
	title?: string;
}

/** Basename of a cwd for the header's right-side workspace label. */
function workspaceName(cwd: string): string {
	return basename(cwd) || cwd;
}

/** One quiet context row: brand, session title, and workspace. */
export function header(_version: string, info?: SessionHeaderInfo): Component {
	return {
		render(width: number): string[] {
			const brand = isOpaqueBackground()
				? theme.accentBg(theme.onAccent(" MACHINA "))
				: theme.accentBold("MACHINA");
			const title = info?.title ? theme.muted(sanitizeDisplay(info.title)) : "";
			const where = info?.cwd ? theme.dim(sanitizeDisplay(workspaceName(info.cwd))) : "";
			const left = title ? `${brand}  ${title}` : brand;
			const room = Math.max(0, width - visibleWidth(where) - 1);
			const first = truncateToWidth(left, room, "…", true);
			const gap = Math.max(0, width - visibleWidth(first) - visibleWidth(where));
			return [truncateToWidth(`${first}${" ".repeat(gap)}${where}`, width)];
		},
		invalidate(): void {},
	};
}

/** Private Editor layout fields the popup flip needs (read-only use). */
interface EditorPopupLayout {
	renderedAutocompleteHeight: number;
}

/** A bounded view of the editor's rendered rows plus its row mapping. */
export interface ComposerWindow {
	/** Rendered rows, popup first when present; marker rows included. */
	rows: string[];
	/** Native body rows before windowing (excludes the popup). */
	bodyRows: number;
	/** First native body row index included in the window. */
	start: number;
	/** Visible native body rows (excludes marker rows). */
	contentRows: number;
	/** 1 when the window opens with an `↑ N more` marker row. */
	markerAbove: 0 | 1;
}

/** Window start that keeps the cursor row in view with trailing preference. */
function windowStart(bodyRows: number, contentRows: number, at: number): number {
	return Math.max(0, Math.min(bodyRows - contentRows, at - contentRows + 1));
}

/**
 * Bound the editor rows around the real cursor instead of clipping the tail:
 * the cursor row stays visible with preference for trailing context, while
 * hidden draft rows are disclosed with the same `↑ N more` / `↓ N more`
 * markers the editor's own borders use (review round 4, R4-094). Autocomplete
 * popup rows (already moved above the input) are preserved. The returned
 * mapping lets the mouse handler translate screen rows back to editor-native
 * rows across the window start and marker rows.
 */
export function composerWindow(rows: string[], budget: number, popupHeight = 0): ComposerWindow {
	const popup = popupHeight > 0 ? rows.slice(0, popupHeight) : [];
	const body = popupHeight > 0 ? rows.slice(popupHeight) : rows;
	if (body.length <= budget) {
		return { rows: [...popup, ...body], bodyRows: body.length, start: 0, contentRows: body.length, markerAbove: 0 };
	}
	const cap = Math.max(1, budget);
	const cursor = body.findIndex((line) => line.includes(CURSOR_MARKER) || line.includes("\x1b[7m"));
	const at = cursor < 0 ? body.length - 1 : cursor;
	if (cap <= 2) {
		// No room for a marker next to the cursor row: keep the cursor row.
		const start = windowStart(body.length, cap, at);
		return { rows: [...popup, ...body.slice(start, start + cap)], bodyRows: body.length, start, contentRows: cap, markerAbove: 0 };
	}
	// One probing window first: blank border rows never justify a marker, so
	// reclaim their budget for draft rows when nothing textual is hidden.
	const probe = windowStart(body.length, cap, at);
	const probeAbove = body.slice(0, probe).some((line) => line !== "");
	const probeBelow = body.slice(probe + cap).some((line) => line !== "");
	const markerCount = (probeAbove ? 1 : 0) + (probeBelow ? 1 : 0);
	if (markerCount === 0) {
		return { rows: [...popup, ...body.slice(probe, probe + cap)], bodyRows: body.length, start: probe, contentRows: cap, markerAbove: 0 };
	}
	const content = cap - markerCount;
	const start = windowStart(body.length, content, at);
	const end = start + content;
	const hiddenAbove = body.slice(0, start).filter((line) => line !== "").length;
	const hiddenBelow = body.slice(end).filter((line) => line !== "").length;
	const out: string[] = [];
	const markerAbove = hiddenAbove > 0 ? 1 : 0;
	if (markerAbove) out.push(theme.muted(`↑ ${hiddenAbove} more`));
	out.push(...body.slice(start, end));
	if (hiddenBelow > 0) out.push(theme.muted(`↓ ${hiddenBelow} more`));
	return { rows: [...popup, ...out], bodyRows: body.length, start, contentRows: content, markerAbove };
}

/**
 * Move trailing popup lines above the input block, Codex style. The Editor
 * renders `[top, text, bottom, ...popup]`; the composer shows
 * `[...popup, top, text, bottom]` so the `/` menu opens upward into the
 * transcript area instead of pushing past the footer.
 */
export function popAbove(lines: string[], popupHeight: number): string[] {
	if (!(popupHeight > 0) || popupHeight >= lines.length) return lines;
	return [...lines.slice(lines.length - popupHeight), ...lines.slice(0, lines.length - popupHeight)];
}

/**
 * pi-tui's Editor paints its fake caret by wrapping the grapheme under the
 * cursor in reverse video (`\x1b[7m<g>\x1b[0m`). When the app's blink phase is
 * off we unwrap it so the character shows through unchanged. This is the only
 * `\x1b[7m` the Editor emits, so the match is unambiguous.
 */
// eslint-disable-next-line no-control-regex -- matching the Editor's reverse-video caret is the point.
const COMPOSER_CARET_RE = /\x1b\[7m([\s\S]*?)\x1b\[0m/;
function stripFakeComposerCursor(line: string): string {
	return line.replace(COMPOSER_CARET_RE, "$1");
}

/**
 * Codex-style composer input: the pi-tui Editor with its rules replaced by
 * filled rows, so the input reads as one solid block instead of ruled lines.
 * Scroll position survives via a muted `↑ N more` marker.
 */
export class ComposerInput extends Editor {
    onPaste?: (text: string) => boolean;
    onSubmitBlocked?: () => boolean;
	/** ComposerBox's editable-row budget for the draft window (setMaxRows). */
	windowRows: number = LIMITS.composerMaxRows;
	/**
	 * App-owned caret blink phase. When false the fake caret cell renders as
	 * plain text (the character under it shows through); the zero-width IME
	 * marker from the base Editor is untouched. Defaults to always visible so
	 * the composer stays usable without a host.
	 */
	cursorBlockVisible: () => boolean = () => true;
    private attachmentPaste: string | undefined;

    override handleInput(data: string): void {
        // A terminal with mouse reporting can deliver a raw SGR mouse report if
        // the host did not decode it. Never let that leak into the draft.
        // eslint-disable-next-line no-control-regex -- raw mouse reports intentionally match ESC
        if (/\x1b\[<\d+(?:;\d+)*[Mm]/.test(data)) return;
        if (matchesKey(data, Key.enter) && this.onSubmitBlocked?.()) return;
        if (this.attachmentPaste !== undefined || data.includes("\x1b[200~")) {
            if (this.attachmentPaste === undefined) {
                const start = data.indexOf("\x1b[200~");
                if (start > 0) super.handleInput(data.slice(0, start));
                this.attachmentPaste = "";
                data = data.slice(start + 6);
            }
            this.attachmentPaste += data;
            const end = this.attachmentPaste.indexOf("\x1b[201~");
            if (end < 0) return;
            const text = sanitizePastedText(this.attachmentPaste.slice(0, end));
            const remaining = this.attachmentPaste.slice(end + 6);
            this.attachmentPaste = undefined;
            if (!this.onPaste?.(text)) super.handleInput(`\x1b[200~${text}\x1b[201~`);
            if (remaining) this.handleInput(remaining);
            return;
        }
        if (hasTerminalPasteResidue(data)) {
            // Non-bracketed paste (notably right-click paste on Windows) reaches
            // this branch as one text chunk. Keep its controls out of the draft.
            const clean = sanitizePastedText(data);
            if (clean) this.insertTextAtCursor(clean);
            return;
        }
        if (!this.onPaste?.(data)) super.handleInput(data);
    }

	override renderTopBorder(_width: number, hiddenLineCount: number): string {
		if (hiddenLineCount > 0) return theme.muted(`↑ ${hiddenLineCount} more`);
		return "";
	}

	override renderBottomBorder(_width: number, hiddenLineCount: number): string {
		if (hiddenLineCount > 0) return theme.muted(`↓ ${hiddenLineCount} more`);
		return "";
	}

	override render(width: number): string[] {
		const lines = super.render(width);
		const { renderedAutocompleteHeight: popupHeight } = this as unknown as EditorPopupLayout;
		// The base Editor always emits a top and a bottom border row. The
		// composer paints its own full-width rules, so drop those empty rows:
		// otherwise the prompt marker lands on the blank border and the draft
		// appears one row below it, indented. Non-empty scroll markers
		// (`↑/↓ N more`) are real information and stay.
		const popup = popupHeight > 0 ? lines.slice(lines.length - popupHeight) : [];
		const body = popupHeight > 0 ? lines.slice(0, lines.length - popupHeight) : lines.slice();
		const top = body.shift();
		const bottom = body.pop();
		if (top !== undefined && visibleWidth(top) > 0) body.unshift(top);
		if (bottom !== undefined && visibleWidth(bottom) > 0) body.push(bottom);
		const blockVisible = this.cursorBlockVisible();
		const styled = body.map((line) => {
			const colored = styleComposerText(line);
			return blockVisible ? colored : stripFakeComposerCursor(colored);
		});
		return [...popup, ...styled];
	}

	override handleMouse(event: TuiMouseEvent): ReturnType<Editor["handleMouse"]> {
		const { renderedAutocompleteHeight: popupHeight } = this as unknown as EditorPopupLayout;
		const window = composerWindow(this.render(event.width), this.windowRows, popupHeight);
		const clipped = window.start > 0 || window.contentRows !== window.bodyRows;
		if (!(popupHeight > 0) && !clipped) {
			// The composer drops the editor's empty top border row; the
			// editor's own hit-testing still counts that row.
			return super.handleMouse({ ...event, y: event.y + 1 });
		}
		// The popup is drawn above the input and the outer window may have
		// sliced body rows: map the clicked row back to the Editor-native
		// coordinates before delegating. Native order is
		// [top border, content, bottom border, popup].
		const popupCommand = event.y < popupHeight && /^\/[\w-]*$/.test(this.getText().trim());
		let y: number;
		if (event.y < popupHeight) {
			y = window.bodyRows + 2 + event.y;
		} else {
			const contentIndex = event.y - popupHeight - window.markerAbove;
			if (contentIndex < 0 || contentIndex >= window.contentRows) return undefined; // `↑/↓ more` marker row
			y = 1 + window.start + contentIndex;
		}
		const result = super.handleMouse({ ...event, y });
		// Picker-first actions open as soon as the command is selected.
		if (popupCommand && !this.isShowingAutocomplete() && ["/delegate", "/delete"].includes(this.getText().trim())) {
			this.onSubmit?.(this.getText());
		}
		return result;
	}
}

/** Muted composer placeholder (the Editor cannot render its own). */
export const COMPOSER_PLACEHOLDER = "Ask anything, or type / for commands.";
const COMPOSER_TITLE = "";

/**
 * Docked input on a raised surface. Rendering and hit-testing share the
 * same editor width so wrapping and cursor placement agree at the edge.
 */
export class ComposerBox extends Box {
	readonly editor: ComposerInput;
	placeholder = COMPOSER_PLACEHOLDER;
	imageShortcut = "Alt+V";
	onPasteImage?: () => void;
	private readonly tui: TUI;
	private degradedMode = false;
	/** Last measured chrome height, keyed by the inputs that can change it. */
	private measuredRows: { width: number; text: string; windowRows: number; degraded: boolean; popupHeight: number; rows: number } | undefined;

	constructor(tui: TUI, editorTheme: EditorTheme, options?: EditorOptions) {
		super(0, 0, theme.userBg);
		this.tui = tui;
		// The composer owns the left gutter (`❯ ` / two spaces); the inner
		// Editor must not add a second one or the draft starts one cell too
		// far right.
		this.editor = new ComposerInput(tui, editorTheme, { ...options, paddingX: 0 });
		this.addChild(this.editor);
	}

	/** Viewport-derived editable-row budget (shell density, rules included). */
	setMaxRows(rows: number): void {
		const chrome = (this.tui.terminal.rows ?? 24) >= 12 ? 2 : 0;
		this.editor.windowRows = Math.max(1, rows - chrome);
	}

	/** Safe degraded mode: draft preserved, editing deferred until resize. */
	setDegradedMode(degraded: boolean): void {
		this.degradedMode = degraded;
	}

	/** Chrome height without a second full render when nothing changed. */
	measureRows(width: number): number {
		const text = this.editor.getText();
		const popupHeight = (this.editor as unknown as EditorPopupLayout).renderedAutocompleteHeight ?? 0;
		const cached = this.measuredRows;
		if (
			cached &&
			cached.width === width &&
			cached.text === text &&
			cached.windowRows === this.editor.windowRows &&
			cached.degraded === this.degradedMode &&
			cached.popupHeight === popupHeight
		) {
			return cached.rows;
		}
		return this.render(width).length;
	}

	override render(width: number): string[] {
		const prefix = (index: number): string => index === 0 ? theme.accent("❯ ") : "  ";
		const total = Math.max(0, width);
		const { framed, inset, outer, editorWidth: inner } = this.geometry(total);
		const margin = " ".repeat(inset);
		const imageKey = this.imageAction();
		const room = imageKey ? outer - visibleWidth(imageKey) : 0;
		const transparent = !isOpaqueBackground();
		const paint = (line: string) => `${margin}${surfaceLine(truncateToWidth(line, outer, "…", true), outer, true)}`;
		// Transparent canvas: a real box (corners, side rules) so the input is
		// an object on the terminal instead of floating text.
		const boxTop = (): string => {
			const width = Math.max(0, outer - 4);
			const image = imageKey ? ` ${imageKey.trim()} ` : "";
			const fill = Math.max(0, width - visibleWidth(image));
			const middle = truncateToWidth(
				`${theme.border("─".repeat(fill))}${image ? theme.accent(image) : ""}`,
				width,
				"",
			);
			return `${margin}${theme.border("╭─")}${middle}${theme.border("─╮")}`;
		};
		const boxBottom = (): string => `${margin}${theme.border(`╰${"─".repeat(Math.max(0, outer - 2))}╯`)}`;
		const heading = transparent ? boxTop() : paint(`${room >= 3 ? `${" ".repeat(room)}${theme.accent(imageKey)}` : ""}`);
		const withDock = (rows: string[]): string[] =>
			framed
				? [
					heading,
					...rows.map(line => paint(`  ${truncateToWidth(line, outer - 4, "…")}`)),
					transparent ? boxBottom() : paint(""),
				]
				: rows;
		if (this.degradedMode) {
			const rows = withDock([truncateToWidth(
				`${theme.accent("❯ ")}${theme.muted(truncateToWidth("Too small - resize to continue (draft kept)", inner))}`,
				total,
			)]);
			this.rememberMeasurement(width, rows.length);
			return rows;
		}
		let body: string[];
		let inputStart = 0;
		if (this.editor.getText() === "") {
			body = [theme.muted(truncateToWidth(this.placeholder, inner))];
		} else {
			const rendered = this.editor.render(inner);
			const { renderedAutocompleteHeight: popupHeight } = this.editor as unknown as EditorPopupLayout;
			const window = composerWindow(rendered, this.editor.windowRows, popupHeight);
			body = window.rows;
			inputStart = Math.min(body.length - 1, popupHeight + window.markerAbove);
			// A retained `↑/↓ N more` marker may sit on the first content row;
			// the prompt marker belongs on the draft line below it.
			while (inputStart < body.length - 1 && /^[↑↓]\s/.test(stripTerminalSequences(body[inputStart] ?? "").trim())) {
				inputStart++;
			}
		}
		const rows = withDock(body.map((line, index) =>
			truncateToWidth(`${index === inputStart ? prefix(0) : "  "}${line}`, total),
		));
		this.rememberMeasurement(width, rows.length);
		return rows;
	}

	private geometry(width: number) {
		const framed = width >= 8 && (this.tui.terminal.rows ?? 24) >= 12;
		const inset = framed ? transcriptInset(width) : 0;
		const outer = width - inset * 2;
		return { framed, inset, outer, editorWidth: Math.max(1, outer - (framed ? 6 : 2)) };
	}

	private imageAction(): string {
		return this.onPasteImage ? `Paste image · ${this.imageShortcut}  ` : "";
	}

	private rememberMeasurement(width: number, rows: number): void {
		this.measuredRows = {
			width,
			text: this.editor.getText(),
			windowRows: this.editor.windowRows,
			degraded: this.degradedMode,
			popupHeight: (this.editor as unknown as EditorPopupLayout).renderedAutocompleteHeight ?? 0,
			rows,
		};
	}

	override handleMouse(event: TuiMouseEvent): ReturnType<Box["handleMouse"]> {
		const { framed, inset, outer, editorWidth } = this.geometry(event.width);
		const imageAction = visibleWidth(this.imageAction());
		if (framed && this.onPasteImage && outer - COMPOSER_TITLE.length - imageAction >= 3 && event.type === "click" && event.button === "left"
			&& event.y === 0 && event.x >= inset + outer - imageAction && event.x < inset + outer) {
			this.onPasteImage();
			return { handled: true, render: true, target: {
				component: this, originX: event.screenX - event.x, originY: event.screenY - event.y,
				width: event.width, height: event.height,
			} };
		}
		const topRule = framed ? 1 : 0;
		const xOffset = inset + (framed ? 4 : 2);
		if (event.x < xOffset || event.x >= xOffset + editorWidth || event.y < topRule) return undefined;
		return super.handleMouse({
			...event,
			y: event.y - topRule,
			x: event.x - xOffset,
			width: editorWidth,
		});
	}
}

/**
 * A titled, filterable list shown as an overlay (e.g. the session picker).
 * Implemented over Menu: every picker owns a real filter buffer matched
 * against label, value and description. Kept for existing callers; new code
 * uses Menu directly. The row budget follows Menu's documented bounded
 * default ({@link DEFAULT_MENU_VISIBLE_ROWS}), clamped by the live viewport;
 * a caller may pass an explicit cap, or `"full"` to opt out for a huge view.
 */
export class Picker implements Component {
	private readonly menu: Menu;

	constructor(title: string, items: SelectItem[], onSelect: (item: SelectItem) => void, onCancel: () => void, visibleRows?: number | "full", hint = "enter select · esc back", tui?: TUI, count?: string | false) {
		this.menu = new Menu(title, items, { visibleRows, hint, tui, count });
		this.menu.onSelect = onSelect;
		this.menu.onCancel = onCancel;
	}

	handleInput(data: string): void {
		this.menu.handleInput(data);
	}

	render(width: number): string[] {
		return this.menu.render(width);
	}

	invalidate(): void {
		this.menu.invalidate();
	}
}


export { DEFAULT_MENU_VISIBLE_ROWS, Menu } from "./components/Menu.ts";
export type { MenuOptions } from "./components/Menu.ts";
export { SecretForm, SecretInput, TextForm } from "./components/Form.ts";
export type { SecretFormOptions, TextFormOptions } from "./components/Form.ts";
