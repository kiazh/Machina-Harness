// Responsive docked root: one retained transcript ScrollView for the shell's
// lifetime, composer + footer pinned to the bottom. Density budgets derive
// from actual terminal dimensions at sync time; the same ScrollView instance
// survives root rebuilds so the user's scroll anchor is never lost.
//
// The workspace composition (plan §7 + UX-5) is opt-in: once a layout state
// arrives, the top row becomes an HStack of the panes (chat transcript on its
// geometry side, then WorkspaceShell's divider/editor/explorer/grip columns),
// and the composer + approval dock become FULL-WIDTH rows beneath it. Every
// width comes from computeLayout(); this file never re-derives a split, and
// retained components keep their identity.

import { HStack, isViewportTUI, ScrollView, VStack, type Component, type TUI } from "@earendil-works/pi-tui";
import { LIMITS } from "../design/tokens.ts";
import { theme } from "../theme.ts";
import type { LayoutInput } from "../workspace/state.ts";
import type { Footer } from "./Footer.ts";
import { WorkspaceShell } from "./WorkspaceShell.ts";

const LAYOUT_NODE = Symbol.for("@earendil-works/pi-tui/layout-node");

export type Density = "full" | "reduced" | "compact" | "degraded";

export interface ComposerBudget {
	setMaxRows(rows: number): void;
	setDegradedMode(degraded: boolean): void;
	/** Cheap chrome measurement; falls back to render() when absent. */
	measureRows?(width: number): number;
}

/** Pinned bar contract: a component that can hide itself and size by budget. */
export interface PinnedBarSlot extends Component {
	visible: boolean;
	setBudget(rows: number): void;
}

export class AppShell {
	/** The single primary transcript ScrollView, retained for life. */
	readonly transcriptScroll: ScrollView;
	/**
	 * Divider-drag ratio hook: the app persists the ratio and feeds it back
	 * through setWorkspaceState. Keyboard resizing will reuse the same path.
	 */
	onWorkspaceRatioChange?: (ratio: number) => void;
	/** Far-right explorer grip clicked: the app flips its explorer request. */
	onWorkspaceExplorerToggle?: () => void;
	/** Notify the app before layout measurement when terminal dimensions change. */
	onViewportResize?: () => void;
	private currentRoot: VStack;
	private readonly viewportRoot: Component;
	private readonly tui: TUI;
	private readonly composer: Component & ComposerBudget;
	private readonly footer: Footer;
	private lastCols = -1;
	private lastRows = -1;
	private density: Density = "full";
	private dock: Component | undefined;
	/** Pinned top bar above the transcript (music); hidden rows collapse. */
	private pinnedBar: PinnedBarSlot | undefined;
	private readonly pinnedRule: Component = {
		render: (width: number): string[] => [theme.border("─".repeat(Math.max(0, width)))],
		invalidate: (): void => {},
	};
	/** Space between the composer and the footer so the input reads as raised. */
	private readonly bottomSpacer: Component = {
		render: (): string[] => Array.from({ length: this.bottomGapRows() }, () => ""),
		invalidate: (): void => {},
	};
	/** A clean row between the final answer and the input dock. */
	private readonly composerGap: Component = {
		render: (): string[] => this.composerGapRows() > 0 ? [""] : [],
		invalidate: (): void => {},
	};
	private agentBar: (Component & { visible: boolean }) | undefined;
	private activityBar: (Component & { visible: boolean }) | undefined;
	/** Cached chrome row budget; invalidated by composer/dock changes. */
	private chromeCache: { key: string; rows: number } | undefined;
	/** Created on first workspace use; its absence means today's chat-only tree. */
	private workspace: WorkspaceShell | undefined;
	/** Optional full-page tab (currently the live status tab). */
	private page: Component | undefined;
	/** Persistent brand/tabs/state strip above every screen. */
	private navBar: (Component & { rows?: () => number }) | undefined;

	constructor(tui: TUI, transcript: Component, composer: Component & ComposerBudget, footer: Footer) {
		this.tui = tui;
		this.composer = composer;
		this.footer = footer;
		// Follow new output only when already following the end; scrolling
		// upward freezes follow until an explicit return-to-end.
		this.transcriptScroll = new ScrollView(transcript, { follow: "end" });
		this.currentRoot = this.build();
		// Resolve responsive budgets before the viewport engine measures children.
		const root = {
			[LAYOUT_NODE]: () => {
				this.sync();
				const node = this.currentRoot as unknown as { [LAYOUT_NODE](): unknown };
				return node[LAYOUT_NODE]();
			},
			render: (width: number) => { this.sync(); return this.currentRoot.render(width); },
			invalidate: () => this.currentRoot.invalidate(),
		};
		this.viewportRoot = root;
		this.sync();
	}

	setDock(component?: Component): void {
		this.dock = component;
		this.invalidateChrome();
		this.applyBudgets();
		this.rebuild();
	}

	/**
	 * Pinned bar above the transcript (chat column both in chat-only and
	 * split layouts). The transcript ScrollView instance is never replaced;
	 * visibility toggles collapse the row without touching scroll state.
	 */
	setPinnedBar(bar?: PinnedBarSlot): void {
		this.pinnedBar = bar;
		this.applyBudgets();
		this.rebuild();
	}

	setAgentBar(bar: Component & { visible: boolean }): void {
		this.agentBar = bar;
		this.rebuild();
	}

	setActivityBar(bar: Component & { visible: boolean }): void {
		this.activityBar = bar;
		this.invalidateChrome();
		this.rebuild();
	}

	/** Swap the whole viewport for a page (status tab). Undefined restores chat. */
	setPage(page?: Component): void {
		this.page = page;
		this.invalidateChrome();
		this.rebuild();
	}

	/** Persistent command strip. Its rows are charged against every screen. */
	setNavBar(nav?: Component & { rows?: () => number }): void {
		this.navBar = nav;
		this.invalidateChrome();
		this.syncPageRows();
		this.rebuild();
	}

	get root(): Component {
		return this.viewportRoot;
	}

	get currentDensity(): Density {
		return this.density;
	}

	/**
	 * Opt-in workspace composition. `state` carries intent (requested mode,
	 * persisted ratio, explorer request, preferred pane); the terminal
	 * dimensions are taken from the live terminal so callers never race a
	 * resize. The root is rebuilt only when the composed geometry changes.
	 */
	setWorkspaceState(state: LayoutInput): void {
		this.workspaceShell().setState({ ...state, columns: this.liveColumns(), rows: this.liveRows() });
	}

	/** Injected editor pane; retained across every later rebuild. */
	setWorkspaceEditor(editor?: Component): void {
		this.workspaceShell().setEditor(editor);
	}

	/** Injected explorer drawer; composed only when geometry says docked. */
	setWorkspaceExplorer(explorer?: Component): void {
		this.workspaceShell().setExplorer(explorer);
	}

	/** Recompute budgets from actual terminal dimensions (cheap; idempotent). */
	sync(): void {
		const cols = this.liveColumns();
		const rows = this.liveRows();
		if (cols === this.lastCols && rows === this.lastRows) {
			this.applyBudgets();
			return;
		}
		this.lastCols = cols;
		this.lastRows = rows;
		const density = densityFor(cols, rows);
		const changed = density !== this.density;
		this.density = density;
		this.applyBudgets();
		this.syncWorkspace(cols, rows);
		this.syncPageRows();
		if (changed || !this.currentRoot) this.rebuild();
		this.onViewportResize?.();
	}

	/** Release shell state (the coordinator stays owned by the App). */
	dispose(): void {
		this.lastCols = -1;
		this.lastRows = -1;
	}

	/**
	 * Rows (including inter-section gaps) the workspace root reserves below the
	 * panes row for the optional dock, the pinned composer and the footer. The
	 * app subtracts this from the pane's allocated rows so render() height and
	 * the live viewport stay exactly aligned.
	 *
	 * Measured once per state/layout key: the footer is a fixed single row,
	 * the composer caches its own wrapped height, and dock/activity heights
	 * only change when their component identity/visibility does. Callers that
	 * mutate chrome content call {@link invalidateChrome}.
	 */
	chromeRows(): number {
		if (this.page) return 0;
		const width = this.liveColumns();
		const activity = this.activityBar;
		const split = this.hasSplitComposer();
		const activityRows = !split && activity?.visible
			? (activity as Component & { measureRows?(columns: number): number }).measureRows?.(width)
				?? activity.render(width).length
			: 0;
		// measureRows is itself keyed by the composer's draft/window state, so
		// this stays a cheap lookup while catching draft/popup height changes.
		const composerRows = split ? 0 : this.composer.measureRows?.(width) ?? this.composer.render(width).length;
		const footerRows = this.footer.measureRows?.(width) ?? 1;
		const key = [
			width,
			this.lastRows,
			this.density,
			activityRows,
			composerRows,
			footerRows,
			this.dock ? (this.dockInChatColumn() ? "dock-chat" : "dock") : "nodock",
			this.navRows(),
		].join("|");
		const cached = this.chromeCache;
		if (cached && cached.key === key) return cached.rows;
		let rows = this.navRows() + composerRows + activityRows + footerRows + this.bottomGapRows();
		if (!split) rows += this.composerGapRows();
		if (this.dock && !this.dockInChatColumn()) {
			rows += this.dock.render(width).length;
		}
		this.chromeCache = { key, rows };
		return rows;
	}

	/** Chrome content changed (composer text, dock state): re-measure once. */
	invalidateChrome(): void {
		this.chromeCache = undefined;
	}

	/** Rows the persistent command strip occupies on this viewport. */
	private navRows(): number {
		if (!this.navBar) return 0;
		return Math.max(0, this.navBar.rows?.() ?? 0);
	}

	/** Breathing room under the composer; taller terminals get more of it. */
	private bottomGapRows(): number {
		if (this.page) return 0;
		return this.lastRows >= 40 ? 2 : this.lastRows >= 16 ? 1 : 0;
	}

	private composerGapRows(): number {
		if (this.page) return 0;
		return this.lastRows >= 6 ? 1 : 0;
	}

	/**
	 * Pages size themselves from the terminal. With the command strip above
	 * them they must know the reduced viewport, or their footer row is clipped.
	 */
	private syncPageRows(): void {
		const page = this.page as (Component & { setViewportRows?: (rows: number) => void }) | undefined;
		if (!page?.setViewportRows) return;
		page.setViewportRows(Math.max(1, this.liveRows() - this.navRows()));
	}

	private liveColumns(): number {
		return Math.max(0, this.tui.terminal.columns);
	}

	private liveRows(): number {
		return Math.max(0, this.tui.terminal.rows);
	}

	private workspaceShell(): WorkspaceShell {
		this.workspace ??= new WorkspaceShell(this.tui, {
			onRatioChange: (ratio) => this.onWorkspaceRatioChange?.(ratio),
			onResizeDemand: () => this.rebuild(),
			onExplorerToggle: () => this.onWorkspaceExplorerToggle?.(),
		});
		return this.workspace;
	}

	/**
	 * Re-run workspace geometry against the live terminal; a changed
	 * LayoutResult (field compare in WorkspaceShell) demands a rebuild.
	 */
	private syncWorkspace(cols: number, rows: number): void {
		const workspace = this.workspace;
		const state = workspace?.state;
		if (!workspace || !state) return;
		workspace.setState({ ...state, columns: cols, rows });
	}

	private applyBudgets(): void {
		if (this.dock) {
			this.composer.setMaxRows(this.lastRows < 16 ? 1 : 3);
			this.composer.setDegradedMode(this.density === "degraded");
			this.footer.compact = this.lastRows < 16;
			this.pinnedBar?.setBudget(this.pinnedBarRows());
			return;
		}
		switch (this.density) {
			case "full":
				this.composer.setMaxRows(LIMITS.composerMaxRows);
				this.composer.setDegradedMode(false);
				this.footer.compact = false;
				break;
			case "reduced":
				this.composer.setMaxRows(4);
				this.composer.setDegradedMode(false);
				this.footer.compact = false;
				break;
			case "compact":
				this.composer.setMaxRows(2);
				this.composer.setDegradedMode(false);
				this.footer.compact = true;
				break;
			case "degraded":
				this.composer.setMaxRows(1);
				this.composer.setDegradedMode(true);
				this.footer.compact = true;
				break;
		}
		this.pinnedBar?.setBudget(this.pinnedBarRows());
	}

	/**
	 * Pinned-bar rows by viewport: the full mock layout (title/artist/
	 * transport) needs room; short viewports keep one compact line; degraded
	 * viewports hide the bar entirely (playback keeps its state).
	 */
	private pinnedBarRows(): number {
		switch (this.density) {
			case "full":
			case "reduced":
				// One quiet strip: title, artist and clock. The old three-row
				// player pushed the conversation down for ambient music.
				return 1;
			default:
				return 0;
		}
	}

	private dockInChatColumn(): boolean {
		if (!this.dock) return false;
		return !this.workspace?.editor || this.workspace.layout?.effectiveMode !== "editor";
	}

	private hasSplitComposer(): boolean {
		return Boolean(this.workspace?.editor && this.workspace.layout?.effectiveMode === "split");
	}

	private build(): VStack {
		const navEntries = this.navBar ? [{ component: this.navBar, shrink: 0 }] : [];
		// Page tabs own the whole viewport: no composer, dock, activity bar or
		// footer chrome underneath them.
		if (this.page) {
			this.syncPageRows();
			return new VStack([
				...navEntries,
				{ component: this.page, basis: 0, grow: 1 },
			], { gap: 0 });
		}
		const topRow = this.workspaceRow() ?? this.chatColumn();
		const activity = this.activityBar;
		const split = this.hasSplitComposer();
		return new VStack([
			...navEntries,
			{ component: topRow, basis: 0, grow: 1 },
			...(this.dock && !this.dockInChatColumn() ? [{ component: this.dock, shrink: 0 }] : []),
			...(!split && activity ? [{ component: activity, shrink: 0, visible: () => activity.visible }] : []),
			{ component: this.composerGap, shrink: 0 },
			...(!split ? [{ component: this.composer, shrink: 0 }] : []),
			{ component: this.bottomSpacer, shrink: 0 },
			{ component: this.footer, shrink: 0 },
		]);
	}

	/**
	 * Workspace top row for the effective mode, or undefined when the chat-only
	 * tree is exact (no state yet, chat mode, or no editor injected). The chat
	 * objects are retained in every branch; only composition changes. The chat
	 * transcript keeps its geometry column (side per `chatSide`); the explorer
	 * drawer and the grip follow the same LayoutResult.
	 */
	private workspaceRow(): Component | undefined {
		const workspace = this.workspace;
		const result = workspace?.layout;
		if (!workspace || !result || !workspace.editor) return undefined;
		if (result.effectiveMode === "split" && result.chat && result.editor) {
			return new HStack(
				workspace.entries(result, {
					component: this.chatColumn(true),
					basis: result.chat.columns,
					shrink: 0,
					grow: 0,
				}),
				{ gap: 0 },
			);
		}
		if (result.effectiveMode === "editor" && result.editor) {
			return new HStack(workspace.entries(result), { gap: 0 });
		}
		return undefined;
	}

	/**
	 * The chat column: the pinned bar (when configured) above the retained
	 * transcript ScrollView. Without a bar this is the exact legacy object,
	 * so existing composition tests and identities are unchanged. A fresh
	 * wrapper per build is fine — the ScrollView instance inside is retained
	 * and carries the scroll anchor.
	 */
	private chatColumn(split = false): Component {
		const bar = this.pinnedBar;
		const agentBar = this.agentBar;
		const dock = this.dockInChatColumn() ? this.dock : undefined;
		const activity = this.activityBar;
		if (!bar && !agentBar && !dock && !split) return this.transcriptScroll;
		return new VStack(
			[
				...(bar ? [{
					component: bar,
					basis: "auto" as const,
					shrink: 0,
					minSize: 0,
					visible: () => bar.visible && this.pinnedBarRows() > 0,
				}, {
					component: this.pinnedRule,
					basis: 1,
					shrink: 0,
					visible: () => bar.visible && this.pinnedBarRows() > 0,
				}] : []),
				...(agentBar ? [{ component: agentBar, shrink: 0, visible: () => agentBar.visible }] : []),
				{ component: this.transcriptScroll, basis: 0, grow: 1, minSize: dock && this.lastRows < 12 ? 0 : 1 },
				...(split && activity ? [{ component: activity, shrink: 0, visible: () => activity.visible }] : []),
				...(dock ? [{ component: dock, shrink: 0 }] : []),
				...(split ? [{ component: this.composerGap, shrink: 0 }, { component: this.composer, shrink: 0 }] : []),
			],
			{ gap: 0 },
		);
	}

	private rebuild(): void {
		this.currentRoot = this.build();
		if (isViewportTUI(this.tui)) this.tui.setLayoutRoot(this.viewportRoot);
	}
}

export function densityFor(columns: number, rows: number): Density {
	if (columns < 20 || rows < 6) return "degraded";
	if (rows <= 8) return "compact";
	if (rows <= 15) return "reduced";
	return "full";
}
