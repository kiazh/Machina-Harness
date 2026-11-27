// Compact honest tool turn: marker + name + muted args header, bounded
// result/diff preview, explicit outcome text. Neutral frame; critical outcome
// stays visible at narrow widths. Arguments are display-only.

import { truncateToWidth, visibleWidth, type Component, type TuiMouseEvent } from "@earendil-works/pi-tui";
import { ICONS, TOOL_MARKERS } from "../design/icons.ts";
import { uiLog } from "../debug.ts";
import { ACTIVITY_INTERVAL_MS, type ActivitySource } from "../design/motion.ts";
import { panelInset } from "../design/spacing.ts";
import { sanitizeDisplay, shapePreview, truncateUtf8ByBytes } from "../design/text.ts";
import { toolCallDisplayName } from "../mcpNames.ts";
import { theme } from "../theme.ts";
import { styleDiffLine } from "./DiffBlock.ts";

export type ToolOutcome =
	| "queued"
	| "running"
	| "awaiting_approval"
	| "success"
	| "denied"
	| "error"
	| "interrupted"
	| "outcome_unknown"
	| "historical";

const HARNESS_LABELS: Record<string, string> = {
	opencode: "OpenCode",
	codex: "Codex",
	hermes: "Hermes",
};

/** Human label for tools whose wire name is not a useful status line. */
function toolLabel(name: string, args: Record<string, unknown>): string {
	if (name === "harness_delegate") {
		const platform = typeof args.platform === "string" ? args.platform : "";
		return `Consulting ${HARNESS_LABELS[platform] ?? (platform || "external harness")}`;
	}
	if (name === "delegate") {
		const agent = typeof args.agent === "string" ? args.agent : "";
		return agent ? `Delegating to ${agent}` : "Delegating";
	}
	const mcpLabel = toolCallDisplayName(name, args);
	if (mcpLabel) return mcpLabel;
	return name;
}

export interface ToolResultView {
	/** Sanitized, bounded retained content. */
	text: string;
	backendTruncated: boolean;
	locallyTruncated: boolean;
}

function markerFor(outcome: ToolOutcome): string {
	switch (outcome) {
		case "queued":
			return TOOL_MARKERS.queued;
		case "running":
			return TOOL_MARKERS.running;
		case "awaiting_approval":
			return "○";
		case "success":
			return TOOL_MARKERS.success;
		case "denied":
			// A policy denial is not a success and not an execution error:
			// its own neutral marker (⊘, muted) keeps the three distinct.
			return "⊘";
		case "error":
			return TOOL_MARKERS.error;
		case "interrupted":
			return TOOL_MARKERS.interrupted;
		case "outcome_unknown":
			return TOOL_MARKERS.unknown;
		case "historical":
			return "○";
	}
}

function between(left: string, right: string, width: number): string {
	const room = Math.max(0, width - visibleWidth(right) - 1);
	const first = truncateToWidth(left, room, "…", true);
	return `${first}${" ".repeat(Math.max(0, room - visibleWidth(first)))} ${truncateToWidth(right, width)}`;
}

function duration(ms: number): string {
	if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
	return `${(ms / 1000).toFixed(1)}s`;
}

function looksLikeDiff(text: string): boolean {
	return /^@@ /m.test(text) && /^(?:diff --git |--- |\+\+\+ )/m.test(text);
}

const CONTENT_KEYS = ["content", "diff", "patch"] as const;

export class ToolTurn implements Component {
	private readonly name: string;
	private readonly args: string;
	private readonly externalHarness: boolean;
	private readonly activity: ActivitySource;
	private readonly startedAt = Date.now();
	private completedAt: number | undefined;
	private result: ToolResultView = { text: "", backendTruncated: false, locallyTruncated: false };
	private outcome: ToolOutcome = "running";
	private paused: "stopping" | "cleanup_pending" | undefined;
	private expanded = false;
	private readonly content?: string;
	private readonly contentTruncated: boolean;
	private readonly cached = new Map<number, string[]>();

	constructor(name: string, args: Record<string, unknown>, activity: ActivitySource) {
		// Gateway frames are typed at compile time but untrusted at runtime; a
		// malformed tool frame must not take the renderer down.
		const safeName = typeof name === "string" && name.trim() ? name : "unknown tool";
		const safeArgs: Record<string, unknown> = args && typeof args === "object" && !Array.isArray(args)
			? args
			: {};
		if (safeName !== name || safeArgs !== args) {
			uiLog.scope("tool").warn("tool.start.normalized", { name: typeof name, args: typeof args });
		}
		this.name = sanitizeDisplay(toolLabel(safeName, safeArgs));
		this.externalHarness = safeName === "harness_delegate";
		const contentKeys: readonly string[] = safeName === "write_file" || safeName.includes("edit") || safeName.includes("patch")
			? CONTENT_KEYS
			: [];
		const rawContent = contentKeys
			.map((key) => safeArgs[key])
			.find((value) => typeof value === "string" && value.length > 0);
		if (typeof rawContent === "string") {
			const clipped = truncateUtf8ByBytes(rawContent, 8_000);
			this.content = clipped.text;
			this.contentTruncated = clipped.truncated;
		} else {
			this.contentTruncated = false;
		}
		// The `mcp_call` label already names the connector and tool; showing the
		// raw namespaced `name` in the argument line would duplicate the ugly
		// wire form (`vercel__list_projects`). Keep only the call arguments.
		const baseArgs = safeName === "mcp_call"
			? (safeArgs.arguments && typeof safeArgs.arguments === "object" && !Array.isArray(safeArgs.arguments) ? { arguments: safeArgs.arguments } : {})
			: safeArgs;
		const displayArgs = contentKeys.length
			? Object.fromEntries(Object.entries(baseArgs).filter(([key]) => !contentKeys.includes(key)))
			: baseArgs;
		const argsText = sanitizeDisplay(Object.entries(displayArgs).map(([key, value]) =>
			["path", "command"].includes(key) && typeof value === "string" ? value : formatToolArgs({ [key]: value }),
		).join(", "));
		const clipped = truncateUtf8ByBytes(argsText, 4_000);
		this.args = clipped.truncated ? `${clipped.text} …` : clipped.text;
		this.activity = activity;
	}

	settle(result: ToolResultView, outcome: ToolOutcome): void {
		// Tool frames arrive from a subprocess-facing gateway; a missing result
		// field is unknown output, not a reason to crash the transcript.
		if (typeof result?.text !== "string") {
			uiLog.scope("tool").warn("tool.result.invalid", { name: this.name, type: typeof result?.text });
		}
		this.result = {
			text: typeof result?.text === "string" ? result.text : "",
			backendTruncated: result?.backendTruncated === true,
			locallyTruncated: result?.locallyTruncated === true,
		};
		this.outcome = typeof outcome === "string" ? outcome : "outcome_unknown";
		if (outcome !== "running" && outcome !== "queued" && this.completedAt === undefined) {
			this.completedAt = Date.now();
		}
		this.cached.clear();
	}

	setMotionPaused(phase: "stopping" | "cleanup_pending"): void {
		this.paused = phase;
		this.cached.clear();
	}

	/** Re-resolve theme colors (render is lazy; nothing was baked). */
	refreshSkin(): void {
		this.invalidate();
	}

	render(width: number): string[] {
		const cached = this.cached.get(width);
		if (this.outcome !== "running" && cached) return cached;
		const total = Math.max(0, width);
		const indent = " ".repeat(panelInset(width));
		const room = Math.max(1, Math.min(Math.max(1, 108 - indent.length), total - indent.length));
		const marker = this.outcome === "running" && !this.paused
			? theme.accent(this.activity.frame([...ICONS.toolActiveFrames], ACTIVITY_INTERVAL_MS, this.startedAt))
			: styleMarker(this.outcome, markerFor(this.outcome));
		const elapsed = this.completedAt === undefined ? "" : duration(this.completedAt - this.startedAt);
		const status = this.statusText();
		const chevron = theme.dim(this.expanded ? "▾" : "▸");
		const header = `${marker} ${theme.text(this.name)}${this.args ? ` ${theme.dim(this.args)}` : ""} ${chevron}`
			.replace(/\r?\n/g, " ");
		const lines = [between(header, status, room)];
		const showContent = Boolean(this.content)
			&& !["error", "denied", "interrupted", "outcome_unknown"].includes(this.outcome);
		const source = showContent ? this.content! : this.result.text;
		const previewBudget = this.expanded ? 200 : this.outcome === "running" ? 1 : 2;
		if (source.trim()) {
			const shaped = shapePreview(source, Math.max(1, room - 2), previewBudget);
			const diff = !showContent && looksLikeDiff(source);
			shaped.shown.forEach((line, index) => {
				const prefix = index === 0 ? theme.dim("⎿ ") : "  ";
				const styled = diff ? styleDiffLine(line) : theme.text(line);
				lines.push(`${prefix}${truncateToWidth(styled, Math.max(1, room - 2))}`);
			});
			if (shaped.omitted > 0) {
				lines.push(theme.dim(`  … ${shaped.omitted} more line${shaped.omitted === 1 ? "" : "s"}`));
			}
			if (showContent && this.contentTruncated) {
				lines.push(theme.dim("  content preview truncated"));
			}
		} else if (this.expanded) {
			lines.push(theme.dim(this.outcome === "running" ? "  waiting for output" : "  no output recorded"));
		}
		if (this.result.backendTruncated && this.outcome !== "running") {
			lines.push(theme.dim("  server output truncated"));
		}
		if (elapsed && this.outcome !== "queued" && this.outcome !== "running" && this.outcome !== "historical") {
			lines[0] = between(header, `${status} ${theme.dim(elapsed)}`, room);
		}
		const rows = ["", ...lines.map((line) => truncateToWidth(`${indent}${line}`, total))];
		if (this.outcome !== "running") {
			if (this.cached.size >= 4) this.cached.clear();
			this.cached.set(width, rows);
		}
		return rows;
	}

	toggle(): void {
		this.expanded = !this.expanded;
		this.cached.clear();
	}

	handleMouse(event: TuiMouseEvent): { handled: boolean; render: boolean } | undefined {
		if (event.type !== "click" || event.button !== "left") return undefined;
		const target = this.expanded ? 1 : 2;
		if (event.y < 0 || event.y > target) return undefined;
		this.toggle();
		return { handled: true, render: true };
	}

	private statusText(): string {
		if (this.outcome === "running") {
			if (this.paused) {
				return theme.dim(`${this.paused === "stopping" ? "stopping" : "cleanup pending"}; outcome pending`);
			}
			return theme.dim(this.externalHarness ? "waiting for external harness" : "running");
		}
		if (this.outcome === "queued") return theme.dim("queued");
		if (this.outcome === "awaiting_approval") return theme.warning("awaiting approval");
		if (this.outcome === "denied") return theme.muted("denied; not executed");
		if (this.outcome === "interrupted") return theme.warning("interrupted; outcome unknown");
		if (this.outcome === "outcome_unknown") return theme.muted("completion not reported");
		if (this.outcome === "historical") return theme.dim("recorded output");
		if (this.outcome === "error") return theme.error("failed");
		return theme.dim("done");
	}

	invalidate(): void { this.cached.clear(); }
}

function styleMarker(outcome: ToolOutcome, mark: string): string {
	switch (outcome) {
		case "success":
			return theme.success(mark);
		case "denied":
			return theme.muted(mark);
		case "error":
			return theme.error(mark);
		case "interrupted":
			return theme.warning(mark);
		case "outcome_unknown":
			return theme.muted(mark);
		case "historical":
			return theme.dim(mark);
		default:
			return theme.dim(mark);
	}
}

/** Compact single-line argument summary (display-only, never reconstructed). */
export function formatToolArgs(args: Record<string, unknown>): string {
	return Object.entries(args)
		.map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
		.join(", ");
}
