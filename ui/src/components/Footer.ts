// Quiet activity/learning footer: status line + hints + bottom-right square.
// Timer ownership lives in the AnimationCoordinator; this widget only holds
// the acknowledgment state (last pulse, active window) and selects frames
// at render. No transcript notices, no focus theft.

import { truncateToWidth, visibleWidth, type Component } from "@earendil-works/pi-tui";
import { ICONS } from "../design/icons.ts";
import { ACTIVITY_INTERVAL_MS, type AnimationCoordinator, type AnimationSubscription } from "../design/motion.ts";
import { bodyIndent } from "../design/spacing.ts";
import { sanitizeLabelText } from "../design/text.ts";
import { chromeInner, repairFill, surfaceLine, theme } from "../theme.ts";

/** Cooldown between learning acknowledgments (ms). */
const GROWTH_COOLDOWN_MS = 30_000;
/** Visible acknowledgment lifetime (ms). */
const GROWTH_DURATION_MS = 800;
/** Growth frame cadence (ms). */
const GROWTH_INTERVAL_MS = ACTIVITY_INTERVAL_MS;

const WORK_INTERVAL_MS = 33;
const WORK_CYCLE_MS = 480;
const WORK_REELS = ["◐", "◓", "◑", "◒"];
const WORK_REELS_WIDTH = 13;

type FooterStatus = "ready" | "working" | "offline" | "awaiting_approval" | "stopping" | "cleanup_pending";

export class Footer implements Component {
	cwd = "";
	branch = "";
	model = "";
	tokens = 0;
	/** Current context size, independent of cumulative turn accounting. */
	contextTokens = 0;
	contextPercent: number | null = 0;
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
				this.workingSub = this.activity.subscribe("footer:working", WORK_INTERVAL_MS);
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

	readonly chatStatus: Component = {
		render: (width) => this.renderChatStatus(width),
		invalidate() {},
	};

	chatStatusRows(): number {
		if (this.compact && this.status === "awaiting_approval") return 0;
		return 1;
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

	/** Short token counts keep the context gauge inside the footer corner. */
	private compactTokens(value: number): string {
		if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
		if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
		return String(value);
	}

	private renderChatStatus(width: number): string[] {
		if (width <= 0 || this.chatStatusRows() === 0) return [];
		const inset = width >= 20 ? bodyIndent(width) : 0;
		const fit = width - 2 * inset;
		const pad = " ".repeat(inset);
		const count = this.compactTokens(this.contextTokens);
		const percent = this.contextPercent === null ? "—" : `${Math.round(this.contextPercent)}%`;
		const unit = this.contextTokens === 1 ? " token" : " tokens";
		const usage = truncateToWidth(`${count}${fit >= 31 ? unit : ""} (${percent})`, fit, "");
		const room = fit - visibleWidth(usage) - 1;
		let activity = "";
		if (this.status === "working" && room >= 5) {
			const hint = room >= 15 ? "esc×2 interrupt" : "esc×2";
			activity = theme.muted(hint);
			if (room >= WORK_REELS_WIDTH + 2 + visibleWidth(hint)) {
				const elapsed = this.reducedMotion ? 0 : Date.now() - this.workingStart;
				const phase = (elapsed % WORK_CYCLE_MS) / WORK_CYCLE_MS;
				const reelCount = WORK_REELS.length;
				const step = Math.floor(phase * reelCount);
				const reverseStep = (reelCount - step + reelCount / 2) % reelCount;
				const light = 0.34 * Math.cos(phase * 2 * Math.PI);
				const left = theme.activity(WORK_REELS[step]!, 0.66 + light, 0);
				const right = theme.activity(WORK_REELS[reverseStep]!, 0.66 - light, 1);
				const reels = `${theme.dim("[")}   ${left}   ${right}   ${theme.dim("]")}`;
				activity = `${reels}  ${activity}`;
			}
		}
		const gap = " ".repeat(fit - visibleWidth(usage) - visibleWidth(activity));
		return [repairFill("background", `${pad}${activity}${gap}${theme.muted(usage)}${pad}`)];
	}

	render(width: number): string[] {
		if (width <= 0) return [""];
		const square = this.growthActive ? this.pulsingSquare(this.growthStart) : theme.muted(ICONS.idleSquare);
		const status = this.status === "working"
			? theme.accent("working")
			: this.status === "offline" ? theme.error("offline")
			: this.status === "awaiting_approval" ? theme.warning("awaiting approval")
			: this.status === "cleanup_pending" ? theme.warning("cleanup pending")
			: this.status === "stopping" ? theme.warning("stopping") : theme.muted("ready");
		const agents = this.subagents > 0
			? theme.muted(` · ${this.subagents} subagent${this.subagents === 1 ? "" : "s"}`)
			: "";
		const tail = `${status}${agents}`;
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
		return [surfaceLine(row, width)];
	}

	invalidate(): void {}
}
