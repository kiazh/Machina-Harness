// App bootstrap and lifecycle controller: the one boot sequence, the pinned
// now-playing startup, the dirty-quit review, and both teardown paths.
//
// Interface invariants:
// - start() is the only boot path and preserves the established order:
//   shell/TUI setup, gateway client start, initialize, config/theme/keybindings,
//   profile first-touch, session resume/new, mode notice, workspace surface
//   start, then the now-playing/profile joins and the final render. The
//   now-playing bar never delays session creation; a rejected startup is
//   swallowed because the bar is optional.
// - exit() reviews dirty workspace buffers before any teardown. Cancel or a
//   failed save leaves the app fully alive; the review is re-entrancy guarded
//   and a workspace lifecycle change during the review retires the request.
// - onGatewayExit() is idempotent (both channel error and process exit can
//   call it) and retires local prompts/timers without issuing resolve RPCs.
// - print() and prefillInput() are the FeatureHost text sinks; they are the
//   single place that decides markdown-vs-notice rendering.
// - BootstrapHost is deliberately narrow: only the App members these methods
//   actually call. It is not an AppContext.
import { isAbsolute } from "node:path";
import {
	isViewportTUI,
	type Container,
	type Editor,
	type SelectItem,
	type TUI,
} from "@earendil-works/pi-tui";
import type { AppShell } from "../components/AppShell.ts";
import type { ComposerBox } from "../components.ts";
import type { Footer } from "../components/Footer.ts";
import type { NowPlayingBar } from "../components/NowPlayingBar.ts";
import type { AnimationCoordinator, AnimationSubscription } from "../design/motion.ts";
import { describeError } from "../debug.ts";
import type { DocumentRecord } from "../editor/model.ts";
import type { NoticeKind } from "../features/index.ts";
import type { MusicHost } from "../features/types.ts";
import type { GatewayClient } from "../gateway.ts";
import { parseBindingOverrides, type BindingOverrides } from "../keybindings.ts";
import { NowPlayingController } from "../nowplaying/controller.ts";
import type {
	ConfigGetResult,
	GatewayEvent,
	InitializeResult,
	ResumeResult,
	SessionInfo,
} from "../protocol.ts";
import { isOpaqueBackground, setOpaqueBackground, setSkin } from "../theme.ts";
import type { Transcript } from "../transcript.ts";
import type { TurnSessionStore } from "../turnSessionStore.ts";
import type { ConflictResolution, SaveConflictDescriptor, SaveOutcome } from "../workspace/controller.ts";
import { displayUriName } from "../workspace/surface.ts";
import type { LogSink, ScreenView } from "./contracts.ts";

/** The launch options the boot sequence reads (AppOptions is assignable). */
export interface BootstrapOptions {
	model?: string;
	provider?: string;
	sessionId?: string;
	mode?: string;
}

/**
 * The workspace slice the dirty-quit review needs.
 *
 * Captured once at review start, exactly like the former `integration`
 * local: buffers and saves must keep talking to the workspace that owned the
 * review even if a lifecycle switch lands mid-loop (the fence check then
 * retires the exit request).
 */
export interface WorkspaceReview {
	documents(): DocumentRecord[];
	save(documentId: string): Promise<SaveOutcome>;
}

/**
 * App facade methods the bootstrap controller calls.
 *
 * After integration, `App` implements this interface (most members already
 * exist as facade methods); the controller never sees the whole App.
 */
export interface BootstrapHost {
	readonly tui: TUI;
	readonly client: GatewayClient;
	readonly log: LogSink;
	readonly footer: Footer;
	readonly transcript: Transcript;
	readonly shell: AppShell;
	readonly coordinator: AnimationCoordinator;
	readonly editor: Editor;
	readonly nowPlayingBar: NowPlayingBar;
	/** Only dispose() is part of this seam. */
	readonly status: { dispose(): void };
	readonly turnSession: TurnSessionStore;
	/** Non-viewport fallback stacking (classic TUI/test doubles). */
	readonly transcriptView: Container;
	readonly composer: ComposerBox;
	readonly options: BootstrapOptions;
	/** First-touch identity; the `profile.get` guarantee in start(). */
	readonly defaultProfile: string;

	/** Keyboard sink registered at start; the disposer belongs to the controller. */
	onInput(data: string): { consume: boolean } | undefined;
	/** Existing event router; the client callbacks are wired here. */
	onEvent(event: GatewayEvent): void;
	syncCursorBlink(): void;
	/** Stop the single caret-blink subscription and its idle timer. */
	stopCursorBlink(): void;

	request<T>(method: string, params?: Record<string, unknown>): Promise<T>;
	current(): SessionInfo | undefined;
	switchTo(session: SessionInfo, history: ResumeResult["history"]): void;
	showNewChat(): void;
	applyKeybindings(overrides: BindingOverrides): void;
	restyle(): void;
	screen(): ScreenView;
	setFocusForRegion(region: "chat-composer"): void;

	/**
	 * Construct the workspace surface and start it at the absolute launch
	 * root. App still owns the WorkspaceSurfaceHost wiring (workspace
	 * controller extraction); the bootstrap only decides when it starts.
	 */
	startWorkspace(root: string): void;
	beginWorkspaceFence(): number;
	captureWorkspaceFence(): number;
	isWorkspaceCurrent(gen: number): boolean;
	workspaceReview(): WorkspaceReview | undefined;
	resolveWorkspaceSaveConflict(conflict: SaveConflictDescriptor): Promise<ConflictResolution | undefined>;
	disposeWorkspaceTimer(): void;
	disposeWorkspaceIntegration(): Promise<void>;

	/** Drop the pending completion timer/session without closing the session. */
	discardCompletion(): void;
	/** Dispose all keyboard-router state (timer and pending leader). */
	keyboardDispose(): void;
	clearPendingAbortEsc(): void;
	closeQuickOpen(): void;
	/** Cancel every open dialog in reverse mount order. */
	cancelAllDialogs(): void;
	invalidateApprovals(): void;
	setRunning(running: boolean): void;
	isExiting(): boolean;
	markExiting(): void;
	/** Resolve the App's `exited` promise (the field is named resolveExit). */
	resolveExitPromise(): void;

	pick(title: string, items: SelectItem[]): Promise<SelectItem | undefined>;
}

export class AppBootstrap {
	private readonly host: BootstrapHost;
	private nowPlayingController: NowPlayingController | undefined;
	private nowPlayingAnim: AnimationSubscription | undefined;
	private inputListenerDisposer: (() => void) | undefined;
	/** Re-entrancy guard for the dirty-exit review. */
	private dirtyExitInProgress = false;
	/** Gateway exit can arrive from both the error and exit events. */
	private gatewayExitHandled = false;

	constructor(host: BootstrapHost) {
		this.host = host;
	}

	async start(): Promise<void> {
		const host = this.host;
		const workspaceFlagOn = !/^(0|false|off)$/i.test((process.env.MACHINA_WORKSPACE ?? "").trim());
		host.log.info("start", {
			sessionId: host.options.sessionId ?? null,
			model: host.options.model ?? null,
			provider: host.options.provider ?? null,
			mode: host.options.mode ?? null,
			workspace: workspaceFlagOn,
		});
		// Clear vacated rows when content shrinks (closed menus, finished
		// turns). Without this, filled rows linger as ghost bands behind
		// newer output.
		host.tui.setClearOnShrink(true);
		host.shell.sync();
		if (isViewportTUI(host.tui)) {
			host.tui.setLayoutRoot(host.shell.root);
		} else {
			// Fallback for non-viewport TUIs (and test doubles): classic stacking.
			host.tui.addChild(host.transcriptView);
			host.tui.addChild(host.composer);
			host.tui.addChild(host.footer.chatStatus);
			host.tui.addChild(host.footer);
		}
		host.tui.setFocus(host.editor);
		host.syncCursorBlink();
		this.inputListenerDisposer = host.tui.addInputListener((data) => host.onInput(data));

		host.footer.cwd = process.cwd();
		host.footer.status = "offline";
		host.transcript.addNotice("Starting Machina…");
		// Transparent background: apply the override before the first frame so
		// there is never a flash of the opaque canvas. The launcher exports the
		// saved value as MACHINA_TRANSPARENT_BACKGROUND; the persisted file value
		// (read below) restyles with a full repaint if it differs.
		const transparentEnv = /^(1|true|on|yes)$/i.test((process.env.MACHINA_TRANSPARENT_BACKGROUND ?? "").trim());
		setOpaqueBackground(!transparentEnv);
		host.tui.start();

		host.client.onEvent = (event) => host.onEvent(event);
		host.client.onExit = (code, stderr) => this.onGatewayExit(code, stderr);
		host.client.start();

		let initializedCwd: string | undefined;
		let initializedCwdFull: string | undefined;
		// Music detection can stall for seconds (including a blocking macOS
		// Automation prompt); it is joined at the end of start() but MUST NOT
		// delay session creation (finding 8).
		let nowPlayingReady: Promise<void> | undefined;
		let profileReady: Promise<void> | undefined;
		try {
			const init = await host.client.request<InitializeResult>(
				"initialize",
				{ protocolVersion: 2, model: host.options.model, provider: host.options.provider, mode: host.options.mode },
				60_000,
			);
			initializedCwd = init.cwd;
			initializedCwdFull = typeof init.cwdFull === "string" && init.cwdFull ? init.cwdFull : undefined;
			Object.assign(host.footer, { cwd: init.cwd, branch: init.branch, model: init.model, status: "ready" });
			host.log.info("gateway.ready", {
				version: init.version,
				model: init.model,
				provider: init.provider,
				cwd: init.cwd,
			});
			try {
				const { config } = await host.client.request<ConfigGetResult>("config.get", {}, 30_000);
				host.applyKeybindings(parseBindingOverrides(config.keybindings));
				if (typeof config.theme === "string" && config.theme) {
					setSkin(config.theme);
					// The startup frame is already on screen. Use the same
					// cache invalidation/full redraw as live theme selection.
					host.restyle();
				}
				if (typeof config.transparent_background === "boolean") {
					const opaque = !config.transparent_background;
					if (opaque !== isOpaqueBackground()) {
						setOpaqueBackground(opaque);
						// Same full redraw as a skin change: every painted cell
						// (including empty viewport rows) must drop the fill.
						host.restyle();
					}
				}
				// Kick off the pinned now-playing bar here, but never await it
				// before the session exists (finding 8). start() joins the
				// promise before returning, so the bar still lands on the first
				// frame while session creation stays unblocked.
				nowPlayingReady = this.startNowPlaying(config).catch(() => {
					// The music bar is optional; detection failures never block chat.
				});
			} catch {
				// Saved skin is best-effort; the default applies otherwise.
			}
			// Guarantee the default profile exists (first-touch RL/growth/memory
			// identity), but do it alongside the first chat render rather than
			// blocking it on a DB round-trip.
			profileReady = host.client
				.request("profile.get", { userId: host.defaultProfile }, 30_000)
				.then(() => {})
				.catch(() => {
					// Profiles stay best-effort; commands surface real errors.
				});
			if (host.options.sessionId) {
				const { session, history } = await host.client.request<ResumeResult>("session.resume", {
					sessionId: host.options.sessionId,
				});
				host.switchTo(session, history);
			} else {
				// A blank chat is client-side only. The first real message
				// creates the session, so opening the CLI never adds an empty
				// conversation to history.
				host.showNewChat();
			}
			try {
				// Session-scoped authority for the initial session (finding 8):
				// resolve AFTER the session exists so the footer and the
				// notice below reflect the governing session, never the
				// global default. Later switches refresh via switchTo.
				const current = host.current();
				const { mode } = await host.request<{ mode: string }>(
					"mode.get",
					current ? { sessionId: current.id } : {},
				);
				if (mode === "full") {
					host.transcript.addNotice("FULL HOST mode active for this session: host operations proceed without further approval, including credential access, elevation, and destructive system ops.", "warning");
				}
			} catch {
				// Mode display is best-effort.
			}
		} catch (error) {
			host.log.error("start.failed", { error: describeError(error), stderr: host.client.stderr().slice(-8) });
			host.footer.status = "offline";
			host.transcript.clear();
			host.transcript.addNotice(`Could not start: ${message(error)}`, "error");
			const tail = host.client.stderr().slice(-8);
			if (tail.length) host.transcript.addNotice(tail.join("\n"));
			host.transcript.addNotice("Run `machina doctor` to check your setup. Press Ctrl+C to exit.");
		}
		// Integrated workspace: only after initialize + session setup, only when
		// the flag allows it, and never blocking chat. Prefer the absolute
		// launch directory (authenticated handshakes). The redacted basename is
		// a display value and must never be treated as a root: when only that
		// is available (older backend), stay chat-only.
		if (workspaceFlagOn) {
			const root = initializedCwdFull ?? (initializedCwd !== undefined && isAbsolute(initializedCwd) ? initializedCwd : undefined);
			if (root !== undefined) {
				host.startWorkspace(root);
			}
		}
		// Join the background first-touch work now that the session is visible.
		// Neither the profile row nor the music bar may block session creation.
		await Promise.all([profileReady, nowPlayingReady]);
		host.log.info("ready", { session: host.current()?.id ?? null, view: host.screen() });
		host.tui.requestRender();
	}

	/** FeatureHost: the playback surface; undefined until startup config loads. */
	music(): MusicHost | undefined {
		return this.nowPlayingController;
	}

	/**
	 * Start the pinned now-playing bar: config gate, first reading, and the
	 * shared-coordinator subscriptions (poll always; 250 ms animation only
	 * while audio is actually playing).
	 */
	private async startNowPlaying(config: Record<string, unknown>): Promise<void> {
		this.nowPlayingController = new NowPlayingController({
			bar: this.host.nowPlayingBar,
			request: (method, params) => this.host.client.request(method, params ?? {}, 30_000),
			onChange: () => {
				this.syncNowPlayingTicks();
				this.host.tui.requestRender();
			},
		});
		await this.nowPlayingController.initialize(config);
	}

	/**
	 * Subscribe/detach the coordinator demand for the bar. Polling is a slow
	 * 2 s heartbeat; the equalizer and interpolated progress need 250 ms
	 * frames, but only while audio is playing.
	 */
	private syncNowPlayingTicks(): void {
		// Data polling is owned by the controller (its own 2 s interval,
		// stopped on exit/disable); the coordinator here only drives render
		// demand: 250 ms frames for the equalizer and interpolated clock
		// while audio is actually playing.
		const controller = this.nowPlayingController;
		const state = controller?.status();
		const playing =
			(controller?.isEnabled() ?? false) &&
			state?.state === "playing" &&
			Boolean(state.title);
		if (playing && !this.nowPlayingAnim) {
			this.nowPlayingAnim = this.host.coordinator.subscribe("nowplaying.anim", 250);
		} else if (!playing && this.nowPlayingAnim) {
			this.nowPlayingAnim.stop();
			this.nowPlayingAnim = undefined;
		}
	}

	print(text: string, kind: NoticeKind = "info"): void {
		// Plain command output is a report, not a one-line status: render it
		// through Markdown so headings, lists, tables and inline code are
		// parsed consistently instead of appearing as literal source text.
		// The one exception is the pinned U07 `/skills` fallback, whose
		// byte-identical fixed-width table is already a readable report.
		if (kind === "plain" && !/^[ \t]*[─]{3,}[ \t]{2}/m.test(text))
			this.host.transcript.addMarkdown(text, "output");
		else this.host.transcript.addNotice(text, kind);
		this.host.tui.requestRender();
	}

	/**
	 * FeatureHost: place *text* in the composer draft without submitting
	 * (the `/help` palette resolves commands that need arguments this way).
	 * Focus moves to the composer with the cursor after the inserted text.
	 */
	prefillInput(text: string): void {
		this.host.editor.setText(text);
		this.host.setFocusForRegion("chat-composer");
		this.host.tui.requestRender();
	}

	async exit(): Promise<void> {
		const host = this.host;
		if (host.isExiting()) return;
		// Plan §9 dirty-quit review: one dialog before any teardown. The review
		// runs while the app is still alive (dialogs stay usable); a cancel or
		// a failed save leaves everything untouched.
		if (this.dirtyExitInProgress) return;
		this.dirtyExitInProgress = true;
		try {
			if (!(await this.reviewDirtyExit())) return;
		} finally {
			this.dirtyExitInProgress = false;
		}
		if (host.isExiting()) return;
		host.markExiting();
		host.keyboardDispose();
		host.clearPendingAbortEsc();
		host.footer.keyHint = "";
		// P0-20: the Quick Open overlay is not an openDialog dialog, so the
		// cancel loop below never sees it; tear it down explicitly so its
		// pending provider timer dies with the app.
		host.closeQuickOpen();
		// MCH-012: every in-flight workspace open/switch continuation is stale
		// the moment the app is leaving: it must publish nothing.
		host.beginWorkspaceFence();
		host.footer.dispose();
		host.status.dispose();
		host.turnSession.clearInFlightTimers();
		host.transcript.suspend();
		host.invalidateApprovals();
		this.nowPlayingController?.stop();
		this.nowPlayingAnim?.stop();
		this.nowPlayingAnim = undefined;
		host.stopCursorBlink();
		host.coordinator.dispose();
		host.cancelAllDialogs();
		this.inputListenerDisposer?.();
		host.tui.stop();
		await host.disposeWorkspaceIntegration();
		await host.client.stop();
		host.resolveExitPromise();
	}

	/**
	 * Dirty-quit review (plan §9): lists up to two dirty basenames plus a
	 * `+N` remainder (finding 6: `1 unsaved file: main.py`) and offers
	 * Save all & exit / Discard & exit / Cancel. Save-all drives sequential
	 * controller saves; the first failure (conflict, refusal, transport) keeps
	 * the app open with a notice naming the file.
	 */
	private async reviewDirtyExit(): Promise<boolean> {
		const workspace = this.host.workspaceReview();
		if (!workspace || this.host.isExiting()) return true;
		const dirty = workspace.documents().filter((document) => document.dirty);
		if (dirty.length === 0) return true;
		const names = dirty.slice(0, 2).map((document) => displayUriName(document.uri));
		const more = dirty.length > names.length ? ` +${dirty.length - names.length}` : "";
		const label = `${dirty.length} unsaved file${dirty.length === 1 ? "" : "s"}: ${names.join(", ")}${more}`;
		const choice = await this.host.pick(label, [
			{ value: "save", label: "Save all & exit" },
			{ value: "discard", label: "Discard & exit" },
			{ value: "cancel", label: "Cancel" },
		]);
		if (!choice || choice.value === "cancel") return false;
		if (choice.value === "discard") return true;
		// MCH-012: a workspace lifecycle change during the review retires the
		// exit request (the buffers belong to a workspace that moved on).
		const gen = this.host.captureWorkspaceFence();
		if (!this.host.isWorkspaceCurrent(gen)) return false;
		for (const document of dirty) {
			const outcome = await workspace.save(document.id);
			if (!this.host.isWorkspaceCurrent(gen)) return false;
			if (outcome.status === "conflict") {
				// "Save all & exit" survives the conflict detour: an explicit
				// overwrite/reload leaves the file saved (or adopted), so the
				// requested exit is still the user's decision. "Keep editing"
				// (or a failed/stale resolution) cancels it honestly.
				const resolution = await this.host.resolveWorkspaceSaveConflict(outcome.conflict);
				if (!this.host.isWorkspaceCurrent(gen)) return false;
				if (resolution?.status === "saved" || resolution?.status === "reloaded") continue;
				this.print(`Exit cancelled: ${displayUriName(document.uri)} was not saved.`, "warning");
				return false;
			}
			if (outcome.status !== "saved") {
				this.print(`Exit cancelled: could not save ${displayUriName(document.uri)}.`, "error");
				return false;
			}
		}
		return true;
	}

	onGatewayExit(code: number | null, stderr: string[]): void {
		const host = this.host;
		if (host.isExiting() || this.gatewayExitHandled) return;
		this.gatewayExitHandled = true;
		// MCH-012: the transport is gone - in-flight workspace opens publish
		// nothing (their teardown RPCs cannot be trusted either).
		host.beginWorkspaceFence();
		this.nowPlayingController?.stop();
		this.nowPlayingAnim?.stop();
		this.nowPlayingAnim = undefined;
		host.disposeWorkspaceTimer();
		host.keyboardDispose();
		host.discardCompletion();
		host.stopCursorBlink();
		host.footer.dispose();
		host.clearPendingAbortEsc();
		host.status.dispose();
		host.transcript.suspend();
		// The gateway is gone: clear workspace timers/mirrors without RPC
		// expectations (every teardown call is bounded and fails fast).
		void host.disposeWorkspaceIntegration();
		// Retire local approval prompts without resolving: navigation and
		// disconnect invalidation never issues surprise resolve RPCs.
		host.invalidateApprovals();
		// Clear all in-flight tracking so the session isn't permanently locked.
		host.turnSession.clearAllInFlight();
		host.setRunning(false);
		host.footer.status = "offline";
		host.transcript.addNotice(`The gateway stopped${code === null ? "" : ` (exit code ${code})`}.`, "error");
		const tail = stderr.slice(-8);
		if (tail.length) host.transcript.addNotice(tail.join("\n"));
		host.transcript.addNotice("Press Ctrl+C to exit, then run `machina doctor`.");
		host.tui.requestRender();
	}
}

function message(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}
