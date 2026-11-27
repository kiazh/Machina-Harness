// Workspace coordinator: the UI-side host/coordination cluster that used to
// live on App.
//
// It owns the v1 focus router (region geometry, reconciliation, mouse focus),
// completion/intelligence orchestration, save/conflict resolution, dirty-close
// sequencing, Problems/LSP status, and the Quick Open overlay. Editor models
// (EditorStore/EditorView/TabModel/ExplorerModel/ProblemsModel) stay where
// they are; this class orchestrates them through the narrow ports below.
//
// Contract:
// - One focus authority: `activeRegion` is the recorded region, while the TUI's
//   real focused component always wins during reconciliation when it still
//   names a composed region.
// - One lifecycle fence: every async continuation captures the workspace epoch
//   before its first await and publishes nothing after `isCurrent` fails.
// - Save is a compare-and-swap handshake owned by WorkspaceController; the
//   coordinator only routes the outcome and the explicit conflict choices.
// - Completion sessions capture (document, version, position, prefix) at
//   request time; acceptance is version-checked by the session itself.
// - Quick Open owns one overlay at a time; closing it dismisses the pane
//   before focus returns to the composer, so a late provider response can
//   never paint a dead overlay.

import { isAbsolute, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import {
	type Component,
	type Editor,
	type OverlayHandle,
	type SelectItem,
	type TUI,
} from "@earendil-works/pi-tui";
import { uriDisplayPath } from "../components/EditorPane.ts";
import type { AppShell } from "../components/AppShell.ts";
import type { NoticeKind } from "../features/types.ts";
import type { DocumentRecord, EditorStore, EditorViewState } from "../editor/model.ts";
import { sanitizeDisplay } from "../editor/display.ts";
import { languageForId } from "../editor/syntax.ts";
import {
	CompletionSession,
	LocationPicker,
	narrowLocationsPayload,
	RenameSession,
	type IntelligenceOperation,
	type IntelligenceRequest,
	type LocationChoice,
	type ProblemRow,
} from "../editor/intelligence.ts";
import { offsetToPosition, positionToOffset } from "../editor/positions.ts";
import { QuickOpenPane, type QuickOpenView } from "../editor/quickOpen.ts";
import {
	type CloseOutcome,
	type ConflictResolution,
	type DirtyCloseChoice,
	type IntelligenceOutcome,
	type OpenUriOptions,
	type OpenUriOutcome,
	type SaveConflictDescriptor,
	type SaveOutcome,
	type WorkspaceLspStatusSnapshot,
	type WorkspacePhase,
} from "../workspace/controller.ts";
import { displayUriName } from "../workspace/surface.ts";
import type { WorkspaceSurfaceHost } from "../workspace/surface.ts";
import type { FocusRegion, LayoutInput, LayoutResult } from "../workspace/state.ts";
import type {
	LspPosition,
	WorkspaceOpenResult,
	WorkspaceQuickOpenResult,
} from "../workspace/protocol.ts";

// ─── narrow ports over the retained editor models ──────────────────────────
//
// These are the exact calls the coordinator makes. They are structural ports,
// not copies: the live WorkspaceIntegration (surface.ts) satisfies them, and
// tests can bind a real EditorStore with fake controller/view seams.

/** The controller subset the coordinator drives (WorkspaceController). */
export interface WorkspaceControllerPort {
	readonly phase: WorkspacePhase;
	readonly identity: WorkspaceOpenResult | undefined;
	readonly generation: number;

	isCurrent(generation: number): boolean;
	request(operation: IntelligenceOperation, uri: string, version: number | null, position?: LspPosition): Promise<IntelligenceOutcome>;
	save(documentId: string): Promise<SaveOutcome>;
	resolveConflict(documentId: string, choice: "keep" | "reload" | "overwrite-explicit"): Promise<ConflictResolution>;
	closeTab(documentId: string): Promise<CloseOutcome>;
	resolveClose(documentId: string, choice: DirtyCloseChoice): Promise<CloseOutcome>;
	openUri(uri: string, options?: OpenUriOptions): Promise<OpenUriOutcome>;
	noteEdit(documentId: string): void;
	noteFocus(region: FocusRegion): void;
	getLayoutInput(): LayoutInput;
	lspStatusSnapshot(): WorkspaceLspStatusSnapshot;
}

/** Buffer/view-state reads and writes (EditorStore; sessions share it). */
export type WorkspaceStorePort = EditorStore;

/** Active-tab query (TabModel). */
export interface WorkspaceTabsPort {
	activeDocumentId(): string | null;
}

/** Explicit view mutations (EditorView). */
export interface WorkspaceViewPort {
	setInlineCompletion(text?: string): void;
	allowsAutomaticCompletion(): boolean;
	releaseDocument(documentId: string): void;
	setDocument(documentId: string): void;
	setView(state: EditorViewState): void;
}

/** Editor pane mutations (EditorPane). */
export interface WorkspacePanePort extends Component {
	setProblems(component?: Component): void;
	setLspStatus(text: string | undefined): void;
}

/** Diagnostics pull (ProblemsModel). */
export interface WorkspaceProblemsPort {
	refresh(uri: string, version?: number | null): Promise<unknown>;
}

/** The live workspace integration, narrowed to what the coordinator uses. */
export interface WorkspaceEditorIntegration {
	readonly controller: WorkspaceControllerPort;
	readonly store: WorkspaceStorePort;
	readonly tabs: WorkspaceTabsPort;
	readonly view: WorkspaceViewPort;
	readonly pane: WorkspacePanePort;
	readonly explorerPane: Component;
	readonly problemsPane: Component;
	readonly problemsModel: WorkspaceProblemsPort;
}

/**
 * Focused App facade for the coordinator.
 *
 * Invariants:
 * - `integration`/`layout` are live lookups: the surface is constructed after
 *   App and may open (or close) while the coordinator is alive.
 * - `captureFence`/`isCurrent` are the surface's single lifecycle epoch; the
 *   coordinator never invents a second generation.
 * - `overlayActive` covers app dialogs and the approval dock only. The Quick
 *   Open overlay is coordinator-owned and checked internally.
 * - `composerEditor` is the always-live fallback region and the only draft sink
 *   for explicit selection attachment.
 */
export interface WorkspaceHost {
	readonly tui: TUI;
	readonly composerEditor: Editor;
	readonly bindings: {
		matches(data: string, id: "explorer" | "explorerAlt" | "explorerSafe"): boolean;
	};

	integration(): WorkspaceEditorIntegration | undefined;
	layout(): LayoutResult | undefined;
	unsupported(): boolean;

	captureFence(): number;
	isCurrent(gen: number): boolean;
	isExiting(): boolean;
	/** True while an app dialog or the approval dock owns focus. */
	overlayActive(): boolean;

	restoreSplit(): void;
	syncShellIfChanged(): void;
	syncCursorBlink(): void;
	pauseEditorCursorBlink(): void;

	pick(title: string, items: SelectItem[]): Promise<SelectItem | undefined>;
	print(text: string, kind?: NoticeKind): void;
	request<T>(method: string, params: Record<string, unknown>, timeoutMs: number): Promise<T>;
	/** The single caret-blink phase (App owns the animation coordinator). */
	cursorBlockVisible(): boolean;
}

// ─── module helpers (moved verbatim from App) ──────────────────────────────

/** Inline-safe display text: one line, no controls or backticks. */
function inlineSafe(text: string): string {
	return sanitizeDisplay(text).replace(/`+/g, "").trim();
}

/** Fence long enough that no backtick run inside the text can close it. */
function fenceFor(text: string): string {
	let longest = 0;
	for (const run of text.match(/`+/g) ?? []) longest = Math.max(longest, run.length);
	return "`".repeat(Math.max(3, longest + 1));
}

/** Fence language token: identifier characters only (never markup). */
function fenceLanguage(languageId: string): string {
	return languageId.replace(/[^A-Za-z0-9_+.#-]/g, "");
}

/** Completion-prefix characters: the identifier run before the caret. */
const COMPLETION_PREFIX_CHAR = /[A-Za-z0-9_$]/;

/**
 * Trailing identifier at `cursorOffset`, used to request matching suggestions.
 * Punctuation/whitespace end the run, so the prefix never spans tokens.
 */
function completionPrefixBefore(text: string, cursorOffset: number): string {
	const offset = Math.max(0, Math.min(cursorOffset, text.length));
	let start = offset;
	while (start > 0 && COMPLETION_PREFIX_CHAR.test(text[start - 1] ?? "")) start -= 1;
	return text.slice(start, offset);
}

/**
 * Typed failure for the IntelligenceRequest adapter: sessions catch it and
 * record an honest failure (including `superseded`, whose result is gone).
 */
class WorkspaceIntelligenceFailure extends Error {
	readonly reason: "refused" | "failed" | "no-workspace" | "superseded";

	constructor(reason: "refused" | "failed" | "no-workspace" | "superseded", message: string) {
		super(message);
		this.name = "WorkspaceIntelligenceFailure";
		this.reason = reason;
	}
}

function intelligenceOutcomeMessage(outcome: IntelligenceOutcome): string {
	switch (outcome.status) {
		case "refused":
		case "failed":
			return outcome.message;
		case "no-workspace":
			return "no workspace is open";
		case "superseded":
			return "superseded by a newer request";
		default:
			return "unavailable";
	}
}

// ─── coordinator ───────────────────────────────────────────────────────────

/**
 * Remaining App-owned seams for `WorkspaceSurfaceHost`. Everything else is
 * supplied by the coordinator itself, so Surface wiring has one owner even
 * though construction stays with App/bootstrap.
 */
export interface WorkspaceSurfaceWiring {
	readonly shell: AppShell;
	setWorkspaceHints(hints: string): void;
	loadUiPreferences(): void;
	applyUiPreferences(): void;
}

export class WorkspaceCoordinator {
	private readonly host: WorkspaceHost;

	/** v1 focus router state (plan §8): the last meaningfully focused region. */
	private activeRegionValue: FocusRegion = "chat-composer";
	private problemsOpenValue = false;
	private completionSessionValue: CompletionSession | undefined;
	/** Debounce automatic requests without blocking local edits. */
	private automaticCompletionTimer: ReturnType<typeof setTimeout> | undefined;
	private quickOpenPaneValue: QuickOpenPane | undefined;
	private quickOpenHandleValue: OverlayHandle | undefined;

	constructor(host: WorkspaceHost) {
		this.host = host;
	}

	/**
	 * Build the UI seam `WorkspaceSurface` calls back into. The session
	 * lifecycle (construct/open/switch/close) stays with bootstrap; this
	 * method only binds the workspace commands and focus/completion seams to
	 * the coordinator, so App cannot drift from the coordinator's state.
	 */
	workspaceSurfaceHost(wiring: WorkspaceSurfaceWiring): WorkspaceSurfaceHost {
		return {
			request: (method, params, timeoutMs) => this.host.request(method, params, timeoutMs),
			requestRender: () => this.host.tui.requestRender(),
			terminalSize: () => ({
				columns: Math.max(0, this.host.tui.terminal.columns),
				rows: Math.max(0, this.host.tui.terminal.rows),
			}),
			notify: (kind, text) => this.host.print(text, kind),
			print: (text, kind) => this.host.print(text, kind),
			shell: wiring.shell,
			setWorkspaceHints: wiring.setWorkspaceHints,
			setFocusForRegion: (region) => this.setFocusForRegion(region),
			noteRegionFocus: (region) => this.noteRegionFocus(region),
			focusRegions: () => this.focusRegions(),
			activeRegion: () => this.activeRegionValue,
			reconcileFocus: () => this.reconcileFocus(),
			completionActive: () => this.completionActive(),
			clearCompletion: () => this.clearCompletion(),
			scheduleAutomaticCompletion: () => this.scheduleAutomaticCompletion(),
			openProblems: () => this.openProblems(),
			pauseEditorCursorBlink: () => this.host.pauseEditorCursorBlink(),
			isCursorBlockVisible: () => this.host.cursorBlockVisible(),
			syncCursorBlink: () => this.host.syncCursorBlink(),
			refreshProblemsForActiveDocument: () => this.refreshProblemsForActiveDocument(),
			refreshLspStatus: () => this.refreshLspStatus(),
			lspStatusText: () => this.lspStatusText(),
			closeWorkspaceTab: (documentId) => this.closeWorkspaceTab(documentId),
			saveActiveDocument: () => this.saveActiveDocument(),
			openWorkspacePath: (path, options) => this.openWorkspacePath(path, options),
			openProblem: (row) => this.openProblem(row),
			closeProblems: () => this.closeProblems(),
			openQuickOpen: () => this.openQuickOpen(),
			startCompletion: () => this.startCompletion(),
			attachSelectionToDraft: () => this.attachSelectionToDraft(),
			intelligenceRequest: () => this.intelligenceRequest(),
			closeActiveWorkspaceTab: () => this.closeActiveWorkspaceTab(),
			loadUiPreferences: wiring.loadUiPreferences,
			applyUiPreferences: wiring.applyUiPreferences,
			isExiting: () => this.host.isExiting(),
		};
	}

	// ─── focus router (plan §8, v1) ────────────────────────────────────────

	/** The recorded focus region (the TUI's real focus may differ mid-key). */
	get activeRegion(): FocusRegion {
		return this.activeRegionValue;
	}

	/** True while an inline completion session is live. */
	completionActive(): boolean {
		return this.completionSessionValue !== undefined;
	}

	/** True while the Problems section is mounted. */
	problemsVisible(): boolean {
		return this.problemsOpenValue;
	}

	/** The completion session exposed to the keyboard router's narrow port. */
	completionSession(): CompletionSession | undefined {
		return this.completionSessionValue;
	}

	/** True while the Quick Open overlay owns keys. */
	quickOpenActive(): boolean {
		return this.quickOpenPaneValue !== undefined;
	}

	/**
	 * Regions the current geometry makes visible, in cycle order. The editor
	 * is skipped while chat is maximized and the explorer while it is not
	 * docked; the Problems region exists only while its section is open.
	 */
	focusRegions(): FocusRegion[] {
		const regions: FocusRegion[] = ["chat-composer"];
		const layout = this.host.layout();
		if (layout?.editor) regions.push("editor");
		if (layout?.explorer) regions.push("explorer");
		if (this.problemsOpenValue && layout?.editor) regions.push("problems");
		return regions;
	}

	/** The mounted component that owns one visible focus region. */
	private regionComponent(region: FocusRegion): Component {
		const integration = this.host.integration();
		if (integration) {
			if (region === "editor") return integration.pane;
			if (region === "explorer") return integration.explorerPane;
			if (region === "problems") return integration.problemsPane;
		}
		return this.host.composerEditor;
	}

	/** The region a mounted component stands for (undefined for non-regions). */
	private regionForComponent(component: Component | null): FocusRegion | undefined {
		if (component === this.host.composerEditor) return "chat-composer";
		const integration = this.host.integration();
		if (!integration) return undefined;
		if (component === integration.pane) return "editor";
		if (component === integration.explorerPane) return "explorer";
		if (component === integration.problemsPane) return "problems";
		return undefined;
	}

	/**
	 * Deterministic focus reconciliation for every layout/visibility change and
	 * before each dispatched key. The TUI's real focused component wins when it
	 * is a known region that the current geometry still composes; otherwise the
	 * recorded region is re-validated. The composer is the always-live fallback.
	 * Overlays (dialogs, approvals, quick open) own their own focus and are
	 * never stolen from.
	 */
	reconcileFocus(): void {
		if (this.host.isExiting()) return;
		if (this.host.overlayActive() || this.quickOpenPaneValue) return;
		const regions = this.focusRegions();
		const probe = this.host.tui as TUI & { getFocusedComponent?(): Component | null; focused?: Component | null };
		const current =
			typeof probe.getFocusedComponent === "function" ? probe.getFocusedComponent() : (probe.focused ?? null);
		const focusedRegion = this.regionForComponent(current);
		const next =
			focusedRegion !== undefined && regions.includes(focusedRegion)
				? focusedRegion
				: regions.includes(this.activeRegionValue)
					? this.activeRegionValue
					: "chat-composer";
		const expected = this.regionComponent(next);
		if (next !== "editor") this.cancelAutomaticCompletion();
		if (next !== this.activeRegionValue) {
			this.activeRegionValue = next;
			if (next !== "editor" && this.completionSessionValue) this.clearCompletion();
			this.host.integration()?.controller.noteFocus(next);
		}
		if (current !== expected) this.host.tui.setFocus(expected);
		this.host.syncCursorBlink();
	}

	cycleFocus(direction: 1 | -1): void {
		const regions = this.focusRegions();
		// A single visible region still takes focus: after a chat maximize
		// must hand the composer its focus instead of early-returning.
		if (regions.length <= 1) {
			this.setFocusForRegion(regions[0] ?? "chat-composer");
			return;
		}
		const index = regions.indexOf(this.activeRegionValue);
		const next =
			index < 0
				? (regions[0] ?? "chat-composer")
				: (regions[(index + direction + regions.length) % regions.length] ?? "chat-composer");
		this.setFocusForRegion(next);
	}

	/** Focus one region: retarget the TUI, record it, and re-sync narrow fallback. */
	setFocusForRegion(region: FocusRegion): void {
		const integration = this.host.integration();
		const layout = this.host.layout();
		// Only a region the current geometry actually composes may take TUI
		// focus (typing must never land in a hidden editor). The region intent
		// still reaches the controller so `preferredPane`/restore stay honest.
		const visible =
			region === "chat-composer" ||
			(region === "editor" && layout?.editor !== undefined) ||
			(region === "explorer" && layout?.explorer !== undefined) ||
			(region === "problems" && this.problemsOpenValue && layout?.editor !== undefined);
		this.activeRegionValue = visible ? region : "chat-composer";
		if (this.activeRegionValue !== "editor") this.cancelAutomaticCompletion();
		// Inline suggestions belong to the editor and disappear on focus loss.
		if (this.activeRegionValue !== "editor" && this.completionSessionValue) this.clearCompletion();
		if (!integration || !visible) {
			this.host.tui.setFocus(this.host.composerEditor);
		} else {
			switch (region) {
				case "editor":
					this.host.tui.setFocus(integration.pane);
					break;
				case "explorer":
					this.host.tui.setFocus(integration.explorerPane);
					break;
				case "problems":
					this.host.tui.setFocus(integration.problemsPane);
					break;
				default:
					this.host.tui.setFocus(this.host.composerEditor);
			}
		}
		integration?.controller.noteFocus(region);
		this.host.syncShellIfChanged();
		this.host.syncCursorBlink();
		this.host.tui.requestRender();
	}

	/** Mouse-driven region tracking (component onFocusRequested seam). */
	noteRegionFocus(region: FocusRegion): void {
		// A mouse seam can report a region the layout no longer composes (the
		// pane was hidden between render and click). Clamp to the always-live
		// composer instead of leaving editor-scoped gates armed on a dead pane.
		const next = this.focusRegions().includes(region) ? region : "chat-composer";
		if (this.activeRegionValue === next) return;
		this.activeRegionValue = next;
		if (next !== "editor") this.cancelAutomaticCompletion();
		// Inline completion is editor-scoped: mouse focus leaving
		// the editor unmounts it instead of leaving a visible orphan that could
		// later accept a stray Enter.
		if (next !== "editor" && this.completionSessionValue) this.clearCompletion();
		this.host.integration()?.controller.noteFocus(next);
		this.host.syncShellIfChanged();
		this.host.syncCursorBlink();
	}

	/**
	 * Ctrl+T / Ctrl+Shift+E / Alt+E. Ctrl+Shift+E is the browser-safe
	 * alias because Chrome/Edge reserve Ctrl+T before the page sees it.
	 */
	matchesExplorerKey(data: string): boolean {
		return (
			this.host.bindings.matches(data, "explorer") ||
			this.host.bindings.matches(data, "explorerAlt") ||
			this.host.bindings.matches(data, "explorerSafe")
		);
	}

	// ─── documents + intelligence ──────────────────────────────────────────

	/** Editor context for intelligence: active record + LSP cursor position. */
	activeEditorContext(): { document: DocumentRecord; position: LspPosition } | undefined {
		const integration = this.host.integration();
		if (!integration || integration.controller.phase !== "ready") return undefined;
		const documentId = integration.tabs.activeDocumentId();
		if (!documentId) return undefined;
		const document = integration.store.getDocument(documentId);
		if (!document) return undefined;
		const state = integration.store.getViewState(documentId);
		return { document, position: offsetToPosition(document.text, state?.cursorOffset ?? 0) };
	}

	/** Root-relative display path for attachment banners (never a raw URI). */
	workspaceRelativePath(uri: string): string {
		const display = uriDisplayPath(uri);
		const normalizedDisplay = display.replace(/^\/(?=[A-Za-z]:\/)/, "");
		const rawRoot = this.host.integration()?.controller.identity?.resolvedRoot;
		const root = rawRoot ? resolve(rawRoot).replace(/\\/g, "/") : undefined;
		if (root) {
			const prefix = root.endsWith("/") ? root : `${root}/`;
			if (normalizedDisplay === root) return inlineSafe(normalizedDisplay);
			if (normalizedDisplay.startsWith(prefix)) return inlineSafe(normalizedDisplay.slice(prefix.length));
		}
		const parts = display.split("/").filter((segment) => segment.length > 0);
		return inlineSafe(parts[parts.length - 1] ?? display);
	}

	/**
	 * T10 explicit selection attachment (plan §20): move the active editor
	 * selection into the composer draft with a visible scope header. This is
	 * the ONLY path that lets unsaved buffer text reach a message: the user
	 * invokes it directly (/attach or Ctrl+Shift+A in Kitty-protocol
	 * terminals), the notice names the file, line range, version and unsaved
	 * flag, and nothing is submitted — the user still writes and sends the
	 * message. Opening or viewing a file never calls this.
	 */
	attachSelectionToDraft(): { ok: boolean; notice: string } {
		const integration = this.host.integration();
		if (!integration || integration.controller.phase !== "ready") {
			return { ok: false, notice: "Open a workspace first (/workspace)." };
		}
		const documentId = integration.tabs.activeDocumentId();
		const record = documentId ? integration.store.getDocument(documentId) : undefined;
		if (!documentId || !record) {
			return { ok: false, notice: "No file is open to attach a selection from." };
		}
		const state = integration.store.getViewState(documentId);
		const cursor = state?.cursorOffset ?? 0;
		const anchor = state?.selectionAnchorOffset;
		const start = anchor === undefined ? cursor : Math.min(anchor, cursor);
		const end = anchor === undefined ? cursor : Math.max(anchor, cursor);
		if (start >= end) return { ok: false, notice: "No selection to attach" };
		const selected = record.text.slice(start, end);
		// 1-based line range for display; a selection ending exactly at a line
		// start still belongs to the previous line's text in the shown span.
		const startLine = offsetToPosition(record.text, start).line + 1;
		const endPosition = offsetToPosition(record.text, end);
		let endLine = endPosition.line + 1;
		if (endPosition.character === 0 && endLine > startLine) endLine -= 1;
		const lineLabel = startLine === endLine ? `L${startLine}` : `L${startLine}–${endLine}`;
		const relativePath = this.workspaceRelativePath(record.uri);
		const versionLabel = record.dirty ? `unsaved v${record.version}` : "saved";
		const noticeLabel = record.dirty ? `unsaved, v${record.version}` : `saved, v${record.version}`;
		const banner = `<attached: ${relativePath} ${lineLabel} (${versionLabel})>`;
		const fence = fenceFor(selected);
		// A fence must close on its own line: add a trailing newline only when
		// the selection does not already end with one, so the fenced content
		// stays exactly the selected text.
		const body = selected.endsWith("\n") ? selected : `${selected}\n`;
		const block = `${banner}\n${fence}${fenceLanguage(record.languageId)}\n${body}${fence}`;
		this.host.composerEditor.insertTextAtCursor(`${block}\n`);
		this.host.tui.requestRender();
		const rawUri = record.uri.length > 300 ? `${record.uri.slice(0, 299)}…` : record.uri;
		return {
			ok: true,
			notice: `Attached ${relativePath} ${lineLabel} (${noticeLabel}) — ${inlineSafe(rawUri)} — review and send when ready.`,
		};
	}

	/**
	 * Adapt the controller's IntelligenceOutcome union to the frozen
	 * IntelligenceRequest seam: ok → result; refused/failed/no-workspace (and
	 * superseded, whose result is gone) throw a typed error so sessions record
	 * an honest failure and never render a fabricated result.
	 */
	intelligenceRequest(): IntelligenceRequest {
		return async (operation, uri, version, position) => {
			const integration = this.host.integration();
			if (!integration) {
				throw new WorkspaceIntelligenceFailure("no-workspace", "workspace is not available");
			}
			const outcome = await integration.controller.request(operation, uri, version, position);
			switch (outcome.status) {
				case "ok":
					return outcome.result;
				case "superseded":
					throw new WorkspaceIntelligenceFailure("superseded", "superseded by a newer request");
				case "refused":
					throw new WorkspaceIntelligenceFailure("refused", outcome.message);
				case "failed":
					throw new WorkspaceIntelligenceFailure("failed", outcome.message);
				case "no-workspace":
					throw new WorkspaceIntelligenceFailure("no-workspace", "no workspace is open");
			}
		};
	}

	/** Ctrl+Space and idle typing share one version-fenced inline suggestion. */
	async startCompletion(automatic = false): Promise<void> {
		this.cancelAutomaticCompletion();
		const integration = this.host.integration();
		const context = this.activeEditorContext();
		if (!integration || !context) return; // no open file: do not fire (v1)
		if (!automatic && this.activeRegionValue !== "editor") this.setFocusForRegion("editor");
		if (this.activeRegionValue !== "editor") return;
		const gen = this.host.captureFence();
		this.clearCompletion();
		const session = new CompletionSession({
			store: integration.store,
			request: this.intelligenceRequest(),
			uri: context.document.uri,
			documentId: context.document.id,
			position: context.position,
			prefix: completionPrefixBefore(context.document.text,
				integration.store.getViewState(context.document.id)?.cursorOffset ?? 0),
		});
		this.completionSessionValue = session;
		const state = await session.start();
		if (this.completionSessionValue !== session || this.host.isExiting() || !this.host.isCurrent(gen)) return;
		if (state === "visible") {
			// A completion that rewrites existing text, imports names or inserts
			// snippets cannot be represented honestly as an inline suffix.
			const preview = session.inlinePreview();
			if (preview !== undefined) {
				integration.view.setInlineCompletion(preview);
				this.host.tui.requestRender();
				return;
			}
		}
		// Nothing to show: one quiet honest notice per explicit invocation.
		if (!automatic && session.lastDropReason === "unavailable") this.host.print("Completion is unavailable here.", "info");
		else if (!automatic && session.lastDropReason === "error") this.host.print("Completion request failed.", "info");
		this.clearCompletion();
	}

	acceptCompletion(): void {
		const session = this.completionSessionValue;
		if (!session) return;
		const result = session.accept();
		this.clearCompletion();
		if (result.ok) {
			this.host.pauseEditorCursorBlink();
			for (const application of result.applied) {
				this.host.integration()?.controller.noteEdit(application.documentId);
			}
			this.refreshEditorView();
		} else if (result.reason !== "not-open") {
			this.host.print(`Completion not applied: ${result.message}`, "info");
		}
		this.host.tui.requestRender();
	}

	cancelAutomaticCompletion(): void {
		if (this.automaticCompletionTimer !== undefined) clearTimeout(this.automaticCompletionTimer);
		this.automaticCompletionTimer = undefined;
	}

	/** Keep editing local; ask for suggestions once typing pauses. */
	scheduleAutomaticCompletion(): void {
		this.cancelAutomaticCompletion();
		if (this.activeRegionValue !== "editor" || this.completionSessionValue || this.host.isExiting()) return;
		const context = this.activeEditorContext();
		const integration = this.host.integration();
		const store = integration?.store;
		if (!context || !store || context.document.readOnly || languageForId(context.document.languageId) === "plain") return;
		const selection = store.getViewState(context.document.id);
		if (!selection || (selection.selectionAnchorOffset !== undefined && selection.selectionAnchorOffset !== selection.cursorOffset)) return;
		const cursor = selection.cursorOffset;
		const previous = context.document.text[cursor - 1] ?? "";
		const prefix = completionPrefixBefore(context.document.text, cursor);
		const member = context.document.text[cursor - prefix.length - 1] === ".";
		if (previous !== "." && (!COMPLETION_PREFIX_CHAR.test(previous) || (!member && prefix.length < 2))) return;
		const { id, version } = context.document;
		const gen = this.host.captureFence();
		this.automaticCompletionTimer = setTimeout(() => {
			this.automaticCompletionTimer = undefined;
			if (this.host.isExiting() || !this.host.isCurrent(gen) || this.activeRegionValue !== "editor"
				|| this.host.overlayActive() || this.quickOpenPaneValue || this.completionSessionValue) return;
			const current = this.activeEditorContext();
			if (current?.document.id !== id || current.document.version !== version
				|| store.getViewState(id)?.cursorOffset !== cursor || this.lspStatusText() !== "ready"
				|| !this.host.integration()?.view.allowsAutomaticCompletion()) return;
			void this.startCompletion(true);
		}, 150);
	}

	clearCompletion(): void {
		this.cancelAutomaticCompletion();
		this.completionSessionValue?.close("dismissed");
		this.completionSessionValue = undefined;
		this.host.integration()?.view.setInlineCompletion(undefined);
		this.host.tui.requestRender();
	}

	/** Drop local completion state when the gateway is gone, without RPCs. */
	discardCompletion(): void {
		this.cancelAutomaticCompletion();
		this.completionSessionValue = undefined;
	}

	/** Request, pick among multiple targets, then jump. */
	async runDefinitionOrReferences(operation: "definition" | "references"): Promise<void> {
		const integration = this.host.integration();
		const context = this.activeEditorContext();
		if (!integration || !context) return;
		const gen = this.host.captureFence();
		const outcome = await integration.controller.request(
			operation,
			context.document.uri,
			context.document.version,
			context.position,
		);
		if (this.host.isExiting() || this.host.integration() !== integration || !this.host.isCurrent(gen)) return;
		if (outcome.status !== "ok") {
			this.host.print(`${operation} unavailable: ${intelligenceOutcomeMessage(outcome)}`, "info");
			return;
		}
		const payload = narrowLocationsPayload(outcome.result.result);
		const picker = new LocationPicker(payload.locations, { omitted: payload.omitted });
		if (picker.size === 0) {
			this.host.print(`No ${operation} found.`, "info");
			return;
		}
		if (picker.size === 1) {
			const only = picker.current();
			if (only) await this.openLocation(only);
			return;
		}
		const targets = picker.targets();
		const choice = await this.host.pick(
			`${operation === "definition" ? "Definitions" : "References"} (${targets.length})`,
			targets.map((target, index) => ({
				value: String(index),
				label: `${uriDisplayPath(target.uri)}:${target.lineLabel}`,
			})),
		);
		if (!choice) return;
		if (!this.host.isCurrent(gen)) return;
		const target = targets[Number(choice.value)];
		if (target) await this.openLocation(target);
	}

	/** Open one jump target and place the caret (definition-style navigation). */
	private async openLocation(target: LocationChoice): Promise<void> {
		const integration = this.host.integration();
		if (!integration) return;
		const gen = this.host.captureFence();
		const outcome = await integration.controller.openUri(target.uri);
		if (!this.host.isCurrent(gen)) return;
		if (outcome.status !== "opened" && outcome.status !== "focused") return;
		const record = integration.store.getDocument(outcome.documentId);
		if (record) {
			const offset = positionToOffset(record.text, { line: target.line, character: target.character });
			integration.store.setSelection(outcome.documentId, { cursorOffset: offset });
			integration.view.setView({
				documentId: outcome.documentId,
				cursorOffset: offset,
				scrollLine: Math.max(0, target.line - 2),
				horizontalCellOffset: 0,
			});
		}
		this.host.tui.requestRender();
	}

	/** Prepare a rename preview when available. */
	async runRenamePreview(): Promise<void> {
		const integration = this.host.integration();
		const context = this.activeEditorContext();
		if (!integration || !context) return;
		const session = new RenameSession({
			store: integration.store,
			uri: context.document.uri,
			documentId: context.document.id,
			position: context.position,
		});
		const gen = this.host.captureFence();
		const result = await session.start();
		if (!this.host.isCurrent(gen)) return;
		if (!result.ok) {
			// Unavailable is the honest state until the service offers rename
			// (the operation is not in the frozen intelligence allowlist).
			this.host.print(result.reason, "info");
			return;
		}
		const choice = await this.host.pick(
			`Rename: ${result.preview.fileCount} file(s), ${result.preview.totalEdits} edit(s)`,
			[
				{ value: "cancel", label: "Cancel" },
				{ value: "apply", label: "Apply rename" },
			],
		);
		if (choice?.value !== "apply") return;
		if (!this.host.isCurrent(gen)) return;
		const applied = session.apply(true);
		if (!applied.ok) {
			this.host.print(`Rename not applied: ${applied.message}`, "error");
			return;
		}
		for (const application of applied.applied) {
			integration.controller.noteEdit(application.documentId);
			this.refreshEditorView(application.documentId);
		}
		this.host.print("Rename applied.", "info");
	}

	// ─── save / conflict / close ───────────────────────────────────────────

	/** Ctrl+S: save the active buffer; conflicts open the explicit dialog. */
	async saveActiveDocument(): Promise<void> {
		const integration = this.host.integration();
		if (!integration) return;
		const documentId = integration.tabs.activeDocumentId();
		// No open file: do not fire (consistent with completion/definition/rename).
		if (!documentId) return;
		await this.saveDocument(documentId);
	}

	async saveDocument(documentId: string): Promise<boolean> {
		const integration = this.host.integration();
		if (!integration) return false;
		// MCH-012: a save (or its conflict detour) belongs to the workspace
		// generation that started it. A root switch/close/exit while the RPC is
		// parked must not publish into, or resolve against, the successor.
		const gen = this.host.captureFence();
		const outcome = await integration.controller.save(documentId);
		if (!this.host.isCurrent(gen)) return false;
		switch (outcome.status) {
			case "saved":
				this.host.tui.requestRender(); // status row: dirty marker cleared
				return true;
			case "conflict":
				await this.resolveSaveConflict(outcome.conflict);
				return false;
			case "not-found":
				this.host.print("No file open to save.", "info");
				return false;
			default:
				// refused/failed were notified by the controller with the reason.
				return false;
		}
	}

	/**
	 * Reload disk / Overwrite (explicit) / Keep editing, per plan §9. Returns
	 * the controller resolution so close-flow callers can continue the close
	 * after a conflict detour (finding 7).
	 */
	async resolveSaveConflict(conflict: SaveConflictDescriptor): Promise<ConflictResolution | undefined> {
		const integration = this.host.integration();
		if (!integration) return undefined;
		// MCH-012: the picker can stay open across a root switch/close. The
		// resolution (and a possible overwrite write) must target the workspace
		// that raised the conflict, never its successor.
		const gen = this.host.captureFence();
		const choice = await this.host.pick(`Save conflict: ${displayUriName(conflict.uri)}`, [
			{ value: "reload", label: "Reload disk", description: "Discard buffer edits and adopt the file on disk" },
			{ value: "overwrite-explicit", label: "Overwrite (explicit)", description: "Write this buffer over the file on disk" },
			{ value: "keep", label: "Keep editing", description: "Nothing is written; the buffer stays dirty" },
		]);
		if (!this.host.isCurrent(gen)) return undefined;
		const resolution = await integration.controller.resolveConflict(
			conflict.documentId,
			(choice?.value as "keep" | "reload" | "overwrite-explicit" | undefined) ?? "keep",
		);
		if (!this.host.isCurrent(gen)) return undefined;
		if (resolution.status === "reloaded" || resolution.status === "saved" || resolution.status === "kept") {
			this.refreshEditorView(conflict.documentId);
		}
		this.host.tui.requestRender();
		return resolution;
	}

	/** Ctrl+W / `/editor close` path: close the active tab, dirty-guarded. */
	closeActiveWorkspaceTab(): void {
		const documentId = this.host.integration()?.tabs.activeDocumentId();
		if (!documentId) return;
		void this.closeWorkspaceTab(documentId);
	}

	/** Tab-strip close (middle-click / onCloseTab): decision dialog when dirty. */
	async closeWorkspaceTab(documentId: string): Promise<void> {
		const integration = this.host.integration();
		if (!integration) return;
		// MCH-012: the dirty-close picker and the conflict detour are both
		// user-paced awaits; a root switch/close/exit in between retires the
		// whole request (the buffer itself is retained by the controller).
		const gen = this.host.captureFence();
		const outcome = await integration.controller.closeTab(documentId);
		if (!this.host.isCurrent(gen)) return;
		if (outcome.status === "needs-decision") {
			const record = integration.store.getDocument(documentId);
			const choice = await this.host.pick(`Close ${record ? displayUriName(record.uri) : "file"}?`, [
				{ value: "save", label: "Save and close" },
				{ value: "discard", label: "Discard changes" },
				{ value: "cancel", label: "Cancel" },
			]);
			if (!choice || !this.host.isCurrent(gen)) return;
			const resolved = await integration.controller.resolveClose(documentId, choice.value as DirtyCloseChoice);
			if (!this.host.isCurrent(gen)) return;
			if (resolved.status === "closed") this.afterTabClosed(documentId);
			else if (resolved.status === "save-conflict") {
				// The close decision survives the conflict detour (finding 7):
				// once the save/overwrite (or reload) cleared the buffer, run the
				// already-chosen close again — otherwise the tab silently stays
				// open after the user answered "Save and close". Keep editing
				// leaves the tab open (and dirty) on purpose.
				const conflict = await this.resolveSaveConflict(resolved.conflict);
				if (!this.host.isCurrent(gen)) return;
				if (conflict?.status === "saved" || conflict?.status === "reloaded") {
					const closed = await integration.controller.resolveClose(documentId, "discard");
					if (this.host.isCurrent(gen) && closed.status === "closed") this.afterTabClosed(documentId);
				}
			} else if (resolved.status === "needs-decision") {
				this.host.print("The file changed while saving; save again or discard.", "warning");
			}
			return;
		}
		if (outcome.status === "closed") this.afterTabClosed(documentId);
	}

	afterTabClosed(documentId: string): void {
		const integration = this.host.integration();
		if (!integration) return;
		if (this.completionSessionValue?.documentId === documentId) this.clearCompletion();
		integration.view.releaseDocument(documentId);
		const active = integration.tabs.activeDocumentId();
		if (active) integration.view.setDocument(active);
		this.refreshLspStatus();
		this.host.syncCursorBlink();
		this.host.tui.requestRender();
	}

	// ─── open / refresh ────────────────────────────────────────────────────

	/** Open a root-relative workspace path (explorer/quick open) as a file URI. */
	async openWorkspacePath(path: string, options: { preview?: boolean; focus?: boolean } = {}): Promise<void> {
		const integration = this.host.integration();
		if (!integration) return;
		const identity = integration.controller.identity;
		if (!identity) {
			this.host.print("Workspace is not available.", "warning");
			return;
		}
		const gen = this.host.captureFence();
		const uri = pathToFileURL(isAbsolute(path) ? path : join(identity.resolvedRoot, path)).href;
		const outcome = await integration.controller.openUri(uri, options);
		// MCH-012: the read may outlive a root switch; a retired document id
		// must not be published into the successor's view.
		if (!this.host.isCurrent(gen)) return;
		if (outcome.status === "opened" || outcome.status === "focused") {
			if (this.completionSessionValue?.documentId !== outcome.documentId) this.clearCompletion();
			integration.view.setDocument(outcome.documentId);
			void this.refreshProblemsForActiveDocument();
			this.refreshLspStatus();
			// Opening a file reveals the editor from the default chat canvas.
			// Sync the composed geometry before claiming editor focus; otherwise
			// the focus router still sees a chat-only layout and falls back.
			if (options.focus !== false) {
				this.host.syncShellIfChanged();
				this.setFocusForRegion("editor");
			}
			this.host.syncCursorBlink();
			this.host.tui.requestRender();
		}
	}

	/** Problems Enter/click: open the file and place the caret at the row. */
	async openProblem(row: ProblemRow): Promise<void> {
		const integration = this.host.integration();
		if (!integration) return;
		const gen = this.host.captureFence();
		const outcome = await integration.controller.openUri(row.uri);
		if (!this.host.isCurrent(gen)) return;
		if (outcome.status !== "opened" && outcome.status !== "focused") return;
		const record = integration.store.getDocument(outcome.documentId);
		if (record) {
			const offset = positionToOffset(record.text, { line: row.line, character: row.character });
			integration.store.setSelection(outcome.documentId, { cursorOffset: offset });
			integration.view.setView({
				documentId: outcome.documentId,
				cursorOffset: offset,
				scrollLine: Math.max(0, row.line - 2),
				horizontalCellOffset: 0,
			});
		}
		this.host.tui.requestRender();
	}

	/** Pull cursor/selection from the store after an external store mutation. */
	refreshEditorView(documentId?: string): void {
		const integration = this.host.integration();
		if (!integration) return;
		const id = documentId ?? integration.tabs.activeDocumentId();
		if (this.completionSessionValue && this.completionSessionValue.documentId !== id) this.clearCompletion();
		if (!id) return;
		const state = integration.store.getViewState(id);
		if (state) integration.view.setView(state);
		this.host.tui.requestRender();
	}

	// ─── problems + LSP ────────────────────────────────────────────────────

	/** Feed the Problems pane: mounted on demand, refocused on request. */
	openProblems(): void {
		const integration = this.host.integration();
		if (!integration || this.host.unsupported()) {
			this.host.print("Workspace is not available.", "warning");
			return;
		}
		if (!this.problemsOpenValue) {
			this.problemsOpenValue = true;
			integration.pane.setProblems(integration.problemsPane);
		}
		if (!this.host.layout()?.editor) {
			// The section lives inside the editor pane: bring the split back
			// when the geometry allows it, then focus; otherwise say so.
			this.host.restoreSplit();
		}
		if (this.host.layout()?.editor) this.setFocusForRegion("problems");
		else this.host.print("The editor pane is not visible at this terminal size.", "warning");
		void this.refreshProblemsForActiveDocument();
	}

	/** Esc/Problems dismissal: unmount the section and return focus to the editor. */
	closeProblems(): void {
		const integration = this.host.integration();
		if (!integration || !this.problemsOpenValue) return;
		this.problemsOpenValue = false;
		integration.pane.setProblems(undefined);
		if (this.activeRegionValue === "problems") this.setFocusForRegion("editor");
		else this.host.tui.requestRender();
	}

	/** Diagnostics pull for the active document (tab focus / after open). */
	async refreshProblemsForActiveDocument(): Promise<void> {
		const integration = this.host.integration();
		if (!integration || integration.controller.phase !== "ready") return;
		const documentId = integration.tabs.activeDocumentId();
		const record = documentId ? integration.store.getDocument(documentId) : undefined;
		if (!record) return;
		const gen = this.host.captureFence();
		await integration.problemsModel.refresh(record.uri, record.version);
		// The model is URI/version-keyed; a superseded refresh must not repaint
		// the successor's pane from the retired root's diagnostics.
		if (this.host.isCurrent(gen)) this.host.tui.requestRender();
	}

	/**
	 * Right-aligned editor status text for the ACTIVE document's language
	 * (finding 3): the raw LSP state only ("ready", "starting…", "missing"),
	 * never repeating the language name shown on the left. No reported server
	 * means no segment — absence is not invented as a state.
	 */
	lspStatusText(): string | undefined {
		const integration = this.host.integration();
		const controller = integration?.controller;
		if (!integration || !controller || controller.phase !== "ready") return undefined;
		const documentId = integration.tabs.activeDocumentId();
		const record = documentId ? integration.store.getDocument(documentId) : undefined;
		if (!record) return undefined;
		const target = languageForId(record.languageId);
		if (target === "plain") return undefined;
		const family = (language: string): string => language === "javascript" ? "typescript" : language;
		const matches = (id: string): boolean => family(languageForId(id)) === family(target);
		const shown = (state: string): string => (state === "starting" ? "starting…" : state);
		const snapshot = controller.lspStatusSnapshot();
		for (const server of snapshot.servers) {
			if (matches(server.languageId || server.profile)) return shown(server.state);
		}
		for (const [profile, entry] of Object.entries(snapshot.profiles)) {
			if (matches(profile)) return shown(entry.state);
		}
		return undefined;
	}

	refreshLspStatus(): void {
		this.host.integration()?.pane.setLspStatus(this.lspStatusText());
	}

	// ─── quick open ────────────────────────────────────────────────────────

	/** Ctrl+P /quickopen: bounded overlay picker over `workspace.quickOpen`. */
	async openQuickOpen(): Promise<void> {
		const integration = this.host.integration();
		if (!integration || this.host.unsupported() || integration.controller.phase !== "ready") {
			this.host.print("Open a workspace first (/workspace).", "warning");
			return;
		}
		if (this.quickOpenPaneValue) return;
		const pane = new QuickOpenPane((query) => this.workspaceQuickOpen(query), {
			onChange: () => this.host.tui.requestRender(),
		});
		pane.onOpen = (entry) => {
			this.closeQuickOpen();
			void this.openWorkspacePath(entry.path, { preview: true });
		};
		pane.onClose = () => this.closeQuickOpen();
		this.quickOpenPaneValue = pane;
		this.quickOpenHandleValue = this.host.tui.showOverlay(pane, {
			width: "100%",
			maxHeight: "100%",
			// Same palette family as the skills directory: open above so the
			// list reads top-down and the composer stays visible.
			anchor: "top-center",
		});
		pane.activate();
		this.host.tui.setFocus(pane);
		this.host.syncCursorBlink();
		this.host.tui.requestRender();
	}

	closeQuickOpen(): void {
		const pane = this.quickOpenPaneValue;
		if (!pane) return;
		this.quickOpenPaneValue = undefined;
		this.quickOpenHandleValue?.hide();
		this.quickOpenHandleValue = undefined;
		// P0-20: dismiss the pane too. Choice (onOpen) and exit paths close
		// without ever running dismiss(), leaving its provider timer alive:
		// a request scheduled for the dead overlay would still beat the
		// debounce window and hit the workspace with a query nobody asked
		// for. Clear the callback FIRST: dismiss() would otherwise re-enter
		// this funnel through onClose.
		pane.onClose = undefined;
		pane.dismiss();
		// Dismissing the overlay returns focus to the composer (finding 3);
		// the region router keeps the recorded region honest.
		if (!this.host.isExiting()) this.setFocusForRegion("chat-composer");
		this.host.tui.requestRender();
	}

	/** Esc from App's key router: dismiss the overlay through its own funnel. */
	dismissQuickOpen(): void {
		this.quickOpenPaneValue?.dismiss();
	}

	/** Current Quick Open view (read-only; tests and status surfaces). */
	quickOpenView(): QuickOpenView | undefined {
		return this.quickOpenPaneValue?.getView();
	}

	/** Ranking call for Quick Open, bound to the open workspace identity. */
	workspaceQuickOpen(query: string): Promise<WorkspaceQuickOpenResult> {
		const integration = this.host.integration();
		const identity = integration?.controller.identity;
		if (!integration || !identity) return Promise.reject(new Error("workspace is not open"));
		return this.host.request<WorkspaceQuickOpenResult>(
			"workspace.quickOpen",
			{ workspaceId: identity.workspaceId, epoch: identity.epoch, query, limit: 50 },
			120_000,
		);
	}
}
