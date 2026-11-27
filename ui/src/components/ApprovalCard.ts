// Exact approval review: a dedicated bounded bordered widget — never a
// warning dump plus a generic picker. Shows the sanitized argv with argument
// boundaries, session/turn/request binding, cwd, timeout, capabilities,
// network, content identity and bounded evidence. Allow once is selected first.
// Allow options are refused while critical fields do not fit.

import { Key, matchesKey, truncateToWidth, visibleWidth, wrapTextWithAnsi, type Component, type TUI } from "@earendil-works/pi-tui";
import { sanitizeDisplay } from "../design/text.ts";
import { isOpaqueBackground, selectionBar, surfaceLine, theme } from "../theme.ts";
import { renderCard } from "../design/box.ts";
import { chatColumnWidth, panelInset } from "../design/spacing.ts";
import { DiffBlock } from "./DiffBlock.ts";

/** One physical row for identity fields that must never split the frame. */
function oneLine(text: string): string {
	return sanitizeDisplay(text).replace(/\s*\r?\n\s*/g, " ").trim();
}

/** Collapse embedded newlines without trimming indentation. */
function flatLine(text: string): string {
	return sanitizeDisplay(text).replace(/\s*\r?\n\s*/g, " ").replace(/\s+$/, "");
}

export interface ApprovalDetails {
	requestId: string;
	sessionId: string;
	originTurnId?: string;
	transportTurnId?: string;
	operation: string;
	tool?: string;
	target: string;
	argv: string[];
	cwd: string;
	backend: string;
	shell?: string;
	timeout?: number;
	capabilities: string[];
	network?: string[];
	contentDigest?: string;
	contentPreview?: string;
	contentLength?: number;
	fileDiff?: { diff?: string; truncated?: boolean };
	durable?: boolean | null;
}

export interface ApprovalDecision {
	verdict: "approved" | "denied";
	grant: "once" | "session";
}

export interface ApprovalCardOptions {
	tui?: TUI;
	docked?: boolean;
	onDecision?: (decision: ApprovalDecision) => void;
}

const DECISIONS: Array<{ label: string; decision: ApprovalDecision }> = [
	{ label: "Allow once", decision: { verdict: "approved", grant: "once" } },
	{ label: "Allow for this session", decision: { verdict: "approved", grant: "session" } },
	{ label: "Deny", decision: { verdict: "denied", grant: "once" } },
];

export class ApprovalCard implements Component {
	private readonly details: ApprovalDetails;
	private readonly docked: boolean;
	private recorded = "needs approval";
	private readonly tui: TUI | undefined;
	private readonly onDecision?: (decision: ApprovalDecision) => void;
	private selected = 0;
	private evidenceTop = 0;
	private resolving = false;
	private error = "";
	private lastWidth = 80;
	private lastRows = 24;

	constructor(details: ApprovalDetails, options: ApprovalCardOptions = {}) {
		this.details = details;
		this.docked = options.docked ?? false;
		this.tui = options.tui;
		this.onDecision = options.onDecision;
	}

	/** True while critical fields do not fit (Allow refused, Deny/Esc still safe). */
	isAllowBlocked(): boolean {
		return this.allowBlocked(this.tui?.terminal.columns ?? this.lastWidth, this.tui?.terminal.rows ?? this.lastRows);
	}

	setResolving(resolving: boolean): void {
		this.resolving = resolving;
	}

	setError(message: string): void {
		this.error = flatLine(message);
		this.resolving = false;
	}

	handleInput(data: string): void {
		if (matchesKey(data, Key.escape)) {
			if (!this.resolving) this.onDecision?.({ verdict: "denied", grant: "once" });
			return;
		}
		if (this.resolving) return;
		if (matchesKey(data, Key.enter)) {
			this.confirm();
			return;
		}
		if (matchesKey(data, Key.left) || matchesKey(data, Key.up)) {
			this.selected = (this.selected + DECISIONS.length - 1) % DECISIONS.length;
			return;
		}
		if (matchesKey(data, Key.right) || matchesKey(data, Key.down)) {
			this.selected = (this.selected + 1) % DECISIONS.length;
			return;
		}
		if (matchesKey(data, Key.pageUp)) {
			this.evidenceTop = Math.max(0, this.evidenceTop - 5);
			return;
		}
		if (matchesKey(data, Key.pageDown)) {
			this.evidenceTop += 5;
			return;
		}
		if (matchesKey(data, Key.home)) {
			this.evidenceTop = 0;
			return;
		}
		if (matchesKey(data, Key.end)) {
			this.evidenceTop = Number.MAX_SAFE_INTEGER;
			return;
		}
	}

	private confirm(): void {
		const choice = DECISIONS[this.selected];
		if (!choice) return;
		// Refusing approval selection on an unreviewable clipping is safe;
		// denial stays available and never approves unseen actions.
		if (choice.decision.verdict === "approved" && this.isAllowBlocked()) return;
		this.resolving = true;
		this.onDecision?.(choice.decision);
	}

	private allowBlocked(width: number, rows: number): boolean {
		// The decision needs the question, three choices, a hint, and enough
		// transcript to review the evidence. Below that the compact dock still
		// denies safely.
		return rows <= 12 || width < 48;
	}

	private evidenceRows(): string[] {
		const d = this.details;
		const clean = (text: string): string => sanitizeDisplay(text);
		const rows: string[] = [];
		rows.push(`Operation: ${oneLine(d.tool ?? d.operation)}`);
		rows.push(`Target: ${oneLine(d.target)}`);
		rows.push(`argv: ${d.argv.map((a) => JSON.stringify(clean(a))).join(" ") || "(none)"}`);
		rows.push(`Session: ${d.sessionId.slice(0, 8)}  turn: ${(d.originTurnId ?? d.transportTurnId ?? "").slice(0, 8)}  request: ${d.requestId.slice(0, 8)}`);
		rows.push(`cwd: ${oneLine(d.cwd || "(unknown)")}  backend: ${oneLine(d.backend || "(unknown)")}`);
		rows.push(`Timeout: ${d.timeout === undefined ? "unspecified" : d.timeout === 0 && d.capabilities.includes("background-process") ? "none · background command (can be stopped)" : `${d.timeout}s`}`);
		rows.push(`Capabilities: ${d.capabilities.length ? d.capabilities.join(", ") : "(none)"}`);
		if (d.network?.length) rows.push(`Network: ${d.network.map(oneLine).join(", ")}`);
		if (d.shell) rows.push(`Shell: ${oneLine(d.shell)}`);
		if (d.contentDigest) {
			rows.push(`Content: ${oneLine(d.contentDigest)} (${d.contentLength ?? 0} chars)`);
		}
		if (d.contentPreview) {
			rows.push("Preview:");
			for (const line of clean(d.contentPreview).split("\n").slice(0, 6)) rows.push(`  ${line}`);
			if (clean(d.contentPreview).split("\n").length > 6) rows.push("  … preview truncated");
		}
		if (d.durable === false) rows.push("! Local-only approval (database unavailable); cross-worker guarantees are degraded.");
		// No rendered evidence row may carry a newline: it would paint extra
		// terminal rows and break the dock's height math.
		return rows.map(flatLine);
	}

	render(width: number): string[] {
		if (this.docked) return this.renderDock(width);
		const total = Math.max(0, width);
		this.lastWidth = width;
		this.lastRows = this.tui?.terminal.rows ?? 24;
		const blocked = this.allowBlocked(width, this.lastRows);
		const cardWidth = Math.max(20, Math.min(96, total - 4));
		const content = Math.max(8, cardWidth - 6);
		const fields = this.evidenceRows().flatMap(line => wrapTextWithAnsi(line, content));
		const diff = this.details.fileDiff?.diff
			? new DiffBlock(sanitizeDisplay(this.details.fileDiff.diff).split("\n"), 6).render(content)
			: [];
		if (this.details.fileDiff?.truncated) diff.push(theme.dim("… diff truncated"));
		const evidence = [...fields, ...diff];
		const chrome = 2 + 1 + 1 + 3 + 1 + (blocked ? 1 : 0) + (this.error ? 1 : 0);
		const budget = Math.max(1, this.lastRows - chrome);
		const maxTop = Math.max(0, evidence.length - budget);
		this.evidenceTop = Math.max(0, Math.min(this.evidenceTop, maxTop));
		const windowed = evidence.slice(this.evidenceTop, this.evidenceTop + budget);
		const choices = DECISIONS.map((option, index) => {
			const mark = index === this.selected ? "❯" : " ";
			const raw = truncateToWidth(`${mark} ${option.label}`, content, "…", true);
			return index === this.selected ? selectionBar(raw, content) : raw;
		});
		const lines: string[] = [
			`${theme.warning("●")} ${theme.warning(this.recorded)}`,
			"",
			...windowed,
			...(evidence.length > budget
				? [theme.dim(`… ${evidence.length - budget} more evidence rows (pgup/pgdn)`)]
				: []),
			...(blocked ? [theme.warning("! Allow blocked — resize to review")] : []),
			...(this.error ? [theme.error(`✗ ${this.error}`)] : []),
			"",
			...choices,
			theme.dim("Session grants reuse authority for this operation only; changed content asks again."),
			theme.dim(this.resolving ? "Resolving…" : "←→/↑↓ choose · enter confirm · esc deny · pgup/pgdn evidence"),
		];
		const card = renderCard(lines, {
			width: cardWidth,
			title: "Approval required",
			paddingX: 2,
			paddingY: true,
			fill: "toolBg",
		});
		const left = Math.max(0, Math.floor((total - cardWidth) / 2));
		return card.map(line => `${" ".repeat(left)}${line}`);
	}

	/** Evidence stays in the retained transcript; only decisions occupy the dock. */
	readonly evidence: Component = {
		render: (width: number): string[] => this.renderEvidence(width),
		invalidate(): void {},
	};

	setRecorded(status: string): void {
		this.recorded = flatLine(status);
	}

	private renderEvidence(width: number): string[] {
		const indentWidth = panelInset(width);
		const cardWidth = Math.max(1, Math.min(chatColumnWidth(width), width - indentWidth));
		const content = Math.max(8, cardWidth - 6);
		const d = this.details;
		const title = `${theme.warning("●")} ${theme.bold(theme.text(oneLine(d.tool ?? d.operation)))} ${theme.text(oneLine(d.target))} ${theme.dim("·")} ${theme.warning(this.recorded)}`;
		const fields = this.evidenceRows();
		const all = [title, "", ...fields.flatMap(line => wrapTextWithAnsi(theme.text(line), content))];
		const diff = d.fileDiff?.diff ? new DiffBlock(sanitizeDisplay(d.fileDiff.diff).split("\n"), 8).render(content) : [];
		if (d.fileDiff?.truncated) diff.push(theme.dim("… diff truncated"));
		all.push(...diff);
		// Keep the card bounded; the latest evidence stays nearest the
		// decision dock.
		const budget = Math.max(4, Math.min(18, (this.tui?.terminal.rows ?? 40) - 18 - (this.error ? 1 : 0)));
		this.evidenceTop = Math.min(this.evidenceTop, Math.max(0, all.length - budget));
		const windowed = all.slice(this.evidenceTop, this.evidenceTop + budget);
		if (all.length > budget) {
			windowed.push(theme.dim(`Evidence ${this.evidenceTop + 1}–${Math.min(all.length, this.evidenceTop + budget)}/${all.length} · PgUp/PgDn`));
		}
		const card = renderCard(windowed, {
			width: cardWidth,
			title: "Approval evidence",
			paddingX: 1,
			paddingY: false,
			fill: "toolBg",
			titleTone: theme.secondary,
		});
		const indent = " ".repeat(indentWidth);
		return ["", ...card.map(line => truncateToWidth(`${indent}${line}`, width, "", true))];
	}

	private renderDock(width: number): string[] {
		this.lastWidth = width;
		this.lastRows = this.tui?.terminal.rows ?? 24;
		const compact = this.lastRows < 14 || width < 56;
		const tight = this.lastRows <= 12;
		const blocked = this.isAllowBlocked();
		const question = `Allow ${oneLine(this.details.tool ?? this.details.operation)} · ${oneLine(this.details.target)}?`;
		const descriptions = ["Only this exact action", "Where supported by policy", "Do not run this action"];
		const inner = Math.max(1, width - 2);
		const choices = DECISIONS.map((option, index) => {
			const mark = index === this.selected ? "›" : " ";
			const description = !compact && !tight && width >= 56 ? `  ${theme.muted(descriptions[index]!)}` : "";
			const line = truncateToWidth(` ${mark} ${option.label}${description}`, inner, "…", true);
			return index === this.selected ? selectionBar(line, inner) : theme.text(line);
		});
		const hint = this.resolving
			? "Resolving…"
			: blocked
				? "Allow blocked · resize to review · Esc deny"
				: "Enter confirm · ↑↓ choose · Esc deny · PgUp/PgDn evidence";
		// A full-width dock immediately above the composer, never a floating
		// box in the middle of the transcript. The request id stays visible so
		// a queued request is identifiable while it waits.
		const request = `request: ${truncateToWidth(oneLine(this.details.requestId), 16, "")}`;
		const label = truncateToWidth(` Approval required · ${this.recorded}`, width, "…", true);
		let heading = label;
		if (width >= 72) {
			const room = Math.max(1, inner - visibleWidth(request) - 1);
			const clipped = truncateToWidth(` Approval required · ${this.recorded}`, room, "…", true);
			heading = `${clipped}${" ".repeat(Math.max(1, room - visibleWidth(clipped) + 1))}${theme.muted(request)} `;
		}
		const body = [
			surfaceLine(theme.text(truncateToWidth(` ${question}`, width, "…", true)), width, true),
			...choices.map(line => surfaceLine(line, width, true)),
			...(this.error ? [surfaceLine(` ${theme.error(truncateToWidth(this.error, Math.max(0, width - 1), "…"))}`, width, true)] : []),
			surfaceLine(truncateToWidth(` ${theme.muted(hint)}`, width, "…"), width, true),
		];
		if (!isOpaqueBackground()) {
			// Transparent canvas: the same dock, drawn as a real box. Very narrow
			// widths clip the frame rather than overflow the pane.
			const title = truncateToWidth(` Approval required · ${this.recorded}`, Math.max(0, width - 4), "…", true);
			const fill = Math.max(0, width - 4 - visibleWidth(title));
			const top = `${theme.border("╭─")}${theme.bold(theme.warning(title))}${theme.border(`${"─".repeat(fill)}─╮`)}`;
			const bottom = theme.border(`╰${"─".repeat(Math.max(0, width - 2))}╯`);
			return [truncateToWidth(top, width, "", true), ...body, truncateToWidth(bottom, width, "", true)];
		}
		const lines = tight
			? body
			: [surfaceLine(theme.bold(theme.warning(heading)), width, true), ...body];
		return tight ? lines : [...lines, surfaceLine("", width, true)];
	}

	invalidate(): void {}
}
