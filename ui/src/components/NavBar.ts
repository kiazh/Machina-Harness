// Persistent navigation and session context. Only the active tab is marked.
import { truncateToWidth, visibleWidth, type Component, type TuiMouseEvent } from "@earendil-works/pi-tui";
import { sanitizeLabelText } from "../design/text.ts";
import { chromeInner, selectionBar, surfaceLine, theme } from "../theme.ts";

export type NavView = "chat" | "activity" | "changes";

export interface NavSnapshot {
	view: NavView;
	title?: string;
	project?: string;
	branch?: string;
	model?: string;
	/** Footer status vocabulary: ready, working, offline, awaiting_approval... */
	status: string;
	activityCount?: number;
	changeCount?: number;
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
	view: NavView | "keys";
}

function statusText(snapshot: NavSnapshot): { text: string; tone: (text: string) => string; glyph: string } {
	switch (snapshot.status) {
		case "working": return { text: "working", tone: theme.accent, glyph: "●" };
		case "awaiting_approval": return { text: "awaiting approval", tone: theme.warning, glyph: "●" };
		case "stopping": return { text: "stopping", tone: theme.warning, glyph: "◐" };
		case "cleanup_pending": return { text: "cleanup pending", tone: theme.warning, glyph: "◐" };
		case "offline": return { text: "offline", tone: theme.error, glyph: "○" };
		default: return { text: "ready", tone: theme.muted, glyph: "○" };
	}
}

export class NavBar implements Component {
	private readonly snapshot: () => NavSnapshot;
	private readonly onSelect: (view: NavView) => void;
	private readonly terminalRows: () => number;
	private readonly onKeys?: () => void;
	private hitboxes: Hitbox[] = [];

	constructor(
		snapshot: () => NavSnapshot,
		onSelect: (view: NavView) => void,
		terminalRows: () => number,
		onKeys?: () => void,
	) {
		this.snapshot = snapshot;
		this.onSelect = onSelect;
		this.terminalRows = terminalRows;
		this.onKeys = onKeys;
	}

	/** Two rows on normal viewports, one on short ones. */
	rows(): number {
		return this.terminalRows() >= 16 ? 2 : 1;
	}

	render(width: number): string[] {
		const total = Math.max(1, width);
		// On a transparent canvas the row spends two cells on its outline, so
		// fit the content to that smaller width instead of clipping the status.
		const fit = chromeInner(total);
		const snap = this.snapshot();
		// The state chip outranks the wordmark on very narrow strips: losing
		// "awaiting approval" is worse than losing the brand.
		const showBrand = total >= 56;
		const brand = showBrand ? theme.bold(theme.text(" MACHINA ")) : "";
		const compact = total < 64;
		const tabs: Tab[] = [
			{ view: "activity", label: "Activity", short: "Act", count: snap.activityCount ?? 0 },
			{ view: "chat", label: "Chat", short: "Chat", count: 0 },
			{ view: "changes", label: "Changes", short: "Chg", count: snap.changeCount ?? 0 },
		];
		const tabParts = tabs.map((tab) => {
			const active = snap.view === tab.view;
			const name = compact ? tab.short : tab.label;
			const count = tab.count > 0 ? ` ${tab.count}` : "";
			const plain = active ? `[${name}${count}]` : ` ${name}${count} `;
			const styled = active
				? selectionBar(plain, visibleWidth(plain))
				: theme.muted(plain);
			return { tab, plain, styled };
		});
		const brandPlain = showBrand ? " MACHINA " : "";
		const head = showBrand ? `${brand} ${theme.border("│")} ` : "";
		const headPlain = showBrand ? `${brandPlain} │ ` : "";
		const keys = this.onKeys && total >= 76 ? "  Keys ? " : "";
		const left = `${head}${tabParts.map(part => part.styled).join(" ")}${theme.secondary(keys)}`;
		const leftPlain = `${headPlain}${tabParts.map(part => part.plain).join(" ")}${keys}`;
		const leftWidth = visibleWidth(leftPlain);

		// Right side is optional context. Status always survives; title,
		// project and model join only when the remaining columns allow them.
		const state = statusText(snap);
		const candidates = [
			`${state.tone(state.glyph)} ${state.tone(state.text)}`,
			snap.model && snap.model.toLowerCase() !== (snap.title ?? "").toLowerCase()
				? theme.dim(sanitizeLabelText(snap.model))
				: "",
			snap.project ? theme.dim(sanitizeLabelText(snap.project) + (snap.branch ? ` · ${sanitizeLabelText(snap.branch)}` : "")) : "",
			snap.title ? theme.text(sanitizeLabelText(snap.title)) : "",
		].filter(Boolean);
		// The state chip is the one part of the right side that always
		// survives: truncate it to the remaining room, then add context only
		// when it fits beside it.
		const status = candidates[0]!;
		const statusRoom = Math.max(0, fit - leftWidth - 2);
		let right = statusRoom >= 3 ? truncateToWidth(status, statusRoom, "…") : "";
		if (right) {
			for (const candidate of candidates.slice(1)) {
				const next = `${candidate}   ${right}`;
				if (leftWidth + 2 + visibleWidth(next) <= fit) right = next;
			}
		}
		const gap = Math.max(0, fit - leftWidth - visibleWidth(right));
		const line = truncateToWidth(`${left}${right ? `${" ".repeat(gap)}${right}` : ""}`, fit, "…", true);

		this.hitboxes = [];
		const offset = (total - fit) / 2;
		let x = visibleWidth(headPlain) + offset;
		for (const part of tabParts) {
			this.hitboxes.push({ x, width: visibleWidth(part.plain), view: part.tab.view });
			x += visibleWidth(part.plain) + 1;
		}
		if (keys) this.hitboxes.push({ x: leftWidth - keys.length + offset, width: keys.length, view: "keys" });

		const header = surfaceLine(line, total);
		if (this.rows() === 1) return [header];
		return [header, theme.border("─".repeat(total))];
	}

	handleMouse(event: TuiMouseEvent): { handled: boolean; focus?: boolean; render?: boolean } | undefined {
		if (event.type !== "click" || event.button !== "left" || event.y !== 0) return undefined;
		const hit = this.hitboxes.find(candidate =>
			event.x >= candidate.x && event.x < candidate.x + candidate.width,
		);
		if (!hit) return undefined;
		if (hit.view === "keys") this.onKeys?.();
		else this.onSelect(hit.view);
		return { handled: true, render: true };
	}

	invalidate(): void {
		this.hitboxes = [];
	}
}
