// The persistent command strip. Brand, three real tabs, session context and
// live state occupy one row; the active tab gets a bright underline on the
// second row. This is the shell's navigation now: pages swap inside it instead
// of replacing the whole viewport.
import { truncateToWidth, visibleWidth, type Component, type TuiMouseEvent } from "@earendil-works/pi-tui";
import { sanitizeDisplay } from "../design/text.ts";
import { isOpaqueBackground, theme } from "../theme.ts";

export type NavView = "chat" | "activity" | "changes";

export interface NavSnapshot {
	view: NavView;
	title?: string;
	project?: string;
	branch?: string;
	model?: string;
	provider?: string;
	/** Footer status vocabulary: ready, working, offline, awaiting_approval... */
	status: string;
	running?: boolean;
	activityCount?: number;
	changeCount?: number;
	approvals?: number;
	queued?: number;
}

interface Tab {
	view: NavView;
	label: string;
	short: string;
	count: number;
}

interface Hitbox {
	x: number;
	width: number;
	view: NavView;
}

function statusText(snapshot: NavSnapshot): { text: string; tone: (text: string) => string; glyph: string } {
	switch (snapshot.status) {
		case "working": return { text: "working", tone: theme.accent, glyph: "●" };
		case "awaiting_approval": return { text: "awaiting approval", tone: theme.warning, glyph: "●" };
		case "stopping": return { text: "stopping", tone: theme.warning, glyph: "◐" };
		case "cleanup_pending": return { text: "cleanup pending", tone: theme.warning, glyph: "◐" };
		case "offline": return { text: "offline", tone: theme.error, glyph: "○" };
		default: return { text: "ready", tone: theme.dim, glyph: "○" };
	}
}

export class NavBar implements Component {
	private readonly snapshot: () => NavSnapshot;
	private readonly onSelect: (view: NavView) => void;
	private readonly terminalRows: () => number;
	private hitboxes: Hitbox[] = [];

	constructor(
		snapshot: () => NavSnapshot,
		onSelect: (view: NavView) => void,
		terminalRows: () => number,
	) {
		this.snapshot = snapshot;
		this.onSelect = onSelect;
		this.terminalRows = terminalRows;
	}

	/** Two rows on normal viewports, one on short ones. */
	rows(): number {
		return this.terminalRows() >= 12 ? 2 : 1;
	}

	render(width: number): string[] {
		const total = Math.max(1, width);
		const snap = this.snapshot();
		const brand = isOpaqueBackground()
			? theme.accentBg(theme.onAccent(" MACHINA "))
			: theme.accentBold("MACHINA");
		const compact = total < 64;
		const tabs: Tab[] = [
			{ view: "chat", label: "Chat", short: "Chat", count: 0 },
			{ view: "activity", label: "Activity", short: "Act", count: snap.activityCount ?? 0 },
			{ view: "changes", label: "Changes", short: "Chg", count: snap.changeCount ?? 0 },
		];
		const tabParts = tabs.map((tab) => {
			const active = snap.view === tab.view;
			const name = compact ? tab.short : tab.label;
			const count = tab.count > 0 ? ` ${tab.count}` : "";
			const plain = `${name}${count}`;
			const styled = active
				? `${theme.accentBold(name)}${tab.count > 0 ? theme.accent(count) : ""}`
				: `${theme.muted(name)}${tab.count > 0 ? theme.dim(count) : ""}`;
			return { tab, plain, styled };
		});
		const brandPlain = isOpaqueBackground() ? " MACHINA " : "MACHINA";
		const left = `${brand}   ${tabParts.map(part => part.styled).join("   ")}`;
		const leftPlain = `${brandPlain}   ${tabParts.map(part => part.plain).join("   ")}`;
		const leftWidth = visibleWidth(leftPlain);

		// Right side is optional context. Status always survives; title,
		// project and model join only when the remaining columns allow them.
		const state = statusText(snap);
		const candidates = [
			`${state.tone(state.glyph)} ${state.tone(state.text)}`,
			snap.model && snap.model.toLowerCase() !== (snap.title ?? "").toLowerCase()
				? theme.dim(sanitizeDisplay(snap.model))
				: "",
			snap.project ? theme.dim(sanitizeDisplay(snap.project) + (snap.branch ? ` · ${sanitizeDisplay(snap.branch)}` : "")) : "",
			snap.title ? theme.muted(sanitizeDisplay(snap.title)) : "",
		].filter(Boolean);
		let right = "";
		for (const candidate of candidates) {
			const next = right ? `${candidate}   ${right}` : candidate;
			if (leftWidth + 2 + visibleWidth(next) > total) break;
			right = next;
		}
		const gap = Math.max(0, total - leftWidth - visibleWidth(right));
		const line = truncateToWidth(`${left}${right ? `${" ".repeat(gap)}${right}` : ""}`, total, "…", true);

		this.hitboxes = [];
		let x = visibleWidth(brandPlain) + 3;
		for (const part of tabParts) {
			this.hitboxes.push({ x, width: visibleWidth(part.plain), view: part.tab.view });
			x += visibleWidth(part.plain) + 3;
		}

		if (this.rows() === 1) return [line];
		const active = tabParts.find(part => part.tab.view === snap.view);
		let rule = theme.border("─".repeat(total));
		if (active) {
			const activeStart = this.hitboxes.find(hit => hit.view === active.tab.view)?.x ?? 0;
			const activeWidth = Math.min(visibleWidth(active.plain), Math.max(0, total - activeStart));
			if (activeWidth > 0) {
				rule = `${theme.border("─".repeat(activeStart))}${theme.accent("━".repeat(activeWidth))}${theme.border("─".repeat(Math.max(0, total - activeStart - activeWidth)))}`;
			}
		}
		return [line, rule];
	}

	handleMouse(event: TuiMouseEvent): { handled: boolean; focus?: boolean; render?: boolean } | undefined {
		if (event.type !== "click" || event.button !== "left" || event.y !== 0) return undefined;
		const hit = this.hitboxes.find(candidate =>
			event.x >= candidate.x && event.x < candidate.x + candidate.width,
		);
		if (!hit) return undefined;
		this.onSelect(hit.view);
		return { handled: true, render: true };
	}

	invalidate(): void {
		this.hitboxes = [];
	}
}
