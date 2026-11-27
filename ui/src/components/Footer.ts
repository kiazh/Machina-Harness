// Quiet activity/learning footer: status line + hints + bottom-right square.
// Timer ownership lives in the AnimationCoordinator; this widget only holds
// the acknowledgment state (last pulse, active window) and selects frames
// at render. No transcript notices, no focus theft.

import { truncateToWidth, visibleWidth, type Component } from "@earendil-works/pi-tui";
import { ICONS } from "../design/icons.ts";
import { ACTIVITY_INTERVAL_MS, type AnimationCoordinator, type AnimationSubscription } from "../design/motion.ts";
import { transcriptInset } from "../design/spacing.ts";
import { sanitizeLabelText } from "../design/text.ts";
import { chromeInner, repairFill, surfaceLine, theme } from "../theme.ts";

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

	/** A dark live strip plus the normal footer while work is live. */
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

	/** A compact two-row equalizer: no baseline, no playhead. */
	private waveBlock(columns: number, rows: number, tick: number): string[] {
		const partial = ["", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"];
		const shape = [4, 9, 14, 7, 12, 5, 15, 10, 3, 11, 6, 16, 8, 13];
		const out = Array.from({ length: Math.max(1, rows) }, () => [] as string[]);
		for (let column = 0; column < Math.max(0, columns); column += 1) {
			let cells = shape[(column + tick) % shape.length]!;
			for (let row = out.length - 1; row >= 0; row -= 1) {
				const filled = Math.max(0, Math.min(8, cells));
				const glyph = filled === 0 ? " " : filled === 8 ? "█" : partial[filled]!;
				out[row]!.push(glyph === " " ? " " : theme.accent(glyph));
				cells -= 8;
			}
		}
		return out.map((row) => row.join(""));
	}

	/** Start at zero and show the latest context percentage once reported. */
	private tokenCounter(): string {
		const count = this.compactTokens(Math.max(0, this.tokens));
		const percent = this.contextPercent !== null && Number.isFinite(this.contextPercent)
			? ` (${Math.max(0, Math.round(this.contextPercent))}%)`
			: "";
		return `${theme.muted(count)}${theme.dim(percent)}`;
	}

	private darkLine(line: string, width: number): string {
		const total = Math.max(0, Math.floor(width));
		const padded = truncateToWidth(line, total, "", true);
		return repairFill("background", `${padded}${" ".repeat(Math.max(0, total - visibleWidth(padded)))}`);
	}

	private runningRows(width: number): string[] {
		const fit = chromeInner(width);
		const total = Math.max(0, width);
		const inset = Math.min(fit, transcriptInset(width));
		const tick = this.reducedMotion
			? 0
			: Number(this.activity.frame(["0", "1", "2", "3", "4", "5", "6", "7"], ACTIVITY_INTERVAL_MS, this.workingStart)) || 0;
		const waveWidth = Math.min(14, Math.max(8, Math.floor(fit * 0.1)));
		const wave = this.waveBlock(waveWidth, this.compact ? 1 : 2, tick);
		const left = `${" ".repeat(inset)}${this.tokenCounter()}`;
		const interrupt = theme.dim(this.compact || fit < 44 ? "esc×2" : "esc×2 to interrupt");
		if (this.compact) {
			return [
				this.darkLine(this.between(left, `${wave[0] ?? ""}  ${interrupt}`, fit), total),
			];
		}
		return [
			this.darkLine(this.between("", wave[0] ?? "", fit), total),
			this.darkLine(this.between("", wave[1] ?? "", fit), total),
			this.darkLine(this.between(left, interrupt, fit), total),
		];
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
		const displayTokens = Math.max(0, this.tokens);
		const percent = this.contextPercent !== null && Number.isFinite(this.contextPercent)
			? ` (${Math.max(0, Math.round(this.contextPercent))}%)`
			: "";
		// The live strip owns the counter; repeating it in the footer is noise.
		const tokens = this.isLive ? "" : theme.muted(` · ${this.compactTokens(displayTokens)}${percent}`);
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
		const row = truncateToWidth(`${left}${" ".repeat(gap)}${right}`, fit);
		return this.isLive ? this.darkLine(row, width) : surfaceLine(row, width);
	}

	render(width: number): string[] {
		if (width <= 0) return [""];
		const footer = this.footerRow(width);
		if (!this.isLive) return [footer];
		return [...this.runningRows(width), footer];
	}

	invalidate(): void {}
}
