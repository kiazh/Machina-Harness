// The interactive app: transcript, editor and footer on a pi-tui alt screen,
// driven by the Python gateway. Slash commands live in features.ts.

import { mkdir, writeFile } from "node:fs/promises";
import { userInfo } from "node:os";
import { dirname, resolve } from "node:path";
import {
	Container,
	type Component,
	type Editor,
	type OverlayOptions,
	type OverlayHandle,
	type SelectItem,
	truncateToWidth,
	type TUI,
} from "@earendil-works/pi-tui";
import { ArgumentAwareAutocompleteProvider } from "./autocomplete.ts";
import { parseCommand } from "./commands.ts";
import { ComposerBox, Footer, Picker, SecretForm, TextForm, ThinkingIndicator } from "./components.ts";
import { NavBar, type NavSnapshot, type NavView } from "./components/NavBar.ts";
import { DEFAULT_MENU_VISIBLE_ROWS, Menu } from "./components/Menu.ts";
import { OAuthProgress } from "./components/OAuthProgress.ts";
import { AppShell } from "./components/AppShell.ts";
import { AgentTabBar } from "./components/AgentTabBar.ts";
import { ActivityBar, ActivityStore } from "./activity.ts";
import { StatusPage } from "./components/StatusPage.ts";
import { ChangesPage } from "./components/ChangesPage.ts";
import { OutputStore, OutputViewer, type OutputRecord } from "./components/OutputViewer.ts";
import { AnimationCoordinator, CURSOR_BLINK_INTERVAL_MS, CURSOR_BLINK_RESUME_DELAY_MS, reducedMotionFromEnv } from "./design/motion.ts";
import type { AnimationSubscription } from "./design/motion.ts";
import { AppKeybindings, effectiveShortcutRows, type BindingOverrides } from "./keybindings.ts";
import { KeybindingsDialog } from "./components/KeybindingsDialog.ts";
import { KeysDialog, type KeysDialogChoice } from "./components/KeysDialog.ts";
import { loadKeyRows, redactSecret } from "./features/keys.ts";
import { addProvider, removeProvider } from "./features/settings.ts";
import { NowPlayingBar } from "./components/NowPlayingBar.ts";
import { sanitizePastedText } from "./design/text.ts";
import { describeError, redactParamsForLog, uiLog } from "./debug.ts";
import type { MusicHost } from "./features/types.ts";
import { type FamilySession, type FeatureHost, type NoticeKind, runCommand, SLASH_COMMANDS, type WorkspaceHost } from "./features/index.ts";
import { type AskTextOptions, PROFILE_ID_RE, type OAuthProgressHandle, type OAuthProgressOptions, type PickOptions, type SkillsDirectoryHost, type UiPreferenceKey, type UiSettingsHost } from "./features/types.ts";
import type { GatewayClient } from "./gateway.ts";
import { copyText as copyClipboardText } from "./clipboard.ts";
import { clipboardImage, clipboardText, ImageDraft, imagePaths, readImageFile, type ImageAttachment } from "./images.ts";
import type {
	ConfigGetResult,
	ConfigResult,
	ContextResult,
	GatewayEvent,
	HistoryEntry,
	KiroFallback,
	ProvidersListResult,
	SessionInfo,
} from "./protocol.ts";
import { sanitizeDisplay } from "./editor/display.ts";
import { TurnSessionStore } from "./turnSessionStore.ts";
import { StatusController } from "./app/status.ts";
import { UsageController } from "./app/usage.ts";
import { ChangesController } from "./app/changes.ts";
import { ApprovalController } from "./app/approvals.ts";
import { SessionController } from "./app/sessions.ts";
import { KeyboardRouter, type KeyboardHost } from "./app/keyboard.ts";
import { CommandController, type CommandHost } from "./app/commands.ts";
import type { ApprovalHost, SessionHost, StatusHost, ChangesHost, ScreenView } from "./app/contracts.ts";
import { EventRouter, type EventHost } from "./app/events.ts";
import { WorkspaceCoordinator, type WorkspaceHost as WorkspaceCoordinatorHost, type WorkspaceSurfaceWiring } from "./app/workspace.ts";
import { AppBootstrap, type BootstrapHost, type WorkspaceReview } from "./app/bootstrap.ts";
export { isKiroQuotaError } from "./app/events.ts";
export { commandCarriesSecret } from "./app/commands.ts";

import { editorTheme, getSkin, setSkin, SKIN_NAMES, skinDescription } from "./theme.ts";
import { Transcript } from "./transcript.ts";
import {
	type ConflictResolution,
	type SaveConflictDescriptor,
} from "./workspace/controller.ts";
import {
	WorkspaceSurface,
	type WorkspaceIntegration,
	type WorkspaceSurfaceHost,
} from "./workspace/surface.ts";
import {
	defaultUiPreferences,
	UiPreferencesPersistence,
	type UiPreferences,
} from "./workspace/preferences.ts";
import type {
	FocusRegion,
	LayoutResult,
} from "./workspace/state.ts";

export interface AppOptions {
	model?: string;
	provider?: string;
	sessionId?: string;
	mode?: string;
	readClipboardImage?: () => Promise<ImageAttachment | null>;
}

/** The default profile (first-touch identity); maps to the shared agent. */
export function defaultProfileId(): string {
	try {
		return userInfo().username || "default";
	} catch {
		return "default";
	}
}

/**
 * Row cap for the keyboard-shortcuts menu. The documented list is long, so it
 * gets a couple more rows than the shared default
 * ({@link DEFAULT_MENU_VISIBLE_ROWS}) while still scrolling within the box.
 */
const SHORTCUTS_VISIBLE_ROWS = 14;

/** One dialog title/row label sink: never render decoded or server text raw. */
function sanitizeSelectItems(items: SelectItem[]): SelectItem[] {
	return items.map((item) => ({
		value: item.value,
		label: sanitizeDisplay(item.label),
		...(item.description === undefined ? {} : { description: sanitizeDisplay(item.description) }),
		// Section rows are chrome, not values: losing these flags would turn
		// every group heading into a selectable row.
		...((item as { section?: unknown }).section === true ? { section: true } : {}),
		...((item as { muted?: unknown }).muted === true ? { muted: true } : {}),
	}));
}

function sanitizeAskTextOptions(options: AskTextOptions): AskTextOptions {
	return {
		...options,
		title: sanitizeDisplay(options.title),
		...(options.description === undefined ? {} : { description: sanitizeDisplay(options.description) }),
		...(options.placeholder === undefined ? {} : { placeholder: sanitizeDisplay(options.placeholder) }),
		...(options.example === undefined ? {} : { example: sanitizeDisplay(options.example) }),
	};
}

export class App implements FeatureHost {
	readonly exited: Promise<void>;

	private readonly tui: TUI;
	private readonly client: GatewayClient;
	private readonly options: AppOptions;
	private readonly transcriptView = new Container();
	private readonly transcript: Transcript;
	private readonly composer: ComposerBox;
	private readonly editor: Editor;
	private readonly keybindings = new AppKeybindings();
	private keybindingSave: Promise<unknown> = Promise.resolve();
	private readonly coordinator: AnimationCoordinator;
	private readonly footer: Footer;
	/**
	 * Codex-style bottom-docked layout: the transcript scrolls in the
	 * flexible area while the composer and footer stay pinned to the
	 * bottom rows. The shell retains the primary ScrollView for life and
	 * recomputes density budgets from actual terminal dimensions.
	 */
	private readonly shell: AppShell;
	private readonly nowPlayingBar: NowPlayingBar;
	/**
	 * The single caret-blink subscription (key `cursor.blink`). Present only
	 * while a text surface owns the caret; its presence is the source of
	 * truth for "blinking is active" so focus changes can never stack loops.
	 */
	private cursorBlinkSub: AnimationSubscription | undefined;
	private cursorBlinkIdleTimer: ReturnType<typeof setTimeout> | undefined;
	/** Wall-clock start of the current blink phase (kept across re-subscribes). */
	private cursorBlinkStartedAt = 0;
	private lastPrompt = "";
	private contextReadVersion = 0;
	private pendingAbortEscAt = 0;
	private pendingAbortEscTimer: ReturnType<typeof setTimeout> | undefined;
	private readonly defaultProfile = defaultProfileId();
	private currentProfile: string = this.defaultProfile;
	private running = false;
	private readonly activity = new ActivityStore();
	private view: "chat" | "status" | "changes" = "chat";
	private current: SessionInfo | undefined;
	private readonly approvals: ApprovalController;
	private readonly sessions: SessionController;
	private readonly status: StatusController;
	private readonly changes: ChangesController;
	private usage?: UsageController;
	private readonly keyboard: KeyboardRouter;
	private readonly commands: CommandController;
	private readonly events: EventRouter;
	private readonly workspaceCoordinator: WorkspaceCoordinator;
	private readonly bootstrap: AppBootstrap;
	private navBar!: NavBar;
	private statusPage!: StatusPage;
	private changesPage!: ChangesPage;
	/** Turn/session bindings, guards, totals, approvals and hidden tails. */
	private readonly turnSession = new TurnSessionStore({
		visibleSessionId: () => this.current?.id,
		isFamilyMember: (sessionId) => this.sessions.isFamilyMember(sessionId),
		onSlowTurn: () => {
			this.transcript.addNotice("Taking longer than expected; waiting for completion.", "warning");
			this.tui.requestRender();
		},
		onEvicted: (sessionId) => this.sessions.pruneSideSessionState(sessionId),
	});
	/** Bounded retained tool outputs for /output inspection (all sessions). */
	private readonly outputStore = new OutputStore();
	private readonly dialogs: Array<{ handle: OverlayHandle; cancel: () => void; hidden: boolean }> = [];
	private exiting = false;
	private logValue: ReturnType<typeof uiLog.scope> | undefined;
	/** Lazy so prototype-based tests and partially built instances stay safe. */
	private get log() {
		return (this.logValue ??= uiLog.scope("app"));
	}
	/**
	 * Feature-level event subscribers (e.g. /login waiting on
	 * auth.chatgpt.*). They observe every frame before turn bookkeeping,
	 * own their own filtering, and never mutate turn state.
	 */
	private readonly featureEventHandlers = new Set<(event: GatewayEvent) => void>();
	private resolveExit: () => void = () => {};
	/**
	 * True while a slash command is doing pre-dialog work (RPCs, secret
	 * prompts). Input must not reach the composer in that window: typing `/`
	 * while `/mcp` loads opens autocomplete underneath the menu that is about
	 * to appear and corrupts both surfaces.
	 */
	private commandRunning = false;
	private commandRunningAt = 0;
    private imageDraft = new ImageDraft();
    private imageLoads = Promise.resolve();
    private imageEpoch = 0;
    private pendingImageLoads = 0;
    private imageLoadFailures = 0;
    private lastPromptImages: ImageAttachment[] = [];
    private lastPromptSessionId: string | undefined;

	// ─── integrated workspace (T3/T6/T12) ───────────────────────────────────
	/** Owns the controller wiring, lifecycle fence, events and shell sync. */
	private workspaceSurface: WorkspaceSurface | undefined;
	private get integration(): WorkspaceIntegration | undefined { return this.workspaceSurface?.integration; }
	private get workspaceHostSurface(): WorkspaceHost | undefined { return this.workspaceSurface?.workspaceHost; }
	private get workspaceUnsupported(): boolean { return this.workspaceSurface?.unsupported ?? false; }
	private get workspaceLayoutActive(): boolean { return this.workspaceSurface?.layoutActive ?? false; }
	private get workspaceLayout(): LayoutResult | undefined { return this.workspaceSurface?.layout; }
	private get workspaceGeneration(): number { return this.workspaceSurface?.currentGeneration ?? 0; }
	get activeRegion(): FocusRegion { return this.workspaceCoordinator.activeRegion; }
	get completionSession() { return this.workspaceCoordinator.completionSession(); }
	get problemsOpen(): boolean { return this.workspaceCoordinator.problemsVisible(); }
	get quickOpenPane(): Component | undefined {
		if (!this.workspaceCoordinator.quickOpenActive()) return undefined;
		const probe = this.tui as TUI & { getFocusedComponent?(): Component | null; focused?: Component | null };
		return (typeof probe.getFocusedComponent === "function" ? probe.getFocusedComponent() : probe.focused) ?? undefined;
	}
	get skillsMenu() { return this.commands.mountedSkillsMenu; }
	/** Global editor preferences (ui_preferences); defaults until loaded. */
	private uiPrefs: UiPreferences = defaultUiPreferences();

	// Delegated runs settle when their work completes or the user cancels.
	private static readonly DELEGATION_TIMEOUT_MS = 0;

	// `/harnesses` wind-down grace: the gateway kills a delegated run's whole
	// process tree at the deadline it was given, then drains and redacts the
	// output; the RPC settles a few seconds later at most. The client clock
	// adds this margin on top of that deadline so a late-but-successful run is
	// never discarded (finding H22).
	private static readonly HARNESS_RUN_GRACE_MS = 15_000;

	constructor(tui: TUI, client: GatewayClient, options: AppOptions = {}) {
		this.tui = tui;
		this.client = client;
		this.options = options;
		this.exited = new Promise((done) => { this.resolveExit = done; });
		// F-23: one shared reader for the environment (the legacy AH_ name is
		// the documented fallback; an explicit MACHINA_ value always wins).
		const reducedMotion = reducedMotionFromEnv(process.env);
		this.coordinator = new AnimationCoordinator(() => this.tui.requestRender(), reducedMotion);
		this.footer = new Footer(this.coordinator);
		this.transcript = new Transcript(this.transcriptView, new ThinkingIndicator(this.coordinator), this.coordinator);
		this.composer = new ComposerBox(tui, editorTheme, { paddingX: 1 });
		this.editor = this.composer.editor;
		// Caret blink: the composer's block obeys the shared phase, and every
		// draft edit re-derives whether a caret is actually on screen (an empty
		// composer shows only the placeholder, so it must not burn renders).
		this.composer.editor.cursorBlockVisible = () => this.cursorBlinkOn();
		this.composer.editor.onChange = () => {
			this.syncCursorBlink();
			// The draft height feeds chromeRows; drop the cached measurement.
			this.shell.invalidateChrome();
			this.scheduleWorkspaceChromeSync();
		};
        this.composer.editor.onPaste = (text) => this.acceptImageDrop(text);
        this.composer.editor.onSubmitBlocked = () => {
            if (!this.pendingImageLoads) return false;
            const epoch = this.imageEpoch;
            const failures = this.imageLoadFailures;
            void this.imageLoads.then(() => {
                if (epoch === this.imageEpoch && failures === this.imageLoadFailures && !this.exiting) {
                    void this.submit(this.editor.getExpandedText());
                }
            });
            return true;
		};
		this.shell = new AppShell(tui, this.transcriptView, this.composer, this.footer);
		this.shell.onViewportResize = () => this.syncWorkspaceShellIfChanged();
		this.workspaceCoordinator = new WorkspaceCoordinator(this.workspaceCoordinatorHost());
		this.approvals = new ApprovalController(this.approvalHost());
		this.sessions = new SessionController(this.sessionHost());
		this.status = new StatusController(this.statusHost());
		this.changes = new ChangesController(this.changesHost());
		this.nowPlayingBar = new NowPlayingBar(this.coordinator);
		this.configureShellSurfaces();
		// App remains the state owner; this adapter exposes only the keyboard seam.
		this.keyboard = new KeyboardRouter(this.keyboardHost());
		this.commands = new CommandController(this.commandHost());
		this.events = new EventRouter(this.eventHost());
		this.editor.setAutocompleteProvider(new ArgumentAwareAutocompleteProvider(SLASH_COMMANDS, process.cwd()));
		this.editor.setAutocompleteMaxVisible(8);
		this.editor.onSubmit = (text) => void this.submit(text);
		this.bootstrap = new AppBootstrap(this.bootstrapHost());
	}

	private configureShellSurfaces(): void {
		this.navBar = new NavBar(
			() => this.navSnapshot(),
			(view) => this.selectNav(view),
			() => this.tui.terminal.rows,
			() => {
				if (!this.dialogs.length && !this.workspaceCoordinator.quickOpenActive()
					&& (this.view !== "chat" || !this.turnSession.approvalDialogCount())) this.openShortcutReference();
			},
		);
		this.shell.setNavBar(this.navBar);
		this.shell.setPinnedBar(this.nowPlayingBar);
		this.shell.setAgentBar(new AgentTabBar(
			() => this.familyRows(),
			() => this.current?.id,
			(sessionId) => { void this.sessions.openFamily(sessionId); },
		));
		this.shell.setActivityBar(new ActivityBar(() => this.activitySummary(), () => { void this.inspectActivity(); }, () => this.commandActions()));
		this.statusPage = new StatusPage(this.tui, () => this.status.snapshot(), (row) => {
			const id = row.id ?? "";
			const sessionId = id.startsWith("agent:")
				? id.slice("agent:".length)
				: id.startsWith("session:")
					? id.slice("session:".length)
					: id.startsWith("harness-job:")
						? (this.status.harnessSessionId(id.slice("harness-job:".length)) ?? "")
						: "";
			if (!sessionId) return false;
			if (sessionId === this.current?.id) {
				this.closeScreen();
				return true;
			}
			void this.sessions.openFamily(sessionId).then(() => {
				if (this.view !== "chat") this.closeScreen();
			});
			return true;
		});
		this.changesPage = new ChangesPage(
			this.tui,
			() => this.changes.snapshot(),
			(turnId) => this.changes.diffState(turnId),
			{
				onOpenDiff: () => this.changes.ensureDiff(true),
				onReview: (turnId) => { void this.changes.startReview(turnId); },
				onReviewModel: (turnId) => { void this.changes.startReview(turnId, "model"); },
				onCancelReview: (turnId) => { void this.changes.cancelReview(turnId); },
				onFixFinding: (turnId, finding) => { void this.changes.fixFindingInChat(turnId, finding); },
				onFixFailure: (turnId, code, reason) => { void this.changes.fixFailureInChat(turnId, code, reason); },
				onClearHandled: (turnId) => this.changes.clearHandledFindings(turnId),
			},
		);
	}

	private workspaceCoordinatorHost(): WorkspaceCoordinatorHost {
		return {
			tui: this.tui,
			composerEditor: this.editor,
			bindings: this.keybindings,
			integration: () => this.integration,
			layout: () => this.workspaceLayout,
			unsupported: () => this.workspaceUnsupported,
			captureFence: () => this.captureWorkspaceFence(),
			isCurrent: (generation) => this.isWorkspaceCurrent(generation),
			isExiting: () => this.exiting,
			overlayActive: () => this.dialogs.length > 0 || this.turnSession.approvalDialogCount() > 0,
			restoreSplit: () => this.workspaceHostSurface?.restoreSplit(),
			syncShellIfChanged: () => this.syncWorkspaceShellIfChanged(),
			syncCursorBlink: () => this.syncCursorBlink(),
			pauseEditorCursorBlink: () => this.pauseEditorCursorBlink(),
			pick: (title, items) => this.pick(title, items),
			print: (text, kind) => this.print(text, kind),
			request: <T>(method: string, params: Record<string, unknown>, timeoutMs: number) =>
				this.client.request<T>(method, params, timeoutMs),
			cursorBlockVisible: () => this.cursorBlinkOn(),
		};
	}

	private bootstrapHost(): BootstrapHost {
		return {
			tui: this.tui,
			client: this.client,
			log: this.log,
			footer: this.footer,
			transcript: this.transcript,
			shell: this.shell,
			coordinator: this.coordinator,
			editor: this.editor,
			nowPlayingBar: this.nowPlayingBar,
			status: this.status,
			turnSession: this.turnSession,
			transcriptView: this.transcriptView,
			composer: this.composer,
			options: this.options,
			defaultProfile: this.defaultProfile,
			onInput: (data) => this.keyboard.handle(data),
			onEvent: (event) => this.events.route(event),
			syncCursorBlink: () => this.syncCursorBlink(),
			stopCursorBlink: () => this.stopCursorBlink(),
			request: <T>(method: string, params?: Record<string, unknown>) => this.request<T>(method, params),
			current: () => this.current,
			switchTo: (session, history) => this.switchTo(session, history),
			showNewChat: () => this.showNewChat(),
			applyKeybindings: (overrides) => this.applyKeybindings(overrides),
			restyle: () => this.restyle(),
			screen: () => this.screen(),
			setFocusForRegion: (region) => this.setFocusForRegion(region),
			startWorkspace: (root) => this.startWorkspace(root),
			beginWorkspaceFence: () => this.beginWorkspaceFence(),
			captureWorkspaceFence: () => this.captureWorkspaceFence(),
			isWorkspaceCurrent: (generation) => this.isWorkspaceCurrent(generation),
			workspaceReview: () => this.workspaceReview(),
			resolveWorkspaceSaveConflict: (conflict) => this.resolveWorkspaceSaveConflict(conflict),
			disposeWorkspaceTimer: () => this.disposeWorkspaceTimer(),
			disposeWorkspaceIntegration: () => this.disposeWorkspaceIntegration(),
			discardCompletion: () => this.discardCompletion(),
			clearPendingAbortEsc: () => this.clearPendingAbortEsc(),
			keyboardDispose: () => this.keyboardDispose(),
			closeQuickOpen: () => this.closeQuickOpen(),
			cancelAllDialogs: () => this.cancelAllDialogs(),
			invalidateApprovals: () => this.invalidateApprovals(),
			setRunning: (running) => this.setRunning(running),
			isExiting: () => this.isExiting(),
			markExiting: () => this.markExiting(),
			resolveExitPromise: () => this.resolveExitPromise(),
			pick: (title, items) => this.pick(title, items),
		};
	}

	private statusHost(): StatusHost {
		return {
			tui: this.tui,
			log: this.log,
			footer: this.footer,
			activity: this.activity,
			turnSession: this.turnSession,
			request: <T>(method: string, params?: Record<string, unknown>) => this.request<T>(method, params),
			current: () => this.current,
			familyRows: () => this.familyRows(),
			screen: () => this.view,
			setScreen: (view) => { this.view = view; },
			setShellPage: (page) => this.shell.setPage(page),
			statusPage: () => this.statusPage,
			changesPage: () => this.changesPage,
			changesCount: () => this.changes.badgeCount(),
			changesError: () => this.changes.errorMessage(),
			setChangesError: (text) => this.changes.setError(text),
			syncChangesSelection: () => this.changesPage.syncSelection(),
			ensureChangesDiff: (retry?: boolean) => this.changes.ensureDiff(retry),
			refreshChanges: () => { void this.changes.refresh(); },
			refreshFamily: () => this.refreshFamily(),
			showApprovalDock: () => this.showApprovalDock(),
			focusChat: () => this.setFocusForRegion("chat-composer"),
			hasVisibleDialog: () => this.dialogs.some(dialog => !dialog.hidden),
			quickOpenActive: () => this.workspaceCoordinator.quickOpenActive(),
			isExiting: () => this.exiting,
			print: (text, kind) => this.print(text, kind),
			pickCentered: (title, items) => this.pickCentered(title, items),
			askText: (options) => this.askText(options),
			commandAction: (sessionId, jobId, action) => this.commandAction(sessionId, jobId, action),
			flushSteering: (sessionId) => this.flushSteering(sessionId),
			openFamily: (sessionId) => this.sessions.openFamily(sessionId),
			openDialog: (create) => this.openDialog(create),
		};
	}

	private changesHost(): ChangesHost {
		return {
			tui: this.tui,
			log: this.log,
			turnSession: this.turnSession,
			request: <T>(method: string, params?: Record<string, unknown>) => this.request<T>(method, params),
			current: () => this.current,
			changesPage: () => this.changesPage,
			screen: () => this.view,
			queueSteering: (text, fromDraft) => this.queueSteering(text, fromDraft),
			submitTurn: (text) => this.submitTurn(text),
			print: (text, kind) => this.print(text, kind),
			closeScreen: () => this.closeScreen(),
			transcriptNotice: (text, kind) => { this.transcript.addNotice(text, kind); },
		};
	}

	private approvalHost(): ApprovalHost {
		return {
			tui: this.tui,
			log: this.log,
			footer: this.footer,
			turnSession: this.turnSession,
			transcript: this.transcript,
			dialogs: this.dialogs,
			current: () => this.current,
			view: () => this.view,
			running: () => this.running,
			isExiting: () => this.exiting,
			request: <T>(method: string, params?: Record<string, unknown>) => this.request<T>(method, params),
			clientRequest: <T>(method: string, params?: Record<string, unknown>) => this.client.request<T>(method, params),
			setDock: (component) => this.shell.setDock(component),
			invalidateChrome: () => this.shell.invalidateChrome(),
			scrollTranscriptToEnd: () => this.shell.transcriptScroll.scrollToEnd(),
			closeQuickOpen: () => this.closeQuickOpen(),
			focusChat: () => this.setFocusForRegion("chat-composer"),
			syncCursorBlink: () => this.syncCursorBlink(),
			pick: (title, items) => this.pick(title, items),
			askText: (options) => this.askText(options),
			print: (text, kind) => this.print(text, kind),
		};
	}

	private sessionHost(): SessionHost {
		return {
			tui: this.tui,
			log: this.log,
			footer: this.footer,
			activity: this.activity,
			outputStore: this.outputStore,
			turnSession: this.turnSession,
			transcript: this.transcript,
			composer: this.editor,
			current: () => this.current,
			setCurrent: (session) => { this.current = session; },
			request: <T>(method: string, params?: Record<string, unknown>) => this.request<T>(method, params),
			clientRequest: <T>(method: string, params?: Record<string, unknown>) => this.client.request<T>(method, params),
			resetChanges: () => this.changes.reset(),
			clearImages: () => this.clearImages(),
			invalidateContextUsage: () => { this.contextReadVersion++; },
			setRunning: (running) => this.setRunning(running),
			refreshContextUsage: (sessionId) => { void this.refreshContextUsage(sessionId); },
			sessionAgent: () => this.sessionAgent(),
			isExiting: () => this.exiting,
			hasDialogs: () => this.dialogs.length > 0,
			pick: (title, items) => this.pick(title, items),
			print: (text, kind) => this.print(text, kind),
			submitTurn: (text, images, steeringId) => this.submitTurn(text, images, steeringId),
			invalidateApprovals: (sessionId) => this.approvals.invalidate(sessionId),
			restoreApprovals: (sessionId) => this.approvals.restore(sessionId),
			rehydrateApprovals: (sessionId) => this.approvals.rehydrate(sessionId),
			flushPendingQuestions: (sessionId) => this.flushPendingQuestions(sessionId),
		};
	}

	private keyboardHost(): KeyboardHost {
		return {
			tui: this.tui,
			editor: this.editor,
			footer: this.footer,
			transcript: this.transcript,
			keybindings: this.keybindings,
			screen: () => this.view,
			activeRegion: () => this.workspaceCoordinator.activeRegion,
			syncShell: () => this.shell.sync(),
			syncWorkspaceShellIfChanged: () => this.syncWorkspaceShellIfChanged(),
			scheduleWorkspaceChromeSync: () => this.scheduleWorkspaceChromeSync(),
			reconcileFocus: () => this.reconcileFocus(),
			statusPage: () => this.statusPage,
			changesPage: () => this.changesPage,
			dialogCount: () => this.dialogs.length,
			hasVisibleDialog: () => this.dialogs.some((dialog) => !dialog.hidden),
			quickOpenActive: () => this.workspaceCoordinator.quickOpenActive(),
			dismissQuickOpen: () => this.workspaceCoordinator.dismissQuickOpen(),
			commandRunning: () => this.commandRunning,
			commandRunningAt: () => this.commandRunningAt,
			approvalDialogCount: () => this.turnSession.approvalDialogCount(),
			isRunning: () => this.running,
			pendingAbortEscAt: () => this.pendingAbortEscAt,
			armPendingAbortEsc: () => this.armPendingAbortEsc(),
			clearPendingAbortEsc: () => this.clearPendingAbortEsc(),
			lastPrompt: () => this.lastPrompt,
			hasPendingImageLoads: () => this.pendingImageLoads > 0,
			cancel: () => this.cancel(),
			exit: () => this.exit(),
			clear: () => this.clear(),
			openStatus: () => this.openStatus(),
			openScreen: (screen) => this.openScreen(screen),
			closeScreen: () => this.closeScreen(),
			selectNav: (view) => this.selectNav(view),
			openShortcutReference: () => this.openShortcutReference(),
			familyCount: () => this.familyRows().length,
			cycleFamily: (direction) => this.cycleFamily(direction),
			closeFamilyCurrent: async () => { await this.sessions.closeCurrentFamily(); },
			openSubagentMenu: () => this.openSubagentMenu(),
			pasteFromClipboard: (imageOnly) => this.pasteFromClipboard(imageOnly),
			clearImages: () => this.clearImages(),
			completionSession: () => this.workspaceCoordinator.completionSession(),
			activeEditorDocumentId: () => this.integration?.tabs.activeDocumentId() ?? undefined,
			acceptCompletion: () => this.workspaceCoordinator.acceptCompletion(),
			clearCompletion: () => this.workspaceCoordinator.clearCompletion(),
			problemsOpen: () => this.workspaceCoordinator.problemsVisible(),
			closeProblems: () => this.workspaceCoordinator.closeProblems(),
			hasWorkspace: () => this.integration !== undefined,
			workspaceReady: () => this.integration?.controller.phase === "ready",
			workspaceLayoutActive: () => this.workspaceLayoutActive,
			workspaceUnsupported: () => this.workspaceUnsupported,
			explorerRequested: () => this.integration?.controller.getLayoutInput().explorerRequested ?? false,
			setExplorerRequested: (requested) => this.workspaceSurface?.setExplorerRequested(requested),
			restoreSplit: () => this.workspaceHostSurface?.restoreSplit(),
			toggleChatMaximize: () => this.workspaceHostSurface?.toggleChatMaximize(),
			toggleEditorMaximize: () => this.workspaceHostSurface?.toggleEditorMaximize(),
			editorHasSelection: () => this.integration?.view.hasSelection() ?? false,
			handleEditorInput: (data) => { this.integration?.view.handleInput(data); },
			openQuickOpen: () => this.workspaceCoordinator.openQuickOpen(),
			closeActiveWorkspaceTab: () => this.workspaceCoordinator.closeActiveWorkspaceTab(),
			cycleFocus: (direction) => this.workspaceCoordinator.cycleFocus(direction),
			attachSelectionToDraft: () => this.workspaceCoordinator.attachSelectionToDraft(),
			startCompletion: () => this.workspaceCoordinator.startCompletion(),
			runDefinitionOrReferences: (operation) => this.workspaceCoordinator.runDefinitionOrReferences(operation),
			runRenamePreview: () => this.workspaceCoordinator.runRenamePreview(),
			saveActiveDocument: () => this.workspaceCoordinator.saveActiveDocument(),
			print: (text, kind) => this.print(text, kind),
		};
	}

	private commandHost(): CommandHost {
		return {
			tui: this.tui,
			editor: this.editor,
			activity: this.activity,
			current: () => this.current,
			request: (method, params) => this.request(method, params),
			print: (text, kind) => this.print(text, kind),
			transcriptNotice: (text, kind) => this.transcript.addNotice(text, kind),
			isExiting: () => this.exiting,
			isTurnRunning: (sessionId) => this.turnSession.isRunning(sessionId),
			isInFlight: (sessionId) => this.turnSession.isInFlight(sessionId),
			visibleInFlight: () => this.turnSession.visibleInFlight(),
			ensureSession: (text) => this.ensureSession(text),
			submitTurn: (text, images) => this.submitTurn(text, images),
			queueSteering: (text, fromDraft) => this.queueSteering(text, fromDraft),
			setRunning: (running) => this.setRunning(running),
			setCommandRunning: (active) => this.setCommandRunning(active),
			inspectShellJob: (sessionId, jobId) => this.inspectShellJob(sessionId, jobId),
			syncWorkspaceShell: () => this.syncWorkspaceShell(),
			openDialog: (create) => this.openDialog(create),
			runCommand: (command) => runCommand(command, this),
			imageState: () => ({
				pending: this.pendingImageLoads,
				epoch: this.imageEpoch,
				settled: this.imageLoads,
			}),
			imageDraft: () => this.imageDraft,
			setImageDraft: (draft) => { this.imageDraft = draft; },
			bumpImageEpoch: () => ++this.imageEpoch,
			lastPrompt: () => ({
				text: this.lastPrompt,
				sessionId: this.lastPromptSessionId,
				images: this.lastPromptImages,
			}),
			rememberPrompt: (text, images, sessionId) => {
				this.lastPrompt = text;
				this.lastPromptImages = images;
				this.lastPromptSessionId = sessionId;
			},
		};
	}

	private eventHost(): EventHost {
		return {
			tui: this.tui,
			log: this.log,
			activity: this.activity,
			turnSession: this.turnSession,
			footer: this.footer,
			shell: this.shell,
			transcript: this.transcript,
			outputStore: this.outputStore,
			isExiting: () => this.exiting,
			current: () => this.current,
			featureEventHandlers: () => this.featureEventHandlers,
			workspaceEvent: (event) => this.workspaceSurface?.onEvent(event),
			setHarnessJob: (job) => this.status.setHarnessJob(job),
			refreshFamily: () => this.refreshFamily(),
			isFamilyMember: (sessionId) => this.isFamilyMember(sessionId),
			noteUnknownSession: (sessionId) => this.noteUnknownSession(sessionId),
			queueQuestion: (event) => this.approvals.park(event),
			promptQuestion: (event) => this.promptQuestion(event),
			promptApproval: (event) => this.promptApproval(event),
			clearApprovals: (sessionId) => this.clearApprovals(sessionId),
			switchKiroFallback: (sessionId) => { void this.switchKiroFallback(sessionId); },
			invalidateContextUsage: () => { this.contextReadVersion++; },
			refreshContextUsage: (sessionId) => { void this.refreshContextUsage(sessionId); },
			setRunning: (running) => this.setRunning(running),
			flushSteering: (sessionId) => { void this.flushSteering(sessionId); },
			refreshChangesBadge: (sessionId) => { void this.changes.refreshBadge(sessionId); },
			onChildEvent: (event) => this.onChildEvent(event),
		};
	}

	onInput(data: string): { consume: boolean } | undefined { return this.keyboard.handle(data); }
	onKey(data: string): { consume: boolean } | undefined { return this.keyboard.handleKey(data); }
	handleWorkspaceKey(data: string): { consume: boolean } | undefined {
		return this.keyboard.handleWorkspaceKey(data);
	}

	async start(): Promise<void> {
		return this.bootstrap.start();
	}

	private workspaceSurfaceHost(): WorkspaceSurfaceHost {
		const wiring: WorkspaceSurfaceWiring = {
			shell: this.shell,
			setWorkspaceHints: (hints) => { this.footer.workspaceHints = hints; },
			loadUiPreferences: () => { void this.loadUiPreferences(); },
			applyUiPreferences: () => this.applyUiPreferences(this.uiPrefs),
		};
		return this.workspaceCoordinator.workspaceSurfaceHost(wiring);
	}

	/** Start synchronously so workspace startup preserves the boot sequence. */
	startWorkspace(root: string): void {
		this.workspaceSurface = new WorkspaceSurface(this.workspaceSurfaceHost());
		this.client.onWorkspaceEvent = (event) => this.workspaceSurface?.onEvent(event);
		this.workspaceSurface.start(root);
	}

	private screen(): ScreenView {
		return this.view;
	}

	private isExiting(): boolean {
		return this.exiting;
	}

	private markExiting(): void {
		this.exiting = true;
	}

	private resolveExitPromise(): void {
		this.resolveExit();
	}

	private workspaceReview(): WorkspaceReview | undefined {
		const integration = this.integration;
		if (!integration) return undefined;
		return {
			documents: () => integration.store.documents(),
			save: (documentId) => integration.controller.save(documentId),
		};
	}

	private resolveWorkspaceSaveConflict(conflict: SaveConflictDescriptor): Promise<ConflictResolution | undefined> {
		return this.workspaceCoordinator.resolveSaveConflict(conflict);
	}

	private disposeWorkspaceTimer(): void {
		this.workspaceSurface?.disposeTimer();
	}

	private async disposeWorkspaceIntegration(): Promise<void> {
		await this.integration?.controller.dispose();
	}

	private discardCompletion(): void {
		this.workspaceCoordinator.discardCompletion();
	}

	private keyboardDispose(): void {
		this.keyboard.dispose();
	}

	private cancelAllDialogs(): void {
		for (const dialog of [...this.dialogs].reverse()) dialog.cancel();
	}

	private stopCursorBlink(): void {
		this.cursorBlinkSub?.stop();
		this.cursorBlinkSub = undefined;
		if (this.cursorBlinkIdleTimer !== undefined) clearTimeout(this.cursorBlinkIdleTimer);
		this.cursorBlinkIdleTimer = undefined;
	}

	// ─── FeatureHost ─────────────────────────────────────────────────────────
	async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
		const started = Date.now();
		try {
			const result = await this.client.request<T>(method, params, App.requestTimeoutMs(method, params));
			if (this.log.enabled) this.log.debug("rpc.ok", { method, ms: Date.now() - started, params: redactParamsForLog(method, params) });
			if (["mode.get", "mode.set", "mode.revoke"].includes(method)) {
				const responseSession = (result as { sessionId?: unknown; session_id?: unknown }).sessionId
					?? (result as { sessionId?: unknown; session_id?: unknown }).session_id;
				const requestedSession = params.sessionId ?? params.session_id;
				const sessionId = typeof responseSession === "string"
					? responseSession
					: typeof requestedSession === "string"
						? requestedSession
						: undefined;
				// A delayed response for a previous session must never rewrite the
				// footer after a session switch. Updates without a session scope
				// (the global mode query) keep their established behavior.
				if (sessionId !== undefined && this.current?.id !== sessionId) return result;
				const mode = (result as { mode?: string }).mode;
				if (typeof mode === "string") this.footer.mode = mode;
				this.tui.requestRender();
			}
			if (method === "session.setModel") this.adoptSessionRoute(result);
			if (method === "context.compress" && typeof params.sessionId === "string"
				&& (result as { compressed?: boolean })?.compressed) {
				void this.refreshContextUsage(params.sessionId);
			}
			return result;
		} catch (error) {
			this.log.warn("rpc.failed", {
				method,
				ms: Date.now() - started,
				params: redactParamsForLog(method, params),
				error: describeError(error),
			});
			throw error;
		}
	}

	/**
	 * Client clock per method. A client timeout at or below its backend
	 * deadline aborts the RPC while the other side is still working, so both
	 * budgets below outlast their server-side counterpart (finding H22).
	 * - `agents.run`: no fixed clock; individual work has its own safeguards.
	 * - `interop.run`: `/harnesses` passes the gateway's own deadline
	 *   (`interop.list.runTimeoutSeconds`) as `timeout`; wait past it by the
	 *   wind-down grace. The feature refuses to start a run whose deadline the
	 *   gateway did not report, so this branch always has the authoritative
	 *   number when a harness actually runs.
	 */
	private static requestTimeoutMs(method: string, params: Record<string, unknown>): number {
		if (method === "agents.run") return App.DELEGATION_TIMEOUT_MS;
		if (method === "interop.run") {
			const seconds = params.timeout;
			if (typeof seconds === "number" && Number.isFinite(seconds) && seconds > 0) {
				return seconds * 1000 + App.HARNESS_RUN_GRACE_MS;
			}
		}
		return 120_000;
	}

	/**
	 * Adopt the gateway's authoritative route for the current session after a
	 * `session.setModel`. Sessions pin model/provider at creation, so readers
	 * of `session()` (the `/provider remove` re-pin guard, `/models` grouping)
	 * must compare the live pin — never a snapshot taken before the last
	 * switch. All `/provider`/`/models` call sites route through `request`,
	 * so mirroring here keeps the snapshot faithful without re-fetching.
	 */
	private adoptSessionRoute(result: unknown): void {
		const current = this.current;
		if (!current) return;
		const route = result as { sessionId?: unknown; model?: unknown; provider?: unknown };
		if (typeof route.sessionId === "string" && route.sessionId !== current.id) return;
		const model = typeof route.model === "string" && route.model ? route.model : undefined;
		const provider = typeof route.provider === "string" && route.provider ? route.provider : undefined;
		if (!model && !provider) return;
		this.updateSession({
			...current,
			...(model ? { model } : {}),
			...(provider ? { provider } : {}),
		});
	}

	print(text: string, kind: NoticeKind = "info"): void {
		this.bootstrap.print(text, kind);
	}

    copyText(text: string): Promise<boolean> { return copyClipboardText(text); }

	/**
	 * FeatureHost: place *text* in the composer draft without submitting
	 * (the `/help` palette resolves commands that need arguments this way).
	 * Focus moves to the composer with the cursor after the inserted text.
	 */
	prefillInput(text: string): void {
		this.bootstrap.prefillInput(text);
	}

	onGatewayEvent(handler: (event: GatewayEvent) => void): () => void {
		this.featureEventHandlers.add(handler);
		return () => {
			this.featureEventHandlers.delete(handler);
		};
	}

	/**
	 * FeatureHost: the integrated-workspace surface. Present only when the
	 * feature was constructed at start(); commands check controllerAvailable()
	 * so a METHOD_NOT_FOUND backend disables them with one notice.
	 */
	workspace(): WorkspaceHost | undefined {
		return this.workspaceHostSurface;
	}

	/**
	 * FeatureHost: the interactive skills directory surface for `/skills`
	 * (A5, spec §12.3). Present whenever the app runs; the picker talks to
	 * `skills.search` / `skills.show` / `skills.preview_route` and falls back
	 * to the one honest error state when a method is unavailable.
	 */
	skillsDirectory(): SkillsDirectoryHost | undefined {
		return { openSkillsPicker: (query) => this.openSkillsPicker(query ?? "") };
	}

	// ─── global UI settings (closing pass) ──────────────────────────────────

	/**
	 * FeatureHost: the global UI settings surface for `/settings` (and bare
	 * `/config`). Present whenever the app runs; the snapshot reports which
	 * categories have a live target (workspace / editor view).
	 */
	uiSettings(): UiSettingsHost | undefined {
		return {
			snapshot: () => ({
				preferences: { ...this.uiPrefs },
				workspaceAvailable: this.workspaceHostSurface?.controllerAvailable() ?? false,
				editorAvailable: this.integration !== undefined,
			}),
			setPreference: (key, value) => this.setUiPreference(key, value),
			openWorkspaceFolder: (path) => this.switchWorkspaceRoot(path),
			closeWorkspace: async () => {
				await this.workspaceSurface?.close();
			},
			lspRows: () => this.lspSettingsRows(),
			refreshLsp: async () => {
				await this.integration?.controller.lspStatus();
			},
		};
	}

	async openKeybindings(): Promise<void> {
		await this.openDialog<void>((close) => new KeybindingsDialog(
			this.tui,
			this.keybindings.get(),
			(overrides) => {
				this.applyKeybindings(overrides);
				this.keybindingSave = this.keybindingSave.catch(() => {}).then(() =>
					this.client.request("config.set", { key: "keybindings", value: JSON.stringify(overrides), persist: true }, 30_000),
				).catch((error: unknown) => this.print(`Could not save keybindings: ${String(error)}`, "warning"));
			},
			() => close(),
		), { width: 68, maxHeight: 18, anchor: "center" });
		await this.keybindingSave;
	}

	private applyKeybindings(overrides: BindingOverrides): void {
		this.keybindings.set(overrides);
	}

	async openKeysEditor(): Promise<void> {
		const { rows } = await loadKeyRows(this);
		const choice = await this.openDialog<KeysDialogChoice>((close) => new KeysDialog(
			this.tui, rows, (selected) => close(selected), () => close(),
		), { width: 68, maxHeight: 19, anchor: "center" });
		if (!choice) return;
		if (choice.action !== "set" && choice.action !== "clear") return;
		if (choice.action === "clear") { await this.clearCredential(choice.key); return; }
		await this.editCredential(choice.key);
	}

	private async editCredential(key: string): Promise<void> {
		const value = await this.askText({
			title: `Set ${key}`,
			description: "Paste the value. It is masked and never displayed.",
			placeholder: "••••••••", secret: true, centered: true,
		});
		if (value === undefined) return;
		try {
			const result = await this.request<{ message?: string }>("secrets.set", { key, value, persist: true });
			this.print(result.message ?? `${key} set and saved.`, "success");
		} catch (error) {
			this.print(`Could not save ${key}: ${redactSecret(value, error instanceof Error ? error.message : String(error))}`, "error");
		}
	}

	private async clearCredential(key: string): Promise<void> {
		const confirmed = await this.pickCentered(`Clear ${key}? This removes it from this session and .env.`, [
			{ value: "no", label: "Cancel" }, { value: "yes", label: "Clear" },
		]);
		if (confirmed?.value !== "yes") return;
		const result = await this.request<{ message?: string }>("secrets.clear", { key });
		this.print(result.message ?? `${key} cleared.`, "success");
	}

	async openProviderEditor(providerName = ""): Promise<void> {
		const listed = await this.request<ProvidersListResult>("providers.list");
		const credentialNames = new Set(["openrouter", "openai", "anthropic", "google", "mistral", "groq", "together", "deepseek", "xai", "opencode", "kimi", "kiro", "custom"]);
		const rows = listed.providers.map((provider) => ({
			key: provider.name,
			status: provider.selected ? "current" : provider.kind === "named" ? "custom" : "built-in",
			set: provider.selected,
			editable: true,
			clearable: provider.kind === "named",
			credentialKey: provider.kind === "builtin" && credentialNames.has(provider.name) ? `${provider.name.toUpperCase()}_API_KEY` : undefined,
			description: `${provider.kind} ${provider.baseUrl ?? ""} ${provider.keySet ? "key set" : ""}`,
		}));
		const choice = await this.openDialog<KeysDialogChoice>((close) => new KeysDialog(
			this.tui, rows, (selected) => close(selected), () => close(), "Providers", providerName,
		), { width: 68, maxHeight: 19, anchor: "center" });
		if (!choice) return;
		if (choice.action === "addProvider") { await addProvider(this, ""); return; }
		if (choice.action === "removeProvider") { await removeProvider(this, choice.key); return; }
		if (choice.action === "editCredential" || choice.action === "removeCredential") {
			const key = rows.find((row) => row.key === choice.key)?.credentialKey;
			if (key) {
				if (choice.action === "editCredential") await this.editCredential(key);
				else await this.clearCredential(key);
			}
			return;
		}
		if (choice.action === "editProvider") {
			const existing = listed.providers.find((provider) => provider.name === choice.key);
			if (existing?.kind === "named") await addProvider(this, existing.name, existing.baseUrl ?? undefined);
			return;
		}
	}

	// ─── music (pinned now-playing bar) ─────────────────────────────────────

	/**
	 * FeatureHost: playback surface for `/music`. Undefined until the startup
	 * config loads (or when the host never received one), so commands report
	 * one honest notice instead of failing.
	 */
	music(): MusicHost | undefined {
		return this.bootstrap.music();
	}

	private cursorCaretVisible(): boolean {
		if (this.exiting) return false;
		const integration = this.integration;
		if (integration?.view.focused && integration.tabs.count() > 0) return true;
		return this.editor.focused && this.editor.getText().length > 0;
	}

	/**
	 * The caret's current blink phase. When no blink subscription is live
	 * the caret stays steadily visible, including while typing;
	 * under reduced motion the caret is pinned on (documented policy: focus
	 * must stay visible) and no timer exists at all.
	 */
	cursorBlinkOn(): boolean {
		if (!this.cursorBlinkSub || this.coordinator.isReducedMotion) return true;
		return this.coordinator.frame(["on", "off"], CURSOR_BLINK_INTERVAL_MS, this.cursorBlinkStartedAt) === "on";
	}

	/** Retire blink render demand while editing, then restart after idle. */
	private pauseEditorCursorBlink(): void {
		if (this.exiting || !this.integration?.view.focused || this.coordinator.isReducedMotion) return;
		if (this.cursorBlinkIdleTimer !== undefined) clearTimeout(this.cursorBlinkIdleTimer);
		this.cursorBlinkIdleTimer = setTimeout(() => {
			this.cursorBlinkIdleTimer = undefined;
			this.syncCursorBlink();
		}, CURSOR_BLINK_RESUME_DELAY_MS);
		this.syncCursorBlink();
	}

	/**
	 * Reconcile the single blink subscription with the focused caret. The
	 * stored handle guards subscribing, so repeated focus changes can never
	 * stack loops (the coordinator also replaces by key). Retired whenever no
	 * text surface owns the caret: explorer/problems focus, an overlay/dialog,
	 * an empty composer, an empty editor, or ongoing editor typing.
	 */
	private syncCursorBlink(): void {
		const editorFocused = this.integration?.view.focused === true;
		if (!editorFocused && this.cursorBlinkIdleTimer !== undefined) {
			clearTimeout(this.cursorBlinkIdleTimer);
			this.cursorBlinkIdleTimer = undefined;
		}
		const typing = editorFocused && this.cursorBlinkIdleTimer !== undefined;
		const needed = this.cursorCaretVisible() && !this.coordinator.isReducedMotion && !typing;
		if (needed && !this.cursorBlinkSub) {
			this.cursorBlinkStartedAt = Date.now();
			this.cursorBlinkSub = this.coordinator.subscribe("cursor.blink", CURSOR_BLINK_INTERVAL_MS);
		} else if (!needed && this.cursorBlinkSub) {
			this.cursorBlinkSub.stop();
			this.cursorBlinkSub = undefined;
		}
	}

	/** Apply one preference, persist it, and hand back the effective set. */
	private async setUiPreference(key: UiPreferenceKey, value: boolean | number): Promise<UiPreferences> {
		const next: UiPreferences = { ...this.uiPrefs };
		if (key === "syntax") next.syntax = value === true;
		else if (key === "softWrap") next.softWrap = value === true;
		else next.tabWidth = value === 2 || value === 4 || value === 8 ? value : 4;
		this.applyUiPreferences(next);
		// Persistence failure keeps the value applied for this session; the
		// caller (settings menu) reports that honestly and never blocks editing.
		await this.uiPreferencesStore().save(next);
		return { ...next };
	}

	/** Push preferences into the live editor view (no view: kept for later). */
	private applyUiPreferences(preferences: UiPreferences): void {
		this.uiPrefs = { ...preferences };
		const view = this.integration?.view;
		if (!view) return;
		view.setSyntaxEnabled(preferences.syntax);
		view.setSoftWrap(preferences.softWrap);
		view.setTabWidth(preferences.tabWidth);
	}

	/** Best-effort load of `ui_preferences` after initialize; never blocks. */
	private async loadUiPreferences(): Promise<void> {
		try {
			const preferences = await this.uiPreferencesStore().load();
			this.applyUiPreferences(preferences);
		} catch {
			// Defaults stay; the editor keeps working.
		}
	}

	private uiPreferencesStore(): UiPreferencesPersistence {
		return new UiPreferencesPersistence({
			get: async () => {
				const { config } = await this.client.request<ConfigGetResult>("config.get", {}, 30_000);
				const raw = config["ui_preferences"];
				return typeof raw === "string" && raw.length > 0 ? raw : undefined;
			},
			set: async (value) => {
				await this.client.request("config.set", { key: "ui_preferences", value, persist: true }, 30_000);
			},
		});
	}

	/** One settings row per language-server profile (server rows win). */
	private lspSettingsRows(): Array<{ profile: string; state: string; detail?: string }> {
		const controller = this.integration?.controller;
		if (!controller || controller.phase !== "ready") return [];
		const snapshot = controller.lspStatusSnapshot();
		const rows = new Map<string, { profile: string; state: string; detail?: string }>();
		for (const server of snapshot.servers) {
			const profile = server.profile || server.languageId;
			if (!profile) continue;
			rows.set(profile, {
				profile,
				state: server.state,
				...(server.detail !== undefined ? { detail: server.detail } : {}),
			});
		}
		for (const [profile, entry] of Object.entries(snapshot.profiles)) {
			if (rows.has(profile)) continue;
			rows.set(profile, {
				profile,
				state: entry.state,
				...(entry.detail !== undefined ? { detail: entry.detail } : {}),
			});
		}
		return [...rows.values()];
	}

	printMarkdown(text: string): void {
		this.transcript.addMarkdown(text, "output");
		this.tui.requestRender();
	}

	session(): SessionInfo | undefined {
		return this.current;
	}

	/**
	 * Render a blank chat without writing a session. The session row is
	 * created by the first submitted message; /new and /clear use this so an
	 * untouched draft never appears in history.
	 */
	private showNewChat(title?: string): void {
		this.sessions.showNewChat(title);
	}

	/** Host entry point for /new and post-delete navigation. */
	newSession(title?: string): void {
		this.sessions.newSession(title);
	}

	/** Create the durable session for a draft chat at its first message. */
	private ensureSession(text: string): Promise<void> {
		return this.sessions.ensureSession(text);
	}

	switchTo(session: SessionInfo, history: HistoryEntry[]): void {
		this.sessions.switchTo(session, history);
	}

	updateSession(session: SessionInfo): void {
		this.sessions.updateSession(session);
	}

	/** Mark a session visited; SessionController owns the bounded LRU state. */
	rememberSession(sessionId: string): void {
		this.sessions.rememberSession(sessionId);
	}

	/** Family rows, main first, with run/current state for menus. */
	private familyRows(): FamilySession[] {
		return this.sessions.familyRows();
	}

	private activitySummary(): string {
		const session = this.current;
		if (!session) return "";
		const queued = this.activity.queue(session.id).length + this.turnSession.queuedSteering(session.id).length;
		const jobs = this.activity.listJobs(session.id);
		const running = jobs.filter((job) => job.status === "running" || job.status === "stopping").length;
		const agents = this.familyRows().filter((row) => !row.main && row.running).length;
		const currentTool = this.turnSession.activeToolLabel(session.id);
		const failed = jobs.filter((job) => job.status === "failed").length;
		const stopped = jobs.filter((job) => job.status === "stopped").length;
		const finished = jobs.filter((job) => job.status === "exited").length;
		const shell = [running ? `${running} shell running` : "", failed ? `${failed} failed` : "", finished ? `${finished} finished` : "", stopped ? `${stopped} stopped` : ""].filter(Boolean).join(" · ");
		return [currentTool ?? "", queued ? `${queued} queued` : "", shell, agents ? `${agents} agents working` : ""].filter(Boolean).join("  ·  ");
	}

	private commandActions(): { label: string; run: () => void }[] { return this.commands.commandActions(); }

	private commandAction(sessionId: string, jobId: string, action: "background" | "stop" | "output"): Promise<void> {
		return this.commands.commandAction(sessionId, jobId, action);
	}

	private queueSteering(text: string, fromDraft = true): Promise<boolean> {
		return this.sessions.queueSteering(text, fromDraft);
	}

	private flushSteering(sessionId: string): Promise<void> {
		return this.sessions.flushSteering(sessionId);
	}

	/** Open the full-page live status tab (also `/activity`). */
	openStatus(): void {
		this.status.openStatus();
	}

	/** Open the full-screen token/skill dashboard (also `/usage`). */
	openUsage(): void {
		this.usage ??= new UsageController({
			tui: this.tui,
			request: <T>(method: string, params?: Record<string, unknown>) => this.request<T>(method, params),
			openDialog: <T>(create: (close: (value?: T) => void) => Component) => this.openDialog<T>(create),
		});
		this.usage.open();
	}

	/**
	 * Open the per-prompt Changes screen. Changes always belong to the open
	 * chat: with no chat there is nothing to review, and browsing another
	 * chat's work from here would misattribute it.
	 */
	openChanges(): void {
		this.status.openChanges();
	}

	/** Command-strip click: switch screens without changing the app's key map. */
	private selectNav(view: NavView): void {
		this.status.selectNav(view);
	}

	private navSnapshot(): NavSnapshot {
		return this.status.navSnapshot();
	}

	private openScreen(screen: "status" | "changes"): void {
		this.status.openScreen(screen);
	}

	private closeScreen(): void {
		this.status.closeScreen();
	}

	async inspectActivity(): Promise<void> {
		await this.status.inspectActivity();
	}

	private inspectShellJob(sessionId: string, id: string): Promise<void> {
		return this.status.inspectShellJob(sessionId, id);
	}

	/** FeatureHost: the current family (main first) for /agents and menus. */
	familySessions(): FamilySession[] {
		return this.sessions.familySessions();
	}

	/** Down on an empty composer: the same family picker as /agents sessions. */
	private openSubagentMenu(): Promise<void> {
		return this.sessions.openSubagentMenu();
	}

	/** Bounded, best-effort family refresh (see family.ts). */
	private refreshFamily(): void {
		this.sessions.refreshFamily();
	}

	/** Refresh kick for turn-start frames from ids not yet known family members. */
	private noteUnknownSession(sessionId: string): void {
		this.sessions.noteUnknownSession(sessionId);
	}

	private isFamilyMember(sessionId: string): boolean {
		return this.sessions.isFamilyMember(sessionId);
	}

	/** Cycle the family; wraps around. */
	private cycleFamily(direction: 1 | -1): Promise<void> {
		return this.sessions.cycleFamily(direction);
	}

	pick(title: string, items: SelectItem[], options?: PickOptions): Promise<SelectItem | undefined> {
		// The overlay is full-screen, but the menu itself is bounded to the
		// documented default so a long list scrolls instead of covering the
		// page; Menu reserves its own chrome rows within the live viewport.
		// Every dynamic title/label is display-sanitized at this one sink:
		// callers hand over file names, session titles and server strings.
		return this.openDialog<SelectItem>((close) =>
			new Picker(
				sanitizeDisplay(title),
				sanitizeSelectItems(items),
				close,
				() => close(),
				DEFAULT_MENU_VISIBLE_ROWS,
				undefined,
				this.tui,
				options?.count,
			),
		);
	}

	pickCentered(title: string, items: SelectItem[], options?: PickOptions): Promise<SelectItem | undefined> {
		return this.openDialog<SelectItem>((close) => new Picker(
			sanitizeDisplay(title), sanitizeSelectItems(items), close, () => close(),
			DEFAULT_MENU_VISIBLE_ROWS, undefined, this.tui, options?.count,
		), { width: 68, maxHeight: 19, anchor: "center" });
	}

	pickMany(title: string, items: SelectItem[]): Promise<SelectItem[] | undefined> {
		return this.openDialog<SelectItem[]>((close) => {
			const menu = new Menu(sanitizeDisplay(title), sanitizeSelectItems(items), {
				multiSelect: true,
				visibleRows: DEFAULT_MENU_VISIBLE_ROWS,
				tui: this.tui,
			});
			menu.onConfirm = close;
			menu.onCancel = () => close();
			return menu;
		});
	}

	/**
	 * Live theme preview: moving the selection recolors the whole visible
	 * UI without config writes; Esc restores the original theme; Enter
	 * commits the selected ID. Persistence stays with the feature module.
	 */
	pickTheme(): Promise<string | undefined> {
		const previous = getSkin();
		return this.openDialog<string>((close) => {
			const menu = new Menu(
				`Skins (current: ${previous})`,
				SKIN_NAMES.map((skin) => ({
					value: skin,
					label: `${skin}${skin === previous ? " (current)" : ""}`,
					description: skinDescription(skin),
				})),
				{ hint: "↑↓ preview · enter apply · esc restore", visibleRows: DEFAULT_MENU_VISIBLE_ROWS, tui: this.tui, initialValue: previous },
			);
			menu.onSelectionChange = (item) => {
				setSkin(item.value);
				this.restyle();
			};
			menu.onSelect = (item) => close(item.value);
			menu.onCancel = () => {
				setSkin(previous);
				this.restyle();
				close();
			};
			return menu;
		});
	}
	askText(options: AskTextOptions): Promise<string | undefined> {
		// Titles/descriptions/placeholders are chrome: sanitize once here so no
		// form implementation can render a hostile label (or bidi override).
		const safe = sanitizeAskTextOptions(options);
		if (safe.secret) {
			return this.openDialog<string>((close) => {
				const form = new SecretForm(this.tui, {
					title: safe.title,
					description: safe.description,
					placeholder: safe.placeholder ?? "••••••••",
					allowEmpty: safe.allowEmpty,
				});
				form.onSubmit = close;
				form.onCancel = () => close();
				return form;
			}, safe.centered ? { width: 68, maxHeight: 14, anchor: "center" } : undefined);
		}
		return this.openDialog<string>((close) => {
			const form = new TextForm(this.tui, editorTheme, safe);
			form.onSubmit = close;
			form.onCancel = () => close();
			return form;
		}, safe.centered ? { width: 68, maxHeight: 14, anchor: "center" } : undefined);
	}

	/**
	 * Cancellable transient overlay for OAuth browser authorization.
	 * Escape invokes options.onCancel and closes the overlay; status
	 * updates and the login link never touch the transcript.
	 */
	showOAuthProgress(options: OAuthProgressOptions): OAuthProgressHandle {
		let component: OAuthProgress | undefined;
		let closeDialog: ((value?: string) => void) | undefined;
		let settled = false;
		void this.openDialog<string>((close) => {
			closeDialog = close;
			component = new OAuthProgress(options.title, options.status, {
				onOpen: options.onOpen,
                onCopy: options.onCopy,
				onCancel: () => {
					if (settled) return;
					settled = true;
					options.onCancel();
					close("cancel");
				},
				idleHint: options.idleHint,
            }, options.loginUrl, this.tui);
			return component;
		}).then(() => {
			settled = true;
		});
		return {
			update: (status: string, loginUrl?: string) => {
				component?.setStatus(status, loginUrl);
				this.tui.requestRender();
			},
			dispose: () => {
				if (settled) return;
				settled = true;
				closeDialog?.();
			},
		};
	}

	/**
	 * Inspect locally retained tool output. Bare form picks from recent
	 * outputs of the current session; an explicit ID resolves only within
	 * the current session. Opening preserves the chat draft and transcript
	 * scroll position; closing returns to the previous dialog or composer.
	 */
	async inspectOutput(toolId?: string): Promise<void> {
		const session = this.current;
		if (!session) {
			this.transcript.addNotice(
				"No active session. Send a message first; /output will then list retained tool results.",
				"guidance",
			);
			return;
		}
		if (toolId) {
			try {
				await this.openViewer(this.outputStore.resolve(session.id, toolId));
			} catch (error) {
				this.transcript.addNotice(error instanceof Error ? error.message : String(error), "error");
			}
			return;
		}
		const recent = this.outputStore.list(session.id);
		if (recent.length === 0) {
			this.transcript.addNotice(
				"No retained tool output for this session. Run a task that uses a tool, then try /output again.",
				"guidance",
			);
			return;
		}
		const choice = await this.pick(
			"Recent tool output",
			recent.map((record) => ({
				value: record.toolId,
				label: `${record.name} (${record.toolId.slice(0, 8)})`,
				description: `${record.byteLength.toLocaleString("en-US")} bytes${record.backendTruncated ? ", server truncated" : ""}${record.locallyTruncated ? ", locally capped" : ""}`,
			})),
		);
		if (!choice) return;
		const record = this.outputStore.get(session.id, choice.value);
		if (!record) {
			this.transcript.addNotice("That output is no longer retained.", "warning");
			return;
		}
		await this.openViewer(record);
	}

	private openViewer(record: OutputRecord): Promise<void> {
		return this.openDialog<void>((close) => new OutputViewer(record, { tui: this.tui, onClose: () => close() })).then(
			() => undefined,
		);
	}

	profileId(): string {
		return this.currentProfile;
	}

	setProfile(userId: string): void {
		this.currentProfile = userId;
		this.tui.requestRender();
	}

	sessionAgent(): string | undefined {
		// The default profile shares the harness agent (existing data intact);
		// other profiles scope RL, growth notes, and memories via harness-<slug>.
		if (this.currentProfile === this.defaultProfile) return undefined;
		if (!PROFILE_ID_RE.test(this.currentProfile)) return undefined;
		return `harness-${this.currentProfile}`;
	}

	openDialog<T>(create: (close: (value?: T) => void) => Component, overlayOptions?: OverlayOptions): Promise<T | undefined> {
		if (this.exiting) return Promise.resolve(undefined);
		return new Promise((done) => {
			let settled = false;
			const close = (value?: T) => {
				if (settled) return;
				settled = true;
				// P0-23: every close path (choose, Esc-onClose, exit's
				// dialog.cancel()) funnels here before the dialog leaves the
				// DOM; menus and forms own timers/RPC continuations that must
				// not outlive their overlay. Dialogs without the hook are
				// inert callers.
				const liveStop = (component as { stopLiveWork?: () => void } | undefined)?.stopLiveWork;
				if (liveStop) liveStop.call(component);
				dialog.handle.hide();
				this.dialogs.splice(this.dialogs.indexOf(dialog), 1);
				const previous = this.dialogs.at(-1);
				if (this.view === "chat" && this.turnSession.approvalDialogCount()) this.showApprovalDock();
				else if (previous) { previous.handle.setHidden(false); previous.hidden = false; previous.handle.focus(); }
				// The composer owns focus after the last dialog (finding 3):
				// route through the region router so activeRegion agrees, or the
				// editor-scoped gates would still think the editor owns keys.
				else if (!this.exiting) {
					if (this.view === "chat") this.setFocusForRegion("chat-composer");
					else this.tui.setFocus(this.view === "status" ? this.statusPage : this.changesPage);
				}
				// Recompute the recorded region against the component that now
				// owns focus; a dialog close must never leave editor-scoped gates
				// armed behind the composer.
				if (this.view === "chat") this.reconcileFocus();
				this.syncCursorBlink();
				this.tui.requestRender();
				done(value);
			};
			const component = create(close);
			// Each dialog owns its handle, including when approvals interrupt a form.
			// Existing dialogs use a bottom sheet; focused settings can request
			// a centered, bounded overlay through overlayOptions.
			const handle = this.tui.showOverlay(component, overlayOptions ?? {
				width: "100%",
				maxHeight: "100%",
				anchor: "bottom-center",
			});
			const dialog = { handle, cancel: () => close(), hidden: false };
			this.dialogs.push(dialog);
			// Late management RPCs may finish after an approval arrives. Preserve
			// their draft invisibly until the bound decision releases focus.
			if (this.view === "chat" && this.turnSession.approvalDialogCount()) this.showApprovalDock();
			this.syncCursorBlink();
			this.tui.requestRender();
		});
	}

	async writeFile(path: string, text: string): Promise<string> {
		const full = resolve(process.cwd(), path);
		await mkdir(dirname(full), { recursive: true });
		try {
			// "wx" refuses to clobber: /init and /export must never silently
			// truncate an existing file (runCommand reports the refusal).
			await writeFile(full, text, { encoding: "utf8", flag: "wx" });
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code === "EEXIST") {
				throw new Error(`Refusing to overwrite existing file ${full}. Delete it first or choose another path.`);
			}
			throw error;
		}
		return full;
	}

	onConfig(result: ConfigResult): void {
		this.footer.model = result.model;
		this.tui.requestRender();
	}

	private async switchKiroFallback(sessionId: string): Promise<void> {
		if (this.turnSession.hasKiroFallback(sessionId)) return;
		this.turnSession.setKiroFallback(sessionId, true);
		try {
			const fallback = await this.request<KiroFallback>("auth.kiro.fallback", {});
			if (!fallback.provider || !fallback.model) {
				this.print(
					fallback.reason ??
						"Kiro credits are exhausted and no fallback provider is available. Set MACHINA_KIRO_FALLBACK=provider:model.",
					"warning",
				);
				this.turnSession.setKiroFallback(sessionId, false);
				return;
			}
			await this.request("session.setModel", {
				sessionId,
				provider: fallback.provider,
				model: fallback.model,
			});
			if (this.sessions.adoptRoute(sessionId, fallback.provider, fallback.model)) {
				this.footer.model = fallback.model;
			}
			this.print(
				`Kiro credits are exhausted. Switched this chat to ${fallback.provider} / ${fallback.model}; send your message again.`,
				"warning",
			);
		} catch (error) {
			this.turnSession.setKiroFallback(sessionId, false);
			this.print(`Could not switch the Kiro fallback: ${message(error)}`, "error");
		} finally {
			this.tui.requestRender();
		}
	}

	clear(): void {
		this.transcript.clear();
		this.tui.requestRender();
	}

	restyle(): void {
		this.transcript.restyle();
		// A theme changes empty viewport rows too, which do not otherwise
		// differ in the renderer's retained screen cache.
		this.tui.requestRender(true);
	}

	async exit(): Promise<void> {
		return this.bootstrap.exit();
	}


	// ─── input ────────────────────────────────────────────────────────────
    private clearImages(): void {
        this.imageEpoch++;
        this.imageDraft = new ImageDraft();
        this.lastPromptImages = [];
        this.lastPromptSessionId = undefined;
    }

    private acceptImageDrop(text: string): boolean {
        const paths = imagePaths(text);
        if (!paths) return false;
        this.loadImages(() => {
            if (paths.length > 5) throw new Error("Attach at most 5 images per message.");
            return Promise.all(paths.map(readImageFile));
        });
        return true;
    }

    private loadImages(load: () => Promise<ImageAttachment[]>): void {
        const epoch = this.imageEpoch;
        this.pendingImageLoads++;
        this.imageLoads = this.imageLoads.then(async () => {
            if (epoch !== this.imageEpoch || this.exiting) return;
            const images = await load();
            if (epoch !== this.imageEpoch || this.exiting || !images.length) return;
            this.imageDraft.prune(this.editor.getExpandedText());
            const markers = this.imageDraft.addMany(images);
            this.editor.insertTextAtCursor(markers.join(" ") + " ");
        }).catch((error) => {
            this.imageLoadFailures++;
            if (epoch === this.imageEpoch && !this.exiting) this.print(message(error), "error");
        }).finally(() => {
            this.pendingImageLoads--;
            this.tui.requestRender();
        });
    }

	/**
	 * Keyboard shortcut rows, from the single source shared with
	 * `/help` (design/shortcuts.ts). Workspace rows only make sense while the
	 * feature is live, but they stay listed so the shortcuts remain
	 * discoverable; unavailable ones no-op with one notice.
	 */
	private shortcutItems(): SelectItem[] {
		return effectiveShortcutRows(this.keybindings.get()).map((row) => ({
			value: row.value,
			label: row.label,
			...(row.description === undefined ? {} : { description: row.description }),
		}));
	}

	private openShortcutReference(): { consume: true } {
		void this.openDialog((close) => new Picker("Keyboard shortcuts", this.shortcutItems(), (item) => {
			close();
			if (item.value === "commands") {
				this.closeScreen();
				const command = parseCommand("/help");
				if (command) void runCommand(command, this);
			}
		}, () => close(), SHORTCUTS_VISIBLE_ROWS, "↑↓ browse · Enter/Esc close", this.tui));
		return { consume: true };
	}

	private pasteFromClipboard(imageOnly: boolean): void {
		const epoch = this.imageEpoch;
		this.loadImages(async () => {
			const image = await (this.options.readClipboardImage ?? clipboardImage)();
			if (image) return [image];
			if (imageOnly) throw new Error("No image found on the clipboard. Copy an image or drop an image file into chat.");
			const text = await clipboardText();
			if (epoch !== this.imageEpoch || this.exiting) return [];
			const paths = imagePaths(text);
			if (paths) return Promise.all(paths.map(readImageFile));
			if (text) this.editor.insertTextAtCursor(sanitizePastedText(text));
			else this.print("No image or text found in the clipboard. Drop an image file to attach it.", "info");
			return [];
		});
	}

	private syncWorkspaceShell(): void {
		this.workspaceSurface?.syncShell();
	}

	/** Delegate: deferred chrome sync lives on WorkspaceSurface. */
	private scheduleWorkspaceChromeSync(): void {
		this.workspaceSurface?.scheduleChromeSync();
	}

	/** Delegate: cheap geometry re-sync lives on WorkspaceSurface. */
	private syncWorkspaceShellIfChanged(): void {
		this.workspaceSurface?.syncShellIfChanged();
	}

	/** Delegate: explicit root switch lives on WorkspaceSurface. */
	private switchWorkspaceRoot(path: string): Promise<{ ok: boolean; message: string }> {
		return this.workspaceSurface?.switchRoot(path)
			?? Promise.resolve({ ok: false, message: "Workspace is not available." });
	}

	// ─── workspace lifecycle fence (contract C3 / MCH-012) ──────────────────

	/** Delegate: enter a new lifecycle epoch (workspace surface). */
	private beginWorkspaceFence(): number {
		return this.workspaceSurface?.beginFence() ?? this.workspaceGeneration;
	}

	/** Delegate: capture the live epoch + controller generation. */
	private captureWorkspaceFence(): number {
		return this.workspaceSurface?.captureFence() ?? this.workspaceGeneration;
	}

	/** Delegate: true only while the captured epoch/generation is live. */
	private isWorkspaceCurrent(gen: number): boolean {
		if (this.exiting) return false;
		return this.workspaceSurface?.isCurrent(gen) ?? true;
	}


	// ─── workspace facade ────────────────────────────────────────────────────

	/** Public App facade retained for workspace integration callers and tests. */
	setFocusForRegion(region: FocusRegion): void {
		this.workspaceCoordinator.setFocusForRegion(region);
	}

	private closeQuickOpen(): void {
		this.workspaceCoordinator.closeQuickOpen();
	}

	noteRegionFocus(region: FocusRegion): void {
		this.workspaceCoordinator.noteRegionFocus(region);
	}

	openProblems(): void {
		this.workspaceCoordinator.openProblems();
	}

	lspStatusText(): string | undefined {
		return this.workspaceCoordinator.lspStatusText();
	}

	private reconcileFocus(): void {
		this.workspaceCoordinator.reconcileFocus();
	}

	/** Public App facade retained for workspace integration callers and tests. */
	openWorkspacePath(path: string, options: { preview?: boolean; focus?: boolean } = {}): Promise<void> {
		return this.workspaceCoordinator.openWorkspacePath(path, options);
	}

	// ─── skills directory (A5, spec §12.3) ──────────────────────────────────

	private openSkillsPicker(initialQuery = ""): void { this.commands.openSkillsPicker(initialQuery); }

	private submit(raw: string): Promise<void> { return this.commands.submit(raw); }

    async submitTurn(text: string, images: ImageAttachment[] = [], steeringId?: string): Promise<void> {
		if (!this.current) {
			// Queued steering belongs to a session that must already exist;
			// a direct turn starts a new chat and persists it on this message.
			if (steeringId) return;
			await this.ensureSession(text);
		}
		const current = this.current;
		if (!current) return;
		if (current.parentSessionId && !current.isFork && !current.harnessPlatform) {
			this.transcript.addNotice("Subagent sessions are for inspection. Return to the main session to send a message.", "info");
			this.tui.requestRender();
			return;
		}
		const sessionId = current.id;
		this.log.info("turn.submit", {
			session: sessionId,
			chars: text.length,
			images: images.length,
			steering: Boolean(steeringId),
		});
		if (this.turnSession.isInFlight(sessionId)) {
			this.transcript.addNotice("The previous turn is still finishing. Try again shortly.", "warning");
			this.tui.requestRender();
			return;
		}
		this.clearPendingAbortEsc();
		this.turnSession.setInterrupted(sessionId, false);
		this.transcript.addUser(text);
		this.setRunning(true);
		// Register the submitting record before dispatch: gateway work is
		// asynchronous and events can arrive before the RPC result.
		this.turnSession.beginTurn(sessionId);
		this.tui.requestRender();
		try {
            const result = await this.client.request<{ turnId?: string }>("prompt.submit", {
                sessionId, text, ...(images.length ? { images } : {}), ...(steeringId ? { steeringId } : {}),
            });
			this.log.debug("turn.accepted", { session: sessionId, turnId: result?.turnId ?? null });
			if (this.turnSession.confirmTurn(sessionId, result?.turnId) === "mismatch") {
				this.transcript.addNotice(
					`Protocol error: turn ${result?.turnId?.slice(0, 8)} does not match the running turn. Keeping the current turn guard.`,
					"error",
				);
				this.tui.requestRender();
			}
		} catch (error) {
			// Rejection known to precede acceptance (no dispatch evidence)
			// retires the submitting record. After possible dispatch the
			// outcome is unknown: retain the guard, never auto-retry.
			const dispatched = this.turnSession.hadEvents(sessionId);
			this.log.warn("turn.submit.failed", {
				session: sessionId,
				dispatched,
				error: describeError(error),
			});
			if (!dispatched) this.turnSession.clearInFlight(sessionId);
			this.setRunning(this.turnSession.visibleInFlight());
			throw error;
		}
	}

	private armPendingAbortEsc(): void {
		this.clearPendingAbortEsc();
		this.pendingAbortEscAt = Date.now();
		this.footer.keyHint = "Esc again to stop reply · Ctrl+C stops immediately";
		this.pendingAbortEscTimer = setTimeout(() => {
			this.pendingAbortEscAt = 0;
			this.pendingAbortEscTimer = undefined;
			if (!this.exiting) this.footer.keyHint = "";
			this.tui.requestRender();
		}, 900);
		this.tui.requestRender();
	}

	private clearPendingAbortEsc(): void {
		this.pendingAbortEscAt = 0;
		if (this.pendingAbortEscTimer !== undefined) {
			clearTimeout(this.pendingAbortEscTimer);
			this.pendingAbortEscTimer = undefined;
		}
	}

	private cancel(): void {
		this.clearPendingAbortEsc();
		const inFlight = this.turnSession.inFlightSessions();
		if (inFlight.length === 0) return;
		this.log.info("turn.cancel", { sessions: inFlight });
		// Cancel every in-flight turn, not just the most recent: two sessions
		// can each have a turn running after a quick switch.
		for (const sessionId of inFlight) {
			this.client.request("prompt.cancel", { sessionId }).catch(() => {});
		}
		// Don't setRunning(false) while any session is still in inFlight —
		// the completion event hasn't arrived yet and the guard will reject
		// the next submit if we clear running now.
		if (!this.turnSession.visibleInFlight()) {
			this.setRunning(false);
		}
	}

	private setRunning(running: boolean): void {
		this.running = running;
		this.composer.placeholder = running ? "Queue a message · applies at next tool boundary" : "Ask anything, or type / for commands.";
		// AH-031: do NOT disable the composer while a turn runs — safe
		// slash commands (/sessions, /new, /exit, /help, …) must stay
		// usable. Prompt submits are guarded in submit()/submitTurn().
		this.editor.disableSubmit = false;
		if (this.footer.status !== "offline") {
			if (!running) this.footer.status = "ready";
			else if (!["awaiting_approval", "stopping", "cleanup_pending"].includes(this.footer.status)) this.footer.status = "working";
		}
		this.shell.invalidateChrome();
	}

	private setCommandRunning(active: boolean): void {
		this.commandRunning = active;
		this.commandRunningAt = active ? Date.now() : 0;
	}

	private async refreshContextUsage(sessionId: string): Promise<void> {
		if (this.exiting || this.current?.id !== sessionId) return;
		const version = ++this.contextReadVersion;
		try {
			const { totalTokens, budget } = await this.request<ContextResult>("context.get", { sessionId, limit: 0 });
			if (this.exiting || this.contextReadVersion !== version || !Number.isFinite(totalTokens)) return;
			this.footer.contextTokens = totalTokens;
			this.footer.contextPercent = budget > 0 ? 100 * totalTokens / budget : null;
			this.tui.requestRender();
		} catch {
			// Usage is supplemental; retain the last reading if the RPC fails.
		}
	}

	// ─── gateway ────────────────────────────────────────────────────────────
	private onChildEvent(event: Extract<GatewayEvent, { type: "agent.child" }>): void {
		const childId = event.childSessionId;
		const turnId = `delegated:${event.turnId}`;
		if (event.phase === "start") {
			this.turnSession.startDelegated(childId);
			this.refreshFamily();
		} else if (event.phase === "text" || event.phase === "tool") {
			const text = event.phase === "tool" ? `\n[using ${truncateToWidth(sanitizeDisplay(event.value), 80, "…")}]\n` : event.value;
			this.turnSession.appendDelegated(childId, text);
			const delta: GatewayEvent = {
				protocolVersion: 2, sessionId: childId, turnId, type: "message.delta", text,
			};
			if (this.current?.id === childId) this.transcript.apply(delta);
			else this.turnSession.bufferHiddenEvent(delta);
		} else if (event.phase === "settled" || event.phase === "paused") {
			const deleted = event.phase === "settled" && event.value === "deleted";
			if (deleted) {
				this.clearApprovals(childId);
				this.approvals.discard(childId);
				this.activity.pruneSession(childId);
				this.outputStore.pruneSession(childId);
			}
			const complete: GatewayEvent = {
				protocolVersion: 2, sessionId: childId, turnId, type: "message.complete",
				text: this.turnSession.finishDelegated(childId), tokens: 0, iterations: 0, toolCalls: 0, cancelled: false,
			};
			if (this.current?.id === childId) {
				if (deleted) this.transcript.clear();
				else this.transcript.apply(complete);
				this.setRunning(false);
			} else if (!deleted) this.turnSession.bufferHiddenEvent(complete);
			if (event.phase === "settled") {
				const main = this.familyRows().find((row) => row.main)?.session.id;
				this.sessions.dismissFamily(childId);
				if (this.current?.id === childId) {
					void this.sessions.openFamily(main ?? event.sessionId).then(() => {
						if (!deleted || this.exiting) return;
						if (this.current?.id === childId) this.newSession();
						this.turnSession.pruneSessionState(childId);
					});
				} else if (deleted) this.turnSession.pruneSessionState(childId);
			}
		}
		this.tui.requestRender();
	}

	private flushPendingQuestions(sessionId: string): void {
		this.approvals.flushQuestions(sessionId);
	}

	private promptQuestion(event: Extract<GatewayEvent, { type: "question.asked" }>): Promise<void> {
		return this.approvals.promptQuestion(event);
	}

	private promptApproval(event: Extract<GatewayEvent, { type: "needs_approval" | "permission.required" }>): void {
		this.approvals.prompt(event);
	}

	private showApprovalDock(): void {
		this.approvals.showDock();
	}

	/** Invalidate pending approval cards without resolving (navigation/exit/disconnect). */
	private invalidateApprovals(sessionId?: string): void {
		this.approvals.invalidate(sessionId);
	}

	/** Retire approval evidence and cards for a session whose turn ended. */
	private clearApprovals(sessionId: string): void {
		this.approvals.clear(sessionId);
	}

	onGatewayExit(code: number | null, stderr: string[]): void {
		this.bootstrap.onGatewayExit(code, stderr);
	}

}

function message(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}
