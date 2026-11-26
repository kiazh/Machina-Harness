// Exact approval review: a dedicated bounded bordered widget — never a
// warning dump plus a generic picker. Shows the sanitized argv with argument
// boundaries, session/turn/request binding, cwd, timeout, capabilities,
// network, content identity and bounded evidence. Deny is selected first.
// Allow options are refused while critical fields do not fit.

import { Key, matchesKey, truncateToWidth, visibleWidth, wrapTextWithAnsi, type Component, type TUI } from "@earendil-works/pi-tui";
import { sanitizeDisplay } from "../design/text.ts";
import { selectionBar, theme } from "../theme.ts";
import { renderCard } from "../design/box.ts";
import { panelInset } from "../design/spacing.ts";
import { DiffBlock } from "./DiffBlock.ts";

/** One physical row for identity fields that must never split the frame. */
function oneLine(text: string): string {
	return sanitizeDisplay(text).replace(/\s*\r?\n\s*/g, " ").trim();
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
	status?: string;
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
	private selected = 2;
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
		this.error = sanitizeDisplay(message);
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
		// The full decision card needs room for the question, three choices
		// and the hint. Below that the compact dock still denies safely.
		return rows < 12 || width < 30;
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
		return rows.map((line) => clean(line));
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
		this.recorded = sanitizeDisplay(status);
	}

	private renderEvidence(width: number): string[] {
		const indentWidth = panelInset(width);
		const cardWidth = Math.max(24, Math.min(104, width - indentWidth));
		const content = Math.max(8, cardWidth - 6);
		const d = this.details;
		const title = `${theme.warning("●")} ${theme.text(oneLine(d.tool ?? d.operation))} ${theme.muted(oneLine(d.target))} ${theme.dim("·")} ${theme.warning(this.recorded)}`;
		const fields = this.evidenceRows();
		const all = [title, "", ...fields.flatMap(line => wrapTextWithAnsi(line, content))];
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
			titleTone: theme.muted,
		});
		const indent = " ".repeat(indentWidth);
		return ["", ...card.map(line => `${indent}${line}`)];
	}

	private renderDock(width: number): string[] {
		this.lastWidth = width;
		this.lastRows = this.tui?.terminal.rows ?? 24;
		const compact = this.lastRows < 14 || width < 56;
		const tight = this.lastRows <= 10;
		const blocked = this.isAllowBlocked();
		const question = `Allow ${oneLine(this.details.tool ?? this.details.operation)} · ${oneLine(this.details.target)}?`;
		const request = `request: ${oneLine(this.details.requestId).slice(0, 16)}`;
		const descriptions = ["Only this exact action", "Where supported by policy", "Do not run this action"];
		const widthForChoices = tight ? width : Math.max(20, Math.min(112, width) - (compact ? 4 : 8));
		const choices = DECISIONS.map((option, index) => {
			const mark = index === this.selected ? "›" : " ";
			const description = !compact && widthForChoices >= 56 ? `  ${theme.dim(descriptions[index]!)}` : "";
			const line = truncateToWidth(`${mark} ${option.label}${description}`, widthForChoices, "…", true);
			return index === this.selected ? selectionBar(line, widthForChoices) : line;
		});
		const hint = this.resolving
			? "Resolving…"
			: blocked
				? "Allow blocked · resize to review · Esc deny"
				: "↑↓ choose · Enter select · Esc deny";
		if (tight) {
			const lines = [
				theme.border("─".repeat(Math.max(0, width))),
				truncateToWidth(question, width, "…", true),
				...choices,
				theme.dim(truncateToWidth(hint, width)),
			];
			return lines.map(line => truncateToWidth(line, width));
		}
		const cardWidth = Math.max(24, Math.min(width, 112));
		const padX = compact ? 1 : 2;
		const inner = Math.max(8, cardWidth - 2 - padX * 2);
		const heading = truncateToWidth(question, inner, "…", true);
		const room = Math.max(0, inner - visibleWidth(request) - 1);
		const first = truncateToWidth(question, room, "…", true);
		const headingLine = width >= 70
			? `${first}${" ".repeat(Math.max(0, room - visibleWidth(first)))} ${theme.dim(request)}`
			: heading;
		const lines: string[] = [
			headingLine,
			...choices,
			"",
			theme.dim(truncateToWidth(hint, inner)),
		];
		if (this.error) lines.splice(2, 0, theme.error(truncateToWidth(this.error, inner, "…")));
		const card = renderCard(lines, {
			width: cardWidth,
			title: `Approval required · ${this.recorded}`,
			paddingX: padX,
			paddingY: false,
			fill: "toolBg",
		});
		const left = Math.max(0, Math.floor((width - cardWidth) / 2));
		return card.map(line => `${" ".repeat(left)}${line}`);
	}

	invalidate(): void {}
}
