// Assistant prose in a quiet square frame. The box is content-aware (plan §6):
// width follows the measured Markdown rows (compact content-fit -> medium ->
// full pane width) and height is the real rendered row count, never guessed.
// Streaming deltas extend the block; only its one-cell heading marker
// animates while text is arriving.
/* eslint-disable no-control-regex -- terminal escape boundaries are the subject matter here. */

import { Markdown, type Component } from "@earendil-works/pi-tui";
import { ACTIVITY_INTERVAL_MS, type ActivitySource } from "../design/motion.ts";
import {
	chooseResponseGeometry,
	contentPrefersWide,
	renderAssistantFrame,
	type ResponseGeometry,
	type ResponseMeasure,
	type ResponseWidthClass,
} from "../design/responseLayout.ts";
import { sanitizeDisplay } from "../design/text.ts";
import { BLOCK_GAP } from "../design/tokens.ts";
import { markdownTheme } from "../theme.ts";
import { repairFill, theme } from "../theme.ts";

/** Per-frame delta coalescing: one sanitize/Markdown update per animation frame. */
const STREAM_FLUSH_MS = 16;

/**
 * Index after the last byte that can be sanitized without risking a control
 * sequence split across two streamed chunks. An unterminated OSC/APC/CSI/ESC
 * suffix is held as `carry` until the next delta completes it; visible text
 * before it is safe to sanitize now.
 */
function safeEscapeBoundary(text: string): number {
	const length = text.length;
	const oscEnd = (from: number): number => {
		for (let index = from; index < length; index++) {
			const code = text.charCodeAt(index);
			if (code === 0x07 || code === 0x9c) return index + 1;
			if (code === 0x1b && text.charCodeAt(index + 1) === 0x5c) return index + 2;
		}
		return -1;
	};
	const csiEnd = (start: number): number => {
		const match = /^[\x1b\x9b]\[[0-?]*[ -/]*[@-~]/.exec(text.slice(start));
		if (match) return start + match[0].length;
		return /^[\x1b\x9b]\[[0-?]*[ -/]*$/.test(text.slice(start)) ? -1 : start + 1;
	};
	let index = 0;
	while (index < length) {
		const code = text.charCodeAt(index);
		if (code === 0x1b) {
			if (index + 1 >= length) return index;
			const next = text.charCodeAt(index + 1);
			if (next === 0x5d) {
				const end = oscEnd(index + 2);
				if (end < 0) return index;
				index = end;
				continue;
			}
			if (next === 0x50 || next === 0x58 || next === 0x5e || next === 0x5f) {
				const end = oscEnd(index + 2);
				if (end < 0) return index;
				index = end;
				continue;
			}
			if (next === 0x5b) {
				const end = csiEnd(index);
				if (end < 0) return index;
				index = end;
				continue;
			}
			if (next === 0x28 || next === 0x29 || next === 0x23) {
				if (index + 2 >= length) return index;
				index += 3;
				continue;
			}
			index += 2;
			continue;
		}
		if (code === 0x9d || code === 0x90 || code === 0x98 || code === 0x9e || code === 0x9f) {
			const end = oscEnd(index + 1);
			if (end < 0) return index;
			index = end;
			continue;
		}
		if (code === 0x9b) {
			const end = csiEnd(index);
			if (end < 0) return index;
			index = end;
			continue;
		}
		index += 1;
	}
	return length;
}

export class AssistantTurn implements Component {
	private readonly md: Markdown;
	private content: string;
	/** Sanitized projection of `content`; measurement only ever sees this. */
	private display = "";
	/** Raw streamed suffix held because it may end inside a control sequence. */
	private sanitizeCarry = "";
	/** Deltas not sanitized yet; committed at most once per frame. */
	private pending = "";
	private flushTimer: ReturnType<typeof setTimeout> | undefined;
	/** Bumps on every committed append/setText; keys the geometry cache. */
	private revision = 0;
	private cachedGeometry: { revision: number; paneWidth: number; geometry: ResponseGeometry } | undefined;
	private readonly cachedRows = new Map<number, string[]>();
	/** Last class at the same pane width, for monotonic stream growth. */
	private previousClass: { widthClass: ResponseWidthClass; paneWidth: number } | undefined;
	private readonly activity: ActivitySource | undefined;
	private streaming = false;
	private startedAt = 0;

	constructor(text = "", activity?: ActivitySource) {
		this.content = text;
		this.activity = activity;
		// Untrusted content path: assistant prose can echo repo files, web
		// pages, model output. Sanitize the ASSEMBLED content (not per-delta)
		// so a control sequence split across deltas cannot evade the filter
		// (wave-3 hotfix; same sanitizer as UserTurn/ToolTurn/ApprovalCard).
		// The incremental pass keeps an incomplete escape tail as carry, so
		// sanitizing chunk-by-chunk is equivalent to one assembled pass.
		this.display = this.sanitizeStreamChunk(text);
		this.md = new Markdown(this.display, 0, 0, markdownTheme, { color: theme.text });
	}

	setStreaming(active: boolean): void {
		if (this.streaming === active) return;
		this.streaming = active;
		if (active) this.startedAt = Date.now();
		else this.flush();
	}

	append(delta: string): void {
		if (delta.length === 0) return;
		this.content += delta;
		this.pending += delta;
		this.scheduleFlush();
	}

	/** Reconcile to canonical final text (replaces incompatible streamed content). */
	setText(text: string): void {
		this.cancelFlush();
		this.pending = "";
		this.sanitizeCarry = "";
		this.content = text;
		this.display = this.sanitizeStreamChunk(text);
		this.md.setText(this.display);
		this.revision++;
		this.cachedRows.clear();
		// The canonical text is a new sizing basis: stream growth history ends.
		this.previousClass = undefined;
	}

	getText(): string {
		return this.content;
	}

	/** Sanitize one streamed chunk, holding an incomplete escape tail for later. */
	private sanitizeStreamChunk(chunk: string): string {
		const raw = this.sanitizeCarry + chunk;
		const boundary = safeEscapeBoundary(raw);
		this.sanitizeCarry = raw.slice(boundary);
		return sanitizeDisplay(raw.slice(0, boundary));
	}

	private scheduleFlush(): void {
		if (this.flushTimer !== undefined) return;
		// Coalesce bursts of gateway deltas into one sanitize + Markdown update.
		// The final canonical setText and every render flush synchronously, so
		// visible text is never delayed past the next frame.
		this.flushTimer = setTimeout(() => {
			this.flushTimer = undefined;
			this.flush();
		}, STREAM_FLUSH_MS);
		this.flushTimer.unref?.();
	}

	private cancelFlush(): void {
		if (this.flushTimer === undefined) return;
		clearTimeout(this.flushTimer);
		this.flushTimer = undefined;
	}

	/** Commit pending deltas now (render/measure/canonical completion). */
	private flush(): void {
		this.cancelFlush();
		if (this.pending.length === 0) return;
		const pending = this.pending;
		this.pending = "";
		this.display += this.sanitizeStreamChunk(pending);
		this.md.setText(this.display);
		this.revision++;
		this.cachedRows.clear();
	}

	/** Re-resolve theme colors (render is lazy; text was never baked). */
	refreshSkin(): void {
		this.invalidate();
	}

	/**
	 * Chosen geometry for the current content at an allocated pane width.
	 * Cached by (content revision, pane width); the monotonic `previous` class
	 * resets when the pane width changes or canonical text is set.
	 */
	responseGeometry(paneWidth: number): ResponseGeometry {
		this.flush();
		const width = Math.max(0, Math.floor(paneWidth));
		const cached = this.cachedGeometry;
		if (cached && cached.revision === this.revision && cached.paneWidth === width) {
			return cached.geometry;
		}
		// A real pane-width change abandons the previous allocation's growth.
		if (this.previousClass && this.previousClass.paneWidth !== width) this.previousClass = undefined;
		const geometry = chooseResponseGeometry(width, this.measure(), this.previousClass);
		this.previousClass = { widthClass: geometry.widthClass, paneWidth: width };
		this.cachedGeometry = { revision: this.revision, paneWidth: width, geometry };
		return geometry;
	}

	render(width: number): string[] {
		this.flush();
		const cached = this.cachedRows.get(width);
		if (!this.streaming && cached) return cached;
		const geometry = this.responseGeometry(width);
		if (geometry.span <= 0) return [...BLOCK_GAP];
		const rows = this.md.render(geometry.contentWidth);
		const frame = this.streaming
			? this.activity?.frame(["·", "•", "●", "•"], ACTIVITY_INTERVAL_MS, this.startedAt) ?? "·"
			: " ";
		const output = [
			...BLOCK_GAP,
			...renderAssistantFrame(rows, geometry, theme.accent(frame), {
				border: theme.border,
				accentBold: theme.accentBold,
				fill: (line) => repairFill("assistantBg", theme.assistantBg(line)),
				dim: theme.dim,
			}),
		];
		if (!this.streaming) {
			if (this.cachedRows.size >= 4) this.cachedRows.clear();
			this.cachedRows.set(width, output);
		}
		return output;
	}

	/** Measurement hook backed by the real pi-tui Markdown renderer. */
	private measure(): ResponseMeasure {
		this.flush();
		return {
			renderRows: (contentWidth: number) => this.md.render(Math.max(1, Math.floor(contentWidth))),
			prefersWide: () => contentPrefersWide(this.display),
		};
	}

	invalidate(): void {
		this.md.invalidate();
		this.cachedRows.clear();
	}

	/** Commit any pending streamed deltas and drop the frame timer. */
	dispose(): void {
		this.flush();
	}
}
