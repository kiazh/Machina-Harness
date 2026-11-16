// Integrated workspace surface: owns the controller/integration wiring, the
// open/restore/switch/close lifecycle fence, workspace events, and the shell
// geometry sync. App remains the composition root and supplies the UI seams
// (focus router, completion, document commands, notices) through this host.

import type { WorkspaceAttachmentHost } from "../features/workspace.ts";
import type { NoticeKind, WorkspaceHost } from "../features/types.ts";
import { copyText as copyClipboardText } from "../clipboard.ts";
import { clipboardText } from "../images.ts";
import { EditorPane, uriDisplayPath } from "../components/EditorPane.ts";
import { ProblemsPane } from "../components/ProblemsPane.ts";
import { ExplorerModel, ExplorerPane } from "../editor/explorer.ts";
import { ProblemsModel, type IntelligenceRequest, type ProblemRow } from "../editor/intelligence.ts";
import { EditorStore } from "../editor/model.ts";
import { TabModel } from "../editor/tabs.ts";
import { EditorView } from "../editor/view.ts";
import { type AppShell } from "../components/AppShell.ts";
import { type ConfigGetResult, RpcError } from "../protocol.ts";
import { WorkspaceController, type WorkspacePersistenceLike } from "./controller.ts";
import { WorkspaceGatewayAdapter, type WorkspaceRequestFn } from "./gateway.ts";
import { WorkspacePersistence } from "./persistence.ts";
import { computeLayout, explorerTreeRowBudget } from "./layout.ts";
import type { FocusRegion, LayoutInput, LayoutResult, WorkspacePersistedState } from "./state.ts";
import type { WorkspaceEvent } from "./protocol.ts";

/** Integrated workspace wiring: the controller plus the retained views it drives. */
export interface WorkspaceIntegration {
	gateway: WorkspaceGatewayAdapter;
	controller: WorkspaceController;
	store: EditorStore;
	tabs: TabModel;
	view: EditorView;
	pane: EditorPane;
	explorerModel: ExplorerModel;
	explorerPane: ExplorerPane;
	problemsModel: ProblemsModel;
	problemsPane: ProblemsPane;
}

/**
 * UI seams the workspace surface needs from App. Every method is supplied by
 * the composition root; the surface never reaches into App internals.
 */
export interface WorkspaceSurfaceHost {
	request<T>(method: string, params: Record<string, unknown>, timeoutMs: number): Promise<T>;
	requestRender(): void;
	terminalSize(): { columns: number; rows: number };
	/** Append one sanitized notice (transcript/notice surfaces). */
	notify(kind: NoticeKind, text: string): void;
	/** User-facing diagnostics from app-owned entry points (explorer gate). */
	print(text: string, kind: NoticeKind): void;
	readonly shell: AppShell;
	setWorkspaceHints(hints: string): void;
	setFocusForRegion(region: FocusRegion): void;
	noteRegionFocus(region: FocusRegion): void;
	focusRegions(): FocusRegion[];
	activeRegion(): FocusRegion;
	reconcileFocus(): void;
	completionActive(): boolean;
	clearCompletion(): void;
	scheduleAutomaticCompletion(): void;
	openProblems(): void;
	pauseEditorCursorBlink(): void;
	isCursorBlockVisible(): boolean;
	syncCursorBlink(): void;
	refreshProblemsForActiveDocument(): Promise<void>;
	refreshLspStatus(): void;
	lspStatusText(): string | undefined;
	closeWorkspaceTab(documentId: string): Promise<void>;
	saveActiveDocument(): Promise<void>;
	openWorkspacePath(path: string, options?: { preview?: boolean; focus?: boolean }): Promise<void>;
	openProblem(row: ProblemRow): Promise<void>;
	closeProblems(): void;
	openQuickOpen(): Promise<void>;
	startCompletion(): Promise<void>;
	attachSelectionToDraft(): { ok: boolean; notice: string };
	intelligenceRequest(): IntelligenceRequest;
	closeActiveWorkspaceTab(): void;
	loadUiPreferences(): void;
	applyUiPreferences(): void;
	isExiting(): boolean;
}

/** Layout-intent + terminal identity for shell re-sync (cheap change key). */
export function workspaceShellKey(input: LayoutInput, columns: number, rows: number, chromeRows: number): string {
	return [
		input.requestedMode,
		input.splitRatio,
		input.explorerRequested ? 1 : 0,
		input.preferredPane,
		input.chatSide ?? "left",
		columns,
		rows,
		chromeRows,
	].join("|");
}

/** Workspace hint shown while a workspace identity is live. */
const WORKSPACE_FOOTER_HINTS = "Ctrl+T explorer · Ctrl+N then T/C/E/S panes";

/** Basename for notices (scheme-stripped, percent-decoded; never the raw URI). */
export function displayUriName(uri: string): string {
	const path = uriDisplayPath(uri);
	const parts = path.split("/").filter((segment) => segment.length > 0);
	return parts[parts.length - 1] ?? path;
}

/** True when an untrusted `workspace.open` payload carries a usable binding. */
function isWorkspaceIdentity(value: unknown): boolean {
	if (typeof value !== "object" || value === null) return false;
	const identity = value as { workspaceId?: unknown; epoch?: unknown; resolvedRoot?: unknown };
	return (
		typeof identity.workspaceId === "string" &&
		identity.workspaceId.length > 0 &&
		typeof identity.epoch === "number" &&
		Number.isFinite(identity.epoch) &&
		typeof identity.resolvedRoot === "string" &&
		identity.resolvedRoot.length > 0
	);
}

/**
 * One cohesive owner for the integrated workspace. Constructed at start() only
 * when the feature flag and a launch root exist; absent forever otherwise, so
 * chat-only behavior stays byte-identical.
 */
export class WorkspaceSurface {
	/** Mutable until start() finishes wiring the controller. */
	integration: WorkspaceIntegration | undefined;
	/** Latest computed workspace geometry (drives focus regions + pane rows). */
	layout: LayoutResult | undefined;
	/** True once a successful open pushed geometry to the shell. */
	layoutActive = false;
	/** The backend answered METHOD_NOT_FOUND for workspace.* exactly once. */
	unsupported = false;

	private host: WorkspaceSurfaceHost;
	private surface: (WorkspaceHost & WorkspaceAttachmentHost) | undefined;
	private lastShellKey = "";
	private chromeTimer: ReturnType<typeof setTimeout> | undefined;
	private workspacePreOpenRaw: string | undefined;
	private workspacePreOpenCaptured = false;
	/**
	 * Contract C3 / MCH-012: ONE authoritative workspace lifecycle fence.
	 * `generation` is the app-side epoch; every lifecycle entry point (open,
	 * root switch, close, exit, gateway exit) bumps it, so a continuation that
	 * captured the previous epoch publishes nothing. `fenceController` is the
	 * controller generation that epoch is bound to (`undefined` until a
	 * mounted controller is captured). A fence is current only while BOTH
	 * sources agree: see `isCurrent`.
	 */
	private generation = 0;
	private fenceController: number | undefined;

	constructor(host: WorkspaceSurfaceHost) {
		this.host = host;
	}

	/** The feature-host surface; available only once start() wired it. */
	get workspaceHost(): WorkspaceHost | undefined {
		return this.surface;
	}

	/**
	 * Construct the integration and open the workspace in the background.
	 * Called once from start() after initialize + session setup. Nothing here
	 * touches the shell until the open succeeds, so a failure leaves chat-only
	 * behavior byte-identical.
	 */
	start(root: string): void {
		const request: WorkspaceRequestFn = <T>(method: string, params: Record<string, unknown>) => {
			if (this.unsupported) {
				return Promise.reject(new Error("workspace is unavailable on this backend"));
			}
			return this.host.request<T>(method, params, 120_000).then((result) => {
				// Fail closed on a malformed open identity (a backend that does
				// not really serve workspace.* answers `{}`): binding a bogus
				// workspace would half-open the shell. One quiet disable notice.
				if (method === "workspace.open" && !isWorkspaceIdentity(result)) {
					this.noteUnsupported();
					throw new Error("workspace.open returned an invalid identity");
				}
				return result;
			}).catch((error: unknown) => {
				// A backend without workspace.* fails every call closed with
				// METHOD_NOT_FOUND: disable the feature once, quietly, and let
				// the error surface normally for this call.
				if (error instanceof RpcError && error.code === -32601) this.noteUnsupported();
				throw error;
			});
		};
		const gateway = new WorkspaceGatewayAdapter(request);
		const store = new EditorStore();
		const tabs = new TabModel({ isDirty: (documentId) => store.getDocument(documentId)?.dirty ?? false });
		const view = new EditorView({
			store,
			view: { documentId: "", cursorOffset: 0, scrollLine: 0, horizontalCellOffset: 0 },
			onChange: (documentId) => {
				this.host.pauseEditorCursorBlink();
				this.integration?.controller.noteEdit(documentId);
				this.host.clearCompletion();
				this.host.scheduleAutomaticCompletion();
				this.host.requestRender();
			},
			requestRender: () => this.host.requestRender(),
			onCursorMove: () => { if (this.host.completionActive()) this.host.clearCompletion(); },
			// Editor caret shares the app's single blink phase (coordinator).
			isCursorBlockVisible: () => this.host.isCursorBlockVisible(),
			// The view's clipboard seam: copy through the real system clipboard
			// (clipboard.ts), paste through the text reader (images.ts, "" when
			// empty or unavailable → null so the view never inserts "").
			clipboard: {
				copy: (text) => copyClipboardText(text),
				paste: async () => {
					try {
						const text = await clipboardText(process.platform);
						return text.length > 0 ? text : null;
					} catch {
						return null;
					}
				},
			},
		});
		view.onFocusRequested = () => this.host.noteRegionFocus("editor");
		const pane = new EditorPane({
			store,
			tabModel: tabs,
			view,
			requestRender: () => this.host.requestRender(),
			onSelectTab: () => {
				this.host.clearCompletion();
				// Tab focus refreshes the Problems projection for the new active file.
				void this.host.refreshProblemsForActiveDocument();
				this.host.refreshLspStatus();
				this.host.requestRender();
			},
			onCloseTab: (documentId) => void this.host.closeWorkspaceTab(documentId),
			// Fallback when the pane owns the key directly; the app's Ctrl+S
			// handler runs first while the editor region is active.
			onSave: () => void this.host.saveActiveDocument(),
		});
		const controller = new WorkspaceController({
			gateway,
			persistence: this.workspacePersistence(),
			store,
			tabs,
			requestRender: () => this.host.requestRender(),
			notify: (kind, text) => {
				// One quiet disable notice: the controller's own error for the
				// METHOD_NOT_FOUND open is suppressed (the info notice covers it),
				// and after that all controller errors stem from the same cause.
				if (this.unsupported && kind === "error") return;
				this.host.notify(kind, text);
			},
			focus: (region) => this.host.setFocusForRegion(region),
			onDocumentReleased: (documentId) => view.releaseDocument(documentId),
		});
		const explorerModel = new ExplorerModel((path, cursor, listOptions) =>
			gateway.list(path, cursor, listOptions),
		);
		const explorerPane = new ExplorerPane(explorerModel, { onChange: () => this.host.requestRender() });
		explorerPane.onFocusRequested = () => this.host.noteRegionFocus("explorer");
		explorerPane.onOpenFile = (entry) => {
			if (entry.kind === "file") void this.host.openWorkspacePath(entry.path, { preview: false });
		};
		explorerPane.onPreviewFile = (entry, focus = true) => {
			if (entry.kind === "file") void this.host.openWorkspacePath(entry.path, { preview: true, focus });
		};
		const problemsModel = new ProblemsModel({ request: this.host.intelligenceRequest() });
		const problemsPane = new ProblemsPane({
			model: problemsModel,
			requestRender: () => this.host.requestRender(),
			onOpen: (row) => void this.host.openProblem(row),
			onClose: () => this.host.closeProblems(),
		});
		problemsPane.onFocusRequested = () => this.host.noteRegionFocus("problems");

		this.integration = {
			gateway,
			controller,
			store,
			tabs,
			view,
			pane,
			explorerModel,
			explorerPane,
			problemsModel,
			problemsPane,
		};
		this.surface = {
			controller,
			controllerAvailable: () => !this.unsupported && this.integration !== undefined,
			setMode: (mode) => {
				controller.setMode(mode);
				this.syncShell();
			},
			toggleChatMaximize: () => {
				controller.toggleChatMaximize();
				this.syncShell();
			},
			toggleEditorMaximize: () => {
				controller.toggleEditorMaximize();
				this.syncShell();
			},
			restoreSplit: () => {
				controller.restoreSplit();
				this.syncShell();
			},
			setExplorer: (open) => this.setExplorerRequested(open),
			closeActiveTab: () => this.host.closeActiveWorkspaceTab(),
			setChatSide: (side) => {
				controller.setChatSide(side);
				this.syncShell();
			},
			layoutChanged: () => this.syncShell(),
			openProblems: () => this.host.openProblems(),
			openQuickOpen: () => void this.host.openQuickOpen(),
			completeAtCursor: () => void this.host.startCompletion(),
			openRoot: (path) => this.switchRoot(path),
			// T10 explicit sharing: capture the editor selection into the
			// composer draft (plan §20). Never called by open/view paths.
			attachSelectionToDraft: () => this.host.attachSelectionToDraft(),
			dirtyBuffers: () => {
				const dirty = store.documents().filter((document) => document.dirty);
				return {
					count: dirty.length,
					names: dirty.slice(0, 8).map((document) => displayUriName(document.uri)),
				};
			},
		};
		this.host.shell.setWorkspaceEditor(pane);
		this.host.shell.setWorkspaceExplorer(explorerPane);
		this.host.shell.onWorkspaceRatioChange = (ratio) => {
			controller.setRatio(ratio);
			this.syncShell();
		};
		// Far-right grip: flips the same explorer request the Ctrl+T / /explorer
		// path uses, so the listing load and persistence stay single-sourced.
		this.host.shell.onWorkspaceExplorerToggle = () => {
			const explorer = this.integration?.controller.getLayoutInput().explorerRequested ?? false;
			this.setExplorerRequested(!explorer);
		};
		// Global editor preferences (ui_preferences): best-effort, non-blocking.
		this.host.loadUiPreferences();
		void this.openBackground(root);
	}

	// ─── lifecycle fence (contract C3 / MCH-012) ───────────────────────────

	/**
	 * Enter a new app-side lifecycle epoch. Call this BEFORE the first await
	 * of any open/switch/close/exit entry point so parked continuations of the
	 * previous epoch drop. The controller generation is re-bound after the
	 * entry's own controller call returns (`armFence`), because that call
	 * legitimately advances it.
	 */
	beginFence(): number {
		this.host.clearCompletion();
		this.fenceController = undefined;
		return ++this.generation;
	}

	/**
	 * Capture the live fence before an await. When a controller is mounted its
	 * generation is the authoritative half; during early startup/tests without
	 * a controller the app epoch is the only source.
	 */
	captureFence(): number {
		this.fenceController = this.integration?.controller.generation;
		return this.generation;
	}

	/**
	 * Bind `gen` to the controller generation that exists now. Guarded so a
	 * latched continuation can never rebind (or adopt) a retired/newer epoch.
	 */
	armFence(gen: number): void {
		if (gen !== this.generation || this.host.isExiting()) return;
		this.fenceController = this.integration?.controller.generation;
	}

	/**
	 * True only while the captured epoch is still the live one AND (once a
	 * controller was captured) that controller still reports the captured
	 * generation current. A close/switch/dispose in EITHER source therefore
	 * denies the continuation, which must publish nothing.
	 */
	isCurrent(gen: number): boolean {
		if (this.host.isExiting() || gen !== this.generation) return false;
		const integration = this.integration;
		if (this.fenceController === undefined) return true;
		return integration !== undefined && integration.controller.isCurrent(this.fenceController);
	}

	get currentGeneration(): number {
		return this.generation;
	}

	// ─── open / restore / switch / close ────────────────────────────────────

	/**
	 * Background open. Failures notify once via the controller and leave the
	 * shell chat-only. On success the restored layout (or the default split)
	 * reaches the shell, remembered files reopen only when a stored state
	 * existed, cursors are re-applied, and the LSP/Problems feeds start.
	 */
	private async openBackground(root: string): Promise<void> {
		const integration = this.integration;
		if (!integration) return;
		// MCH-012: bump BEFORE the first await so a superseding entry point
		// (switch/close/exit) parked mid-flight drops this whole continuation.
		const gen = this.beginFence();
		// Capture the stored document BEFORE openWorkspace persists its own
		// intent over it (see workspacePersistence).
		// MCH-012: the snapshot itself is published state — a read that lands
		// after a superseding entry must not overwrite the live path's capture.
		let raw: string | undefined;
		try {
			raw = await this.readStateRaw();
		} catch {
			raw = undefined;
		}
		if (!this.isCurrent(gen)) return;
		this.workspacePreOpenRaw = raw;
		this.workspacePreOpenCaptured = false;
		const identity = await integration.controller.openWorkspace(root);
		if (!identity) return;
		// C3: this open legitimately advanced the controller generation; bind
		// the epoch to what it produced before the combined (both-source) check.
		this.armFence(gen);
		if (!this.isCurrent(gen)) return;
		this.layoutActive = true;
		// Preferences are view-global; re-assert them once the editor exists so
		// a prefs load racing the background open can never be lost.
		this.host.applyUiPreferences();
		const restored = await integration.controller.restore(identity.resolvedRoot);
		// MCH-012: restore can be slow; applying its layout/state after a
		// supersede would publish a stale root into the live shell.
		if (!this.isCurrent(gen)) return;
		// The controller's default intent is already the split; only a stored
		// state overrides it (and an explicit command issued during the open
		// window is never clobbered).
		if (restored) integration.controller.applyRestoredLayout(restored);
		this.syncShell();
		if (restored) {
			await integration.controller.reopenRecent(restored);
			if (!this.isCurrent(gen)) return;
			this.restoreCursorState(restored);
			const active = integration.tabs.activeDocumentId();
			if (active) integration.view.setDocument(active);
		}
		if (integration.controller.getLayoutInput().explorerRequested) {
			void integration.explorerModel.load();
		}
		integration.controller.setLspStatusListener(() => {
			integration.pane.setLspStatus(this.host.lspStatusText());
		});
		void integration.controller.lspStatus().then(() => {
			// MCH-012: a late status is only applied while this open is still
			// the live one — a superseded root never writes the pane.
			if (this.integration === integration && this.isCurrent(gen)) {
				integration.pane.setLspStatus(this.host.lspStatusText());
			}
		});
		void this.host.refreshProblemsForActiveDocument();
		this.syncShell();
	}

	/**
	 * Explicit user command to reopen the workspace at another folder
	 * (`/workspace open <path>`). Clean documents from the previous root close
	 * because the new workspace cannot save them (their URIs fall outside its
	 * root); dirty buffers always stay, with the count reported. The new
	 * root's persisted layout/files restore when a stored state exists.
	 */
	async switchRoot(path: string): Promise<{ ok: boolean; message: string }> {
		const integration = this.integration;
		if (!integration) return { ok: false, message: "Workspace is not available." };
		const trimmed = path.trim();
		if (!trimmed) return { ok: false, message: "Usage: /workspace open <path>" };
		// MCH-012: this switch is now the live lifecycle entry — bump BEFORE the
		// first await so any in-flight open/switch continuation drops.
		const gen = this.beginFence();
		// Capture the stored document BEFORE open persists its intent (same
		// one-shot snapshot rule as the boot path).
		let raw: string | undefined;
		try {
			raw = await this.readStateRaw();
		} catch {
			raw = undefined;
		}
		if (!this.isCurrent(gen)) return { ok: false, message: "workspace switched" };
		this.workspacePreOpenRaw = raw;
		this.workspacePreOpenCaptured = false;
		const identity = await integration.controller.openWorkspace(trimmed);
		if (!identity) {
			return { ok: false, message: "Could not open that folder (see the notice above)." };
		}
		// C3: bind the epoch to the generation this open produced, then check
		// both sources (a concurrent close/switch/dispose still denies here).
		this.armFence(gen);
		if (!this.isCurrent(gen)) {
			return { ok: true, message: "workspace switched" };
		}
		this.layoutActive = true;
		// The explorer tree belongs to the old root; root reload alone kept
		// expanded child records from A visible under B (combined #28).
		integration.explorerModel.reset();
		let closed = 0;
		let kept = 0;
		for (const document of integration.store.documents()) {
			if (document.dirty) {
				kept += 1;
				continue;
			}
			const outcome = await integration.controller.closeTab(document.id);
			if (!this.isCurrent(gen)) return { ok: true, message: "workspace switched" };
			if (outcome.status === "closed") closed += 1;
			else kept += 1;
		}
		const restored = await integration.controller.restore(identity.resolvedRoot);
		// MCH-012: never apply a restored layout to a root that has since lost
		// the lifecycle race.
		if (!this.isCurrent(gen)) return { ok: true, message: "workspace switched" };
		if (restored) integration.controller.applyRestoredLayout(restored);
		this.syncShell();
		if (restored) {
			await integration.controller.reopenRecent(restored);
			if (!this.isCurrent(gen)) return { ok: true, message: "workspace switched" };
			this.restoreCursorState(restored);
			const active = integration.tabs.activeDocumentId();
			if (active) integration.view.setDocument(active);
		}
		await integration.explorerModel.reload("");
		// MCH-012: the reload preceded the final shell publish; drop it when
		// another lifecycle entry took over while the read was parked.
		if (!this.isCurrent(gen)) return { ok: true, message: "workspace switched" };
		if (integration.controller.getLayoutInput().explorerRequested) void integration.explorerModel.load();
		void integration.controller.lspStatus();
		void this.host.refreshProblemsForActiveDocument();
		this.syncShell();
		const extras: string[] = [];
		if (closed > 0) extras.push(`closed ${closed} clean tab(s)`);
		if (kept > 0) extras.push(`kept ${kept} unsaved tab(s)`);
		const suffix = extras.length > 0 ? ` · ${extras.join(" · ")}` : "";
		return { ok: true, message: `workspace: ${identity.rootLabel} (${identity.resolvedRoot})${suffix}` };
	}

	/**
	 * Close the live workspace (`/settings` path). The close supersedes any
	 * in-flight open/switch, and its own shell publish must not land on a
	 * newer workspace.
	 */
	async close(): Promise<void> {
		const integration = this.integration;
		if (!integration) return;
		const gen = this.beginFence();
		await integration.controller.closeWorkspace();
		// C3: the close advanced the controller generation; rebind this
		// epoch before publishing the shell state.
		this.armFence(gen);
		if (!this.isCurrent(gen)) return;
		this.syncShell();
	}

	/**
	 * Persistence bridge over `config.get`/`config.set("workspace_ui_state")`
	 * (plan §21). A one-shot snapshot captured before open serves the FIRST
	 * load: the controller persists its default intent during openWorkspace,
	 * which would otherwise overwrite the stored entry before `restore` reads
	 * it (verified by a first-run probe). The snapshot is parsed through
	 * WorkspacePersistence, so sanitization stays single-sourced.
	 */
	private workspacePersistence(): WorkspacePersistenceLike {
		const live = new WorkspacePersistence({
			get: () => this.readStateRaw(),
			set: async (value) => {
				await this.host.request("config.set", { key: "workspace_ui_state", value, persist: true }, 30_000);
			},
		});
		return {
			load: async (rootKey) => {
				if (!this.workspacePreOpenCaptured) {
					this.workspacePreOpenCaptured = true;
					const raw = this.workspacePreOpenRaw;
					if (raw !== undefined) {
						const snapshot = new WorkspacePersistence({ get: async () => raw, set: async () => {} });
						const parsed = await snapshot.load(rootKey);
						if (parsed) return parsed.state;
					}
				}
				return (await live.load(rootKey))?.state;
			},
			save: (rootKey, state) => live.save(rootKey, state),
		};
	}

	/** Read the stored workspace document ("" and failures mean "nothing"). */
	private async readStateRaw(): Promise<string | undefined> {
		const { config } = await this.host.request<ConfigGetResult>("config.get", {}, 30_000);
		const raw = config["workspace_ui_state"];
		return typeof raw === "string" && raw.length > 0 ? raw : undefined;
	}

	/** Apply remembered cursor/scroll positions after a successful reopen. */
	private restoreCursorState(state: WorkspacePersistedState): void {
		const integration = this.integration;
		if (!integration || !state.cursorByUri) return;
		for (const [uri, cursor] of Object.entries(state.cursorByUri)) {
			const document = integration.store.documents().find((candidate) => candidate.uri === uri);
			if (!document) continue;
			const offset = Math.max(0, Math.min(cursor.offset, document.text.length));
			const anchor =
				cursor.anchorOffset === undefined
					? undefined
					: Math.max(0, Math.min(cursor.anchorOffset, document.text.length));
			integration.store.setSelection(document.id, {
				cursorOffset: offset,
				...(anchor !== undefined ? { anchorOffset: anchor } : {}),
			});
			if (document.id === integration.tabs.activeDocumentId()) {
				integration.view.setView({
					documentId: document.id,
					cursorOffset: offset,
					...(anchor !== undefined ? { selectionAnchorOffset: anchor } : {}),
					scrollLine: cursor.scrollLine ?? 0,
					horizontalCellOffset: 0,
				});
			}
		}
	}

	// ─── shell geometry ─────────────────────────────────────────────────────

	/**
	 * Push current layout intent to the shell and size the editor pane. The
	 * split geometry itself always comes from computeLayout (single source);
	 * only the shell's dock/composer/footer chrome (+ density gaps) is
	 * subtracted before handing rows to the pane, so over-allocation can never
	 * clip its status row.
	 */
	syncShell(): void {
		const integration = this.integration;
		if (!integration || !this.layoutActive) {
			// No pane is composed: keys must belong to the composer, never to a
			// component retained from a previous workspace/layout.
			this.host.reconcileFocus();
			return;
		}
		const input = integration.controller.getLayoutInput();
		const { columns, rows } = this.host.terminalSize();
		this.host.shell.setWorkspaceState(input);
		// Breadcrumb root (finding 2): pushed here so open/switch/close all
		// converge on the same single call site.
		const root = integration.controller.identity?.resolvedRoot;
		integration.pane.setWorkspaceRoot(root);
		integration.problemsPane.setWorkspaceRoot(root);
		// Footer workspace hints stay only while a workspace identity exists.
		this.host.setWorkspaceHints(integration.controller.identity ? WORKSPACE_FOOTER_HINTS : "");
		const layout = computeLayout({ ...input, columns, rows });
		this.layout = layout;
		// Composer + optional approval dock + footer are full-width rows under
		// the panes row; chromeRows sums their heights and gaps from the same
		// budgets the root VStack uses.
		const chrome = this.host.shell.chromeRows();
		this.lastShellKey = workspaceShellKey(input, columns, rows, chrome);
		if (layout.editor) integration.pane.layout(Math.max(1, layout.editor.rows - chrome));
		if (layout.explorer) {
			// The docked tree owns its own windowing inside a bounded panel:
			// the drawer gets the rows the panes area can spare, capped so a
			// long tree never paints a full-page surface rectangle. Keep the
			// selection visible inside that budget.
			integration.explorerPane.setRowBudget(explorerTreeRowBudget(layout.explorer.rows - chrome - 2));
		}
		// Focus reconciliation (finding 1): the recomposed geometry may have
		// hidden the region that owns focus (e.g. chat maximized while the
		// editor was active). pi-tui would keep delivering keys to the now
		// unmounted pane — typing silently editing an invisible buffer — so
		// retarget the composer, the only always-visible region. The nested
		// sync from setFocusForRegion terminates immediately: the composer is
		// always in focusRegions(), so this never re-enters the branch.
		if (!this.host.focusRegions().includes(this.host.activeRegion())) {
			this.host.setFocusForRegion("chat-composer");
		}
		// Re-assert the TUI's real focus against the composed geometry: pi-tui
		// keeps delivering keys to a removed component, so a pane that was
		// unmounted after the check above (or a focus cleared by an overlay)
		// must be healed before the next keystroke, not on a focus shortcut.
		this.host.reconcileFocus();
		this.host.requestRender();
	}

	/** Cheap re-sync: only when the intent or terminal dimensions changed. */
	syncShellIfChanged(): void {
		const integration = this.integration;
		if (!integration || !this.layoutActive) return;
		const input = integration.controller.getLayoutInput();
		const { columns, rows } = this.host.terminalSize();
		const key = workspaceShellKey(input, columns, rows, this.host.shell.chromeRows());
		if (key === this.lastShellKey) return;
		this.syncShell();
	}

	/**
	 * Reconcile pane row allocation after the composer settles. The slash
	 * autocomplete grows the composer during the key dispatch; scheduling this
	 * as a task (not a microtask) lets the focused Editor finish its render
	 * state before the shell measures the chrome.
	 */
	scheduleChromeSync(): void {
		if (!this.integration || !this.layoutActive || this.host.isExiting()) return;
		if (this.chromeTimer !== undefined) return;
		this.chromeTimer = setTimeout(() => {
			this.chromeTimer = undefined;
			if (!this.host.isExiting()) this.syncShellIfChanged();
		}, 0);
	}

	/** Clear the deferred chrome sync (exit / gateway exit). */
	disposeTimer(): void {
		if (this.chromeTimer !== undefined) {
			clearTimeout(this.chromeTimer);
			this.chromeTimer = undefined;
		}
	}

	// ─── explorer / events ──────────────────────────────────────────────────

	/** Toggle/open/close the explorer column; loads the listing on open. */
	setExplorerRequested(open: boolean): void {
		const integration = this.integration;
		if (!integration) return;
		const current = integration.controller.getLayoutInput().explorerRequested;
		if (open && !current) {
			// Overlay-in-editor composition is not implemented. Refusing the
			// toggle is the lower-risk honest behavior: never persist an intent
			// that would paint no drawer and never silently steal focus.
			const input = integration.controller.getLayoutInput();
			const { columns, rows } = this.host.terminalSize();
			const probe = computeLayout({ ...input, columns, rows, explorerRequested: true });
			if (!probe.explorer) {
				if (input.requestedMode !== "split") {
					this.host.print("Explorer is only available in the split view; restore the split first.", "warning");
				} else {
					this.host.print("Explorer needs a wider terminal to dock; widen the window and try again.", "warning");
				}
				return;
			}
		}
		if (current !== open) integration.controller.toggleExplorer();
		if (open) void integration.explorerModel.load();
		this.syncShell();
	}

	/** METHOD_NOT_FOUND once: quiet disable, one info notice, no retry spam. */
	noteUnsupported(): void {
		if (this.unsupported) return;
		this.unsupported = true;
		this.host.setWorkspaceHints("");
		this.host.notify("info", "workspace commands unavailable on this backend");
	}

	/**
	 * Workspace notifications (plan §19): a family separate from chat, never
	 * entering turn bookkeeping. The controller identity/epoch-gates them;
	 * diagnostics additionally feed the Problems projection.
	 */
	onEvent(event: WorkspaceEvent): void {
		if (this.host.isExiting()) return;
		const integration = this.integration;
		if (!integration) return;
		integration.controller.handleEvent(event);
		if (event.workspaceId !== integration.controller.identity?.workspaceId
			|| event.epoch !== integration.controller.identity?.epoch) return;
		if (event.type === "workspace.lspState" && event.state === "ready") this.host.scheduleAutomaticCompletion();
		if (event.type === "workspace.diagnostics") {
			integration.problemsModel.push({
				uri: event.uri,
				version: event.version,
				source: event.source,
				diagnostics: event.diagnostics,
			});
		}
		this.syncShellIfChanged();
		this.host.requestRender();
	}
}
