// Quiet activity/learning footer: status line + hints + bottom-right square.
// Timer ownership lives in the AnimationCoordinator; this widget only holds
// the acknowledgment state (last pulse, active window) and selects frames
// at render. No transcript notices, no focus theft.

import { truncateToWidth, visibleWidth, type Component } from "@earendil-works/pi-tui";
import { ICONS } from "../design/icons.ts";
import { ACTIVITY_INTERVAL_MS, type AnimationCoordinator, type AnimationSubscription } from "../design/motion.ts";
import { sanitizeLabelText } from "../design/text.ts";
import { chromeInner, surfaceLine, theme } from "../theme.ts";

/** Cooldown between learning acknowledgments (ms). */
const GROWTH_COOLDOWN_MS = 30_000;
/** Visible acknowledgment lifetime (ms). */
const GROWTH_DURATION_MS = 800;
/** Growth frame cadence (ms). */
const GROWTH_INTERVAL_MS = ACTIVITY_INTERVAL_MS;

type FooterStatus = "ready" | "working" | "offline" | "awaiting_approval" | "stopping" | "cleanup_pending";

export class Footer implements Component {
	cwd = "";
	branch = "";
	model = "";
	tokens = 0;
	/** Kiro-native current-context size; 0 hides it and keeps the session total. */
	contextTokens = 0;
	/** Kiro-native context percentage, when the gateway reported one. */
	contextPercent: number | null = null;
	mode = "ask";
	/** Subagents in the visible session's family (0 hides the segment). */
	subagents = 0;
	/**
	 * Workspace key hints (e.g. "Alt+E files · Alt+1/2/3 layout"), set by
	 * the App only while the workspace is available. Hidden first when the
	 * row runs out of room; compact viewports drop it entirely.
	 */
	workspaceHints = "";
	/** Transient hint while a two-key shortcut is waiting for its letter. */
	keyHint = "";
	private currentStatus: FooterStatus = "ready";
	/** Compact single-row mode for short viewports (hints hide first). */
	compact = false;

	private readonly activity: AnimationCoordinator;
	private lastGrowthPulse = -Infinity;
	private growthStart = -Infinity;
	private growthSub: AnimationSubscription | undefined;
	private workingSub: AnimationSubscription | undefined;
	private workingStart = -Infinity;

	constructor(activity: AnimationCoordinator) {
		this.activity = activity;
	}

	get status(): FooterStatus {
		return this.currentStatus;
	}

	set status(status: FooterStatus) {
		this.currentStatus = status;
		if (status === "working") {
			// Text deltas retire the thinking indicator. Keep render demand
			// for the whole visible working phase, without restarting on deltas.
			if (!this.workingSub) {
				this.workingStart = Date.now();
				this.workingSub = this.activity.subscribe("footer:working", ACTIVITY_INTERVAL_MS);
			}
		} else {
			this.workingSub?.stop();
			this.workingSub = undefined;
		}
	}

	get reducedMotion(): boolean {
		return this.activity.isReducedMotion;
	}

	set reducedMotion(enabled: boolean) {
		this.activity.setReducedMotion(enabled);
	}

	/** A pixel wave plus the normal footer while work is live. */
	measureRows(_width: number): number {
		if (!this.isLive) return 1;
		return this.compact ? 2 : 4;
	}

	private get isLive(): boolean {
		return this.currentStatus === "working"
			|| this.currentStatus === "stopping"
			|| this.currentStatus === "cleanup_pending";
	}

	/**
	 * A bounded acknowledgment; frequent evidence never restarts or queues it.
	 * Growth from non-visible sessions never reaches here (App filters).
	 */
	pulseGrowth(_requestRender?: () => void): boolean {
		const now = Date.now();
		if (this.status === "offline" || now - this.lastGrowthPulse < GROWTH_COOLDOWN_MS) return false;
		this.lastGrowthPulse = now;
		this.growthStart = now;
		this.growthSub?.stop();
		this.growthSub = this.activity.subscribe("growth", GROWTH_INTERVAL_MS, GROWTH_DURATION_MS);
		return true;
	}

	/** Retire motion when the app exits, disconnects or changes visible session. */
	dispose(): void {
		this.workingSub?.stop();
		this.workingSub = undefined;
		this.growthSub?.stop();
		this.growthSub = undefined;
		this.growthStart = -Infinity;
		this.lastGrowthPulse = -Infinity;
	}

	private get growthActive(): boolean {
		if (!this.growthSub) return false;
		if (Date.now() - this.growthStart >= GROWTH_DURATION_MS) {
			this.growthSub.stop();
			this.growthSub = undefined;
			return false;
		}
		return true;
	}

	private pulsingSquare(startedAt: number): string {
		// Brightness changes, never geometry: one fixed cell, a calm rise/fall.
		// Reduced motion retains a clear lit acknowledgment without ticking.
		if (this.reducedMotion) return theme.accent(ICONS.growthStatic);
		return this.activity.frame([
			theme.dim(ICONS.growthStatic),
			theme.muted(ICONS.growthStatic),
			theme.accent(ICONS.growthStatic),
			theme.muted(ICONS.growthStatic),
		], ACTIVITY_INTERVAL_MS, startedAt);
	}

	private between(left: string, right: string, width: number): string {
		const room = Math.max(0, width - visibleWidth(right) - 2);
		const first = truncateToWidth(left, room, "…", true);
		return truncateToWidth(`${first}${" ".repeat(Math.max(0, room - visibleWidth(first)))}  ${right}`, width);
	}

	/** Short token counts keep the context gauge inside the footer corner. */
	private compactTokens(value: number): string {
		if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
		if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
		return String(value);
	}

	/**
	 * Block waveform columns. Each column is up to `rows * 8` levels tall so
	 * two terminal rows still read as a moving equalizer, not a flat line.
	 */
	private waveBody(columns: number, rows: number, tick: number): string[] {
		if (columns <= 0 || rows <= 0) return Array.from({ length: Math.max(0, rows) }, () => "");
		const partial = ["", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"];
		const out = Array.from({ length: rows }, () => [] as string[]);
		for (let column = 0; column < columns; column += 1) {
			const primary = Math.abs(Math.sin(column * 0.5 + tick * 0.62));
			const secondary = Math.abs(Math.sin(column * 0.17 - tick * 0.29));
			const envelope = 0.45 + 0.55 * Math.abs(Math.sin(column * 0.11 + tick * 0.05));
			const strength = Math.max(0, Math.min(1, envelope * (primary * 0.72 + secondary * 0.28)));
			let cells = Math.round(strength * rows * 8);
			for (let row = rows - 1; row >= 0; row -= 1) {
				const filled = Math.max(0, Math.min(8, cells));
				const glyph = filled === 0 ? " " : filled === 8 ? "█" : partial[filled]!;
				out[row]!.push(glyph === " " ? " " : theme.accent(glyph));
				cells -= 8;
			}
		}
		return out.map((row) => row.join(""));
	}

	private runningRows(width: number): string[] {
		const fit = chromeInner(width);
		const total = Math.max(0, width);
		const bodyRows = this.compact ? 0 : 2;
		const waveWidth = Math.min(
			Math.max(8, Math.floor(fit * 0.4)),
			Math.max(8, fit - 24),
		);
		const tick = this.reducedMotion
			? 0
			: Number(this.activity.frame(["0", "1", "2", "3", "4", "5", "6", "7"], ACTIVITY_INTERVAL_MS, this.workingStart)) || 0;
		const body = this.waveBody(waveWidth, bodyRows, tick);
		const elapsed = this.reducedMotion
			? 0
			: this.workingStart > 0
				? Math.max(0, Math.round((Date.now() - this.workingStart) / 1000))
				: 0;
		const clock = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
		const displayTokens = this.contextTokens > 0 ? this.contextTokens : this.tokens;
		const context = displayTokens > 0
			? `${this.compactTokens(displayTokens)}${this.contextTokens > 0 ? " ctx" : ""}${
				this.contextTokens > 0 && this.contextPercent !== null ? ` (${this.contextPercent.toFixed(1)}%)` : ""
			}`
			: "";
		const stop = fit >= 46 ? "esc×2 stop" : "esc stop";
		const parts = [clock, context, stop].filter(Boolean);
		const right = theme.muted(parts.join(" · "));
		const available = Math.max(0, fit - waveWidth - 2);
		const status = visibleWidth(right) <= available
			? right
			: theme.muted(available >= 12 ? (fit >= 46 ? "esc×2 stop" : "esc stop") : "esc");
		const baselineWidth = Math.max(0, Math.min(waveWidth, fit - visibleWidth(status) - 2));
		const marker = baselineWidth > 2
			? this.reducedMotion
				? Math.floor(baselineWidth / 2)
				: (Math.floor((Date.now() - this.workingStart) / 240) % baselineWidth + baselineWidth) % baselineWidth
			: -1;
		const baseline = Array.from({ length: baselineWidth }, (_, index) => (
			index === marker ? theme.accent("▲") : theme.dim("▁")
		)).join("");
		const rows = body.map((line) => surfaceLine(line, total));
		rows.push(surfaceLine(this.between(baseline, status, fit), total));
		return rows;
	}

	private footerRow(width: number): string {
		const square = this.growthActive ? this.pulsingSquare(this.growthStart)
			: this.workingSub ? this.pulsingSquare(this.workingStart) : theme.muted(ICONS.idleSquare);
		const status = this.status === "working"
			? theme.accent(`${this.activity.frame(ICONS.toolActiveFrames, ACTIVITY_INTERVAL_MS)} working`)
			: this.status === "offline" ? theme.error("offline")
			: this.status === "awaiting_approval" ? theme.warning("awaiting approval")
			: this.status === "cleanup_pending" ? theme.warning("cleanup pending")
			: this.status === "stopping" ? theme.warning("stopping") : theme.muted("ready");
		const displayTokens = this.contextTokens > 0 ? this.contextTokens : this.tokens;
		const contextSuffix =
			this.contextTokens > 0 && this.contextPercent !== null
				? ` (${this.contextPercent.toFixed(1)}% ctx)`
				: "";
		const tokens = displayTokens > 0
			? theme.muted(
					` · ${displayTokens.toLocaleString("en-US")}${
						this.contextTokens > 0 ? " ctx tokens" : " tokens"
					}${contextSuffix}`,
				)
			: "";
		const agents = this.subagents > 0
			? theme.muted(` · ${this.subagents} subagent${this.subagents === 1 ? "" : "s"}`)
			: "";
		const tail = `${status}${agents}${tokens}`;
		const metadata = [this.model, this.branch, this.mode]
			.map((part, index) => part ? [theme.muted, theme.dim, theme.muted][index]!(sanitizeLabelText(part)) : "")
			.filter(Boolean)
			.join(theme.dim(" · "));
		const core = metadata ? `${metadata} ${theme.dim("·")} ${tail}` : tail;
		const hint = this.keyHint ? theme.accent(sanitizeLabelText(this.keyHint)) : theme.dim("? shortcuts · / commands");
		const fit = chromeInner(width);
		const room = Math.max(0, fit - 2);
		let left = core;
		if (visibleWidth(left) > room) {
			const budget = Math.max(0, room - visibleWidth(tail) - 3);
			left = budget >= 4 ? `${truncateToWidth(metadata, budget)} / ${tail}` : truncateToWidth(tail, room);
		}
		const baseRight = !this.compact && visibleWidth(left) + visibleWidth(hint) + 5 <= fit
			? `${hint}  ${square}` : square;
		// Workspace hints sit before the shortcuts hint and are the first
		// segment dropped when the row is too tight (plan §12 discoverability).
		let right = baseRight;
		if (this.workspaceHints && !this.compact) {
			const ws = theme.dim(sanitizeLabelText(this.workspaceHints));
			const withWs = `${ws} · ${baseRight}`;
			if (visibleWidth(left) + visibleWidth(withWs) + 5 <= fit) right = withWs;
		}
		const gap = Math.max(0, fit - visibleWidth(left) - visibleWidth(right));
		return surfaceLine(truncateToWidth(`${left}${" ".repeat(gap)}${right}`, fit), width);
	}

	render(width: number): string[] {
		if (width <= 0) return [""];
		const footer = this.footerRow(width);
		if (!this.isLive) return [footer];
		return [...this.runningRows(width), footer];
	}

	invalidate(): void {}
}
