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

	readonly chatStatus: Component = {
		render: (width) => this.renderChatStatus(width),
		invalidate() {},
	};

	chatStatusRows(): number {
		if (this.compact && this.status === "awaiting_approval") return 0;
		return this.status === "working" && !this.compact ? 2 : 1;
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
		const row = (left: string, right = "") => {
			const rightWidth = visibleWidth(right);
			const clipped = truncateToWidth(left, Math.max(0, fit - rightWidth - (right ? 1 : 0)), "");
			const gap = " ".repeat(fit - rightWidth - visibleWidth(clipped));
			return repairFill("background", `${pad}${clipped}${gap}${right}${pad}`);
		};
		const count = this.compactTokens(this.contextTokens);
		const percent = this.contextPercent === null ? "—" : `${Math.round(this.contextPercent)}%`;
		const unit = this.contextTokens === 1 ? " token" : " tokens";
		const usage = theme.muted(`${count}${fit >= 31 ? unit : ""} (${percent})`);
		if (this.status !== "working") return [row(usage)];

		const frame = this.activity.frame(["3", "2", "1", "0", "1", "2", "3", "4", "5", "6", "5", "4"],
			ACTIVITY_INTERVAL_MS, this.workingStart);
		const pulse = Array.from({ length: 7 }, (_, i) => {
			const distance = Math.abs(i - Number(frame));
			if (distance === 0) return theme.accent("◆");
			return theme.dim(distance === 1 ? "◇" : "·");
		}).join(" ");
		const hint = theme.muted("esc×2 interrupt");
		const shortHint = fit >= 5 ? theme.muted("esc×2") : "";
		if (!this.compact) {
			return [row(usage, fit >= visibleWidth(usage) + 14 ? pulse : ""), row("", fit >= 15 ? hint : shortHint)];
		}
		const withPulse = `${pulse}  ${hint}`;
		const room = fit - visibleWidth(usage) - 1;
		const right = room >= visibleWidth(withPulse) ? withPulse : room >= 15 ? hint : shortHint;
		return [row(usage, right)];
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
