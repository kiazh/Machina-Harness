// Keyboard router: one precedence chain for every key that reaches the app.
//
// Interface invariants:
// - `handle()` is the TUI input-listener seam. Its result keeps the toolkit
//   shape: `{ consume: true }` swallows the key, `undefined` forwards it to the
//   focused component.
// - Precedence is fixed and lives here: dialog/approval/quick-open/overlay →
//   page strip → workspace → chat. Only the page strip may browse around a
//   pending approval; every other chat key leaves the approval card alone.
// - Key releases are always consumed; auto-repeat never re-arms a leader, a
//   double-Esc abort, or the explorer burst. Legacy Windows modifier-arrow
//   encodings are normalized before dispatch.
// - Editor text keys (plain arrows, letters) fall through unhandled; the
//   focused editor receives them from the toolkit. Only commands that the
//   editor does not own are consumed here.
// - Host state is read, never copied: the composition root owns the screen,
//   focus region, overlays, run state and leader hint line.
import { isKeyRelease, isKeyRepeat, Key, matchesKey, parseKey } from "@earendil-works/pi-tui";
import type { NavView } from "../components/NavBar.ts";
import type { NoticeKind } from "../features/index.ts";
import type { AppKeybindings } from "../keybindings.ts";
import type { FocusRegion } from "../workspace/state.ts";
import type { ScreenView } from "./contracts.ts";

/** Minimal render seam the router needs from the TUI. */
export interface KeyboardTui {
	requestRender(): void;
}

/** Composer surface driven directly only by the legacy modifier fallback. */
export interface KeyboardEditor {
	handleInput(data: string): void;
	getText(): string;
	setText(text: string): void;
	isShowingAutocomplete(): boolean;
}

/** Footer hint line owned by the router while a leader is armed. */
export interface KeyboardFooter {
	keyHint: string;
}

/** Page components consume their own content keys. */
export interface KeyboardPage {
	handleInput(data: string): boolean;
}

/** Tool-output toggle on the transcript. */
export interface KeyboardTranscript {
	toggleLastTool(): boolean;
}

/** The inline suggestion the editor is currently showing, if any. */
export interface InlineCompletionSession {
	readonly documentId: string;
	inlinePreview(): string | undefined;
}

/** Result of attaching the active editor selection to the composer draft. */
export interface KeyboardAttachment {
	ok: boolean;
	notice: string;
}

/**
 * Focused composition-root surface for the keyboard router.
 *
 * Every member has a caller in `onKey`, `onInput`, `handleWorkspaceKey` or
 * `matchesExplorerKey`. State members are getters because App owns the single
 * copy of each; actions are the same facade paths the old private methods used.
 * Workspace members are deliberately narrow: the router never receives the
 * workspace controller or surface object, only the questions it asks and the
 * commands it forwards.
 */
export interface KeyboardHost {
	readonly tui: KeyboardTui;
	readonly editor: KeyboardEditor;
	readonly footer: KeyboardFooter;
	readonly transcript: KeyboardTranscript;
	readonly keybindings: AppKeybindings;

	// Shell state and geometry. Read live: a key can change the screen mid-route.
	screen(): ScreenView;
	activeRegion(): FocusRegion;
	syncShell(): void;
	syncWorkspaceShellIfChanged(): void;
	scheduleWorkspaceChromeSync(): void;
	reconcileFocus(): void;
	statusPage(): KeyboardPage;
	changesPage(): KeyboardPage;

	// Overlay ownership and the pre-dialog command window.
	dialogCount(): number;
	hasVisibleDialog(): boolean;
	quickOpenActive(): boolean;
	dismissQuickOpen(): void;
	commandRunning(): boolean;
	commandRunningAt(): number;

	// Turn, approval and destructive-action state.
	approvalDialogCount(): number;
	isRunning(): boolean;
	pendingAbortEscAt(): number;
	armPendingAbortEsc(): void;
	clearPendingAbortEsc(): void;
	lastPrompt(): string;
	hasPendingImageLoads(): boolean;
	cancel(): void;
	exit(): void | Promise<void>;
	clear(): void;

	// Screen strip and overlays.
	openStatus(): void;
	openScreen(screen: "status" | "changes"): void;
	closeScreen(): void;
	selectNav(view: NavView): void;
	openShortcutReference(): { consume: boolean };

	// Chat, composer and agent-family actions.
	familyCount(): number;
	cycleFamily(direction: 1 | -1): void | Promise<void>;
	closeFamilyCurrent(): void | Promise<void>;
	openSubagentMenu(): void | Promise<void>;
	pasteFromClipboard(imageOnly: boolean): void;
	clearImages(): void;
	completionSession(): InlineCompletionSession | undefined;
	activeEditorDocumentId(): string | undefined;
	acceptCompletion(): void;
	clearCompletion(): void;
	problemsOpen(): boolean;
	closeProblems(): void;

	// Workspace surface: existence, readiness and the few states keys consult.
	hasWorkspace(): boolean;
	workspaceReady(): boolean;
	workspaceLayoutActive(): boolean;
	workspaceUnsupported(): boolean;
	explorerRequested(): boolean;
	setExplorerRequested(requested: boolean): void;
	restoreSplit(): void;
	toggleChatMaximize(): void;
	toggleEditorMaximize(): void;
	editorHasSelection(): boolean;
	handleEditorInput(data: string): void;

	// Workspace commands reached from the global key map.
	openQuickOpen(): void | Promise<void>;
	closeActiveWorkspaceTab(): void;
	cycleFocus(direction: 1 | -1): void;
	attachSelectionToDraft(): KeyboardAttachment;
	startCompletion(): void | Promise<void>;
	runDefinitionOrReferences(operation: "definition" | "references"): void | Promise<void>;
	runRenamePreview(): void | Promise<void>;
	saveActiveDocument(): void | Promise<void>;

	print(text: string, kind?: NoticeKind): void;
}

/**
 * Normalize modifier-arrow encodings used by Windows consoles.
 *
 * Windows Terminal/ConPTY normally sends the standard xterm form
 * (\x1b[1;5D), which pi-tui parses. A few console hosts drop the leading
 * "1;" and send \x1b[5D instead. Rewrite only those unambiguous variants so
 * the focused editor and the workspace shortcuts see one canonical key.
 */
function normalizeModifierArrow(data: string): string {
	switch (data) {
		case "\x1b[5D":
			return "\x1b[1;5D";
		case "\x1b[5C":
			return "\x1b[1;5C";
		case "\x1b[6D":
			return "\x1b[1;6D";
		case "\x1b[6C":
			return "\x1b[1;6C";
		case "\x1bO5D":
			return "\x1b[1;5D";
		case "\x1bO5C":
			return "\x1b[1;5C";
		default:
			return data;
	}
}

/** The router's own transient chord state; nothing else reads it. */
interface PendingLeader {
	key: string;
	at: number;
}

export class KeyboardRouter {
	private readonly host: KeyboardHost;
	private pendingKeyLeader: PendingLeader | undefined;
	private pendingKeyLeaderTimer: ReturnType<typeof setTimeout> | undefined;
	private lastEscAt = 0;
	private explorerBurstStartedAt = 0;
	private explorerBurstCount = 0;
	private explorerBurstKey = "";

	constructor(host: KeyboardHost) {
		this.host = host;
	}

	/**
	 * TUI input-listener entry point. Returns the consume shape the toolkit
	 * expects: a handled key is swallowed, an unhandled one reaches focus.
	 */
	handle(data: string): { consume: boolean } | undefined {
		return this.onInput(data);
	}

	/** App/test facade for the original key-only dispatch seam. */
	handleKey(data: string): { consume: boolean } | undefined {
		return this.onKey(data);
	}

	/** Release the leader timer on exit/gateway teardown; idempotent. */
	dispose(): void {
		if (this.pendingKeyLeaderTimer !== undefined) clearTimeout(this.pendingKeyLeaderTimer);
		this.pendingKeyLeaderTimer = undefined;
		this.pendingKeyLeader = undefined;
	}

	private onInput(data: string): { consume: boolean } | undefined {
		// Kitty-aware terminals can deliver key-up and auto-repeat events. A
		// release must never cancel a pending leader, and a held leader must not
		// restart its own timer. Legacy terminals send plain bytes, so the
		// action-level cooldowns below still cover those repeats.
		if (isKeyRelease(data)) return { consume: true };
		// A command that has not opened its dialog yet owns the keyboard.
		// Once a dialog/approval is visible it receives keys normally.
		if (this.host.commandRunning() && !this.host.dialogCount() && !this.host.approvalDialogCount()) {
			// Commands normally open their dialog immediately. A slow RPC must
			// not make the composer look dead: keep keys for the first 1.5s,
			// then let ordinary typing reach the editor while still reserving
			// Enter/Esc for the command's own flow.
			const startedAt = this.host.commandRunningAt() || Date.now();
			if (Date.now() - startedAt < 1500) return { consume: true };
			if (matchesKey(data, Key.enter) || matchesKey(data, Key.escape)) {
				return { consume: true };
			}
			return undefined;
		}
		const repeated = isKeyRepeat(data);
		const normalized = normalizeModifierArrow(data);
		if (repeated && matchesKey(normalized, Key.enter)
			&& (this.host.approvalDialogCount() || this.host.screen() !== "chat" || this.host.activeRegion() === "chat-composer")) {
			return { consume: true };
		}
		if (this.host.pendingAbortEscAt() && !matchesKey(normalized, Key.escape)) this.host.clearPendingAbortEsc();
		if (repeated && this.host.pendingAbortEscAt() && matchesKey(normalized, Key.escape)) return { consume: true };
		if (repeated && this.pendingKeyLeader) return { consume: true };
		if (this.pendingKeyLeaderTimer) clearTimeout(this.pendingKeyLeaderTimer);
		this.pendingKeyLeaderTimer = undefined;
		this.host.footer.keyHint = "";
		if (this.host.dialogCount() || this.host.approvalDialogCount() || this.host.quickOpenActive()) {
			this.pendingKeyLeader = undefined;
			return this.onKey(normalized);
		}
		const pending = this.pendingKeyLeader;
		this.pendingKeyLeader = undefined;
		if (pending && Date.now() - pending.at < 5_000) {
			if (matchesKey(normalized, Key.escape)) return { consume: true };
			if (matchesKey(normalized, pending.key as Parameters<typeof matchesKey>[1])) {
				// Holding the leader keeps it armed instead of consuming the
				// repeat as an unknown second key.
				this.pendingKeyLeader = { key: pending.key, at: Date.now() };
				this.pendingKeyLeaderTimer = setTimeout(() => {
					this.pendingKeyLeader = undefined;
					this.pendingKeyLeaderTimer = undefined;
					this.host.footer.keyHint = "";
					this.host.tui.requestRender();
				}, 5_000);
				this.host.footer.keyHint = `${pending.key.toUpperCase()} then key · Esc cancels`;
				this.host.tui.requestRender();
				return { consume: true };
			}
			const next = parseKey(normalized);
			if (next) this.onKey(`${pending.key} ${next.length === 1 ? next.toLowerCase() : next}`);
			return { consume: true };
		}
		for (const leader of this.host.keybindings.leaders()) {
			if (matchesKey(normalized, leader as Parameters<typeof matchesKey>[1])) {
				this.pendingKeyLeader = { key: leader, at: Date.now() };
				this.host.footer.keyHint = `${leader.toUpperCase()} then key · Esc cancels`;
				this.pendingKeyLeaderTimer = setTimeout(() => {
					this.pendingKeyLeader = undefined;
					this.pendingKeyLeaderTimer = undefined;
					this.host.footer.keyHint = "";
					this.host.tui.requestRender();
				}, 5_000);
				this.host.tui.requestRender();
				return { consume: true };
			}
		}
		const result = this.onKey(normalized);
		if (result) return result;
		if (normalized === data) return undefined;
		// The toolkit only sees modifier-arrow sequences in its canonical xterm
		// form. Dispatch an unrecognized legacy encoding ourselves instead of
		// letting the focused component receive the original bytes.
		if (this.host.activeRegion() === "chat-composer") {
			this.host.editor.handleInput(normalized);
			this.host.tui.requestRender();
			return { consume: true };
		}
		if (this.host.activeRegion() === "editor" && this.host.hasWorkspace()) {
			this.host.handleEditorInput(normalized);
			return { consume: true };
		}
		return undefined;
	}

	private onKey(data: string): { consume: boolean } | undefined {
		this.host.syncShell();
		this.host.syncWorkspaceShellIfChanged();
		// A key may only reach a component the current geometry still composes.
		// Layout changes normally reconcile already; this closes the window where
		// the TUI still targets a pane removed by a resize/visibility toggle.
		if (this.host.screen() === "chat") this.host.reconcileFocus();
		// The composer's autocomplete popup can change the chrome height while
		// this key is being handled by the focused Editor. Recheck after the
		// input dispatch so the panes (including Problems) get the remaining
		// rows instead of being clipped by the grown composer.
		this.host.scheduleWorkspaceChromeSync();
		// A visible dialog owns its keys. A dialog merely hidden by the approval
		// dock must still let the screen strip navigate; Esc keeps going to the
		// page it belongs to, never to the dialog underneath.
		if (this.host.dialogCount()) {
			if (this.host.hasVisibleDialog()) return undefined;
			const navKey = this.host.keybindings.matches(data, "showChat")
				|| this.host.keybindings.matches(data, "statusLeft")
				|| this.host.keybindings.matches(data, "activity")
				|| this.host.keybindings.matches(data, "statusRight");
			const pageKey = this.host.screen() !== "chat" && (
				matchesKey(data, Key.escape)
				|| this.host.keybindings.matches(data, "shortcuts")
				|| matchesKey(data, Key.question)
				|| matchesKey(data, Key.shift("?"))
				|| matchesKey(data, Key.shift("/"))
			);
			if (!(navKey || pageKey)) return undefined;
		}

		const questionKey = matchesKey(data, Key.question) || matchesKey(data, Key.shift("?")) || matchesKey(data, Key.shift("/"));
		const tabKey = !this.host.quickOpenActive() && (
			this.host.keybindings.matches(data, "showChat")
			|| this.host.keybindings.matches(data, "statusLeft")
			|| this.host.keybindings.matches(data, "activity")
			|| this.host.keybindings.matches(data, "statusRight"));
		// A pending approval owns decisions, not navigation: browsing Activity
		// or Changes and coming back to decide is always allowed. While a page
		// is open, the page owns its own keys (Esc closes it).
		if (this.host.approvalDialogCount() && this.host.screen() === "chat" && !tabKey) return undefined;
		if (!this.host.quickOpenActive()) {
			// Activity ← Chat → Changes. The arrows walk one linear strip, so
			// Chat is reachable from either side and Esc always returns to it.
			if (this.host.keybindings.matches(data, "statusLeft")) {
				if (this.host.screen() === "changes") this.host.closeScreen();
				else if (this.host.screen() === "chat") this.host.openStatus();
				return { consume: true };
			}
			if (this.host.keybindings.matches(data, "statusRight")) {
				if (this.host.screen() === "status") this.host.closeScreen();
				else if (this.host.screen() === "chat") this.host.openScreen("changes");
				return { consume: true };
			}
			if (this.host.keybindings.matches(data, "showChat")) {
				this.host.selectNav("chat");
				return { consume: true };
			}
			if (this.host.keybindings.matches(data, "activity")) {
				if (this.host.screen() !== "status") this.host.openStatus();
				return { consume: true };
			}
		}
		if (this.host.screen() !== "chat") {
			// A page owns its content keys, never the chat draft. Stop and exit
			// stay global: watching a run from Activity must still cancel it.
			if (this.host.keybindings.matches(data, "stop")) {
				if (this.host.isRunning()) this.host.cancel();
				this.host.tui.requestRender();
				return { consume: true };
			}
			if (this.host.keybindings.matches(data, "exit") && !this.host.isRunning()) {
				void this.host.exit();
				return { consume: true };
			}
			if (matchesKey(data, Key.escape)) {
				this.host.closeScreen();
				return { consume: true };
			}
			if (this.host.keybindings.matches(data, "shortcuts") || questionKey) return this.host.openShortcutReference();
			if (this.host.screen() === "status") this.host.statusPage().handleInput(data);
			else this.host.changesPage().handleInput(data);
			return { consume: true };
		}

		// ── integrated workspace (plan §8 focus priority) ────────────────────
		// The Quick Open overlay owns every key except Esc/Ctrl+C (which would
		// otherwise reach the stop/exit branches below): returning early lets
		// the focused overlay component receive everything else.
		if (this.host.quickOpenActive()) {
			if (matchesKey(data, Key.escape) || this.host.keybindings.matches(data, "stop")) {
				this.host.dismissQuickOpen();
				return { consume: true };
			}
			return undefined;
		}
		if (this.host.screen() === "chat" && this.host.activeRegion() !== "editor" && matchesKey(data, Key.ctrl("o"))) {
			if (this.host.transcript.toggleLastTool()) return { consume: true };
		}
		const explorerKey = this.matchesExplorerKey(data);
		if (explorerKey) {
			// Ctrl+T is a toggle, so terminal auto-repeat must not flip it
			// indefinitely while the user holds the key. Allow two deliberate
			// presses in a short burst, then suppress the repeat stream.
			const now = Date.now();
			if (data !== this.explorerBurstKey || now - this.explorerBurstStartedAt > 700) {
				this.explorerBurstKey = data;
				this.explorerBurstStartedAt = now;
				this.explorerBurstCount = 0;
			}
			if (this.explorerBurstCount >= 2) return { consume: true };
			this.explorerBurstCount++;
		}
		if (explorerKey && (!this.host.hasWorkspace() || this.host.workspaceUnsupported() || !this.host.workspaceLayoutActive())) {
			this.host.print(
				this.host.workspaceUnsupported()
					? "The file explorer is unavailable on this backend."
					: "Open a workspace first: /workspace open .",
				"warning",
			);
			return { consume: true };
		}
		if (this.host.hasWorkspace() && !this.host.workspaceUnsupported()) {
			// Ghost text owns only Tab and Escape; Enter and arrows keep editing.
			const completion = this.host.completionSession();
			if (completion && this.host.activeRegion() === "editor") {
				if (matchesKey(data, Key.tab)
					&& this.host.activeEditorDocumentId() === completion.documentId
					&& completion.inlinePreview() !== undefined) {
					this.host.acceptCompletion();
					return { consume: true };
				}
				const dismiss = matchesKey(data, Key.escape);
				this.host.clearCompletion();
				if (dismiss) return { consume: true };
			}
			// The Problems pane owns Esc while focused: it closes the section
			// instead of falling into the double-Esc prompt refill or stopping
			// the running turn.
			if (this.host.problemsOpen() && this.host.activeRegion() === "problems" && matchesKey(data, Key.escape)) {
				this.host.closeProblems();
				return { consume: true };
			}
			const workspaceKey = this.handleWorkspaceKey(data);
			if (workspaceKey) return workspaceKey;
		}

		// Ctrl+V belongs to the focused file editor. The explicit image
		// shortcut reaches the composer even when the terminal owns Ctrl+V.
		const paste = this.host.keybindings.matches(data, "pasteImage");
		const macPaste = this.host.keybindings.matches(data, "pasteImageMac");
		const imageOnly = this.host.keybindings.matches(data, "pasteImageDirect");
		if (paste && !imageOnly && this.host.activeRegion() === "editor") return undefined;
		if (paste || macPaste || imageOnly) {
			this.host.pasteFromClipboard(imageOnly);
			return { consume: true };
		}

		if (this.host.keybindings.matches(data, "shortcuts") || (questionKey && !this.host.editor.getText() && !this.host.editor.isShowingAutocomplete())) {
			return this.host.openShortcutReference();
		}

		if (this.host.keybindings.matches(data, "stop")) {
			// A focused editor owns Ctrl+C while it has selected text; the
			// global exit/cancel/clear route must not preempt a copy.
			if (this.host.activeRegion() === "editor" && this.host.editorHasSelection()) {
				return undefined;
			}
			if (this.host.isRunning()) this.host.cancel();
			else if (this.host.editor.getText() || this.host.hasPendingImageLoads()) { this.host.editor.setText(""); this.host.clearImages(); }
			else void this.host.exit();
			this.host.tui.requestRender();
			return { consume: true };
		}
		if (this.host.keybindings.matches(data, "exit") && !this.host.isRunning() && !this.host.editor.getText()) {
			void this.host.exit();
			return { consume: true };
		}
		if (this.host.keybindings.matches(data, "cancelTurn") && this.host.isRunning() && !this.host.editor.isShowingAutocomplete()) {
			// Escape is destructive: require a deliberate second press. Ctrl+C
			// remains the immediate stop path.
			const now = Date.now();
			if (this.host.pendingAbortEscAt() && now - this.host.pendingAbortEscAt() <= 900) {
				this.host.clearPendingAbortEsc();
				this.host.cancel();
			} else {
				this.host.armPendingAbortEsc();
			}
			return { consume: true };
		}
		if (this.host.keybindings.matches(data, "clear") && !this.host.isRunning()) {
			this.host.clear();
			return { consume: true };
		}
		// Agent family: Alt+J/K cycles from the composer. Ctrl+Shift+Arrow
		// remains available for word selection in the file editor.
		const cycle = this.host.keybindings.matches(data, "agentNext") ? 1 : this.host.keybindings.matches(data, "agentPrevious") ? -1 : 0;
		if (this.host.keybindings.matches(data, "agentClose")) {
			void this.host.closeFamilyCurrent();
			return { consume: true };
		}
		if (cycle !== 0 && this.host.activeRegion() === "chat-composer" && this.host.familyCount() > 1) {
			void this.host.cycleFamily(cycle as 1 | -1);
			return { consume: true };
		}
		// Down on an empty composer opens the subagent menu (the same picker
		// as /agents sessions). With text, with an inline suggestion, or with
		// no subagents it falls through untouched to the editor's own
		// Down handling (movement and history). Dialogs and approvals were
		// already given the key above.
		if (
			this.host.keybindings.matches(data, "agentMenu") &&
			!this.host.editor.getText() &&
			!this.host.editor.isShowingAutocomplete() &&
			this.host.familyCount() > 1
		) {
			void this.host.openSubagentMenu();
			return { consume: true };
		}
		if (
			matchesKey(data, Key.escape) &&
			!this.host.isRunning() &&
			!this.host.editor.getText() &&
			!this.host.editor.isShowingAutocomplete()
		) {
			// Codex double-Esc: refill the previous prompt for editing.
			const now = Date.now();
			if (now - this.lastEscAt < 800 && this.host.lastPrompt()) {
				this.host.editor.setText(this.host.lastPrompt());
				this.lastEscAt = 0;
			} else {
				this.lastEscAt = now;
			}
			return { consume: true };
		}
		return undefined;
	}

	/**
	 * Ctrl+T / Ctrl+Shift+E / Alt+E. Ctrl+Shift+E is the browser-safe
	 * alias because Chrome/Edge reserve Ctrl+T before the page sees it.
	 */
	private matchesExplorerKey(data: string): boolean {
		return (
			this.host.keybindings.matches(data, "explorer") ||
			this.host.keybindings.matches(data, "explorerAlt") ||
			this.host.keybindings.matches(data, "explorerSafe")
		);
	}

	/**
	 * Global workspace key routing. Keys are consumed only while the workspace
	 * is live and the feature was not disabled; editor-scoped keys additionally
	 * require the editor region so they can never fire while the composer (or a
	 * dialog, handled earlier) owns focus. Ctrl+W closes the active tab through
	 * the same dirty-guarded flow as the tab strip's ✕ (the terminal may
	 * intercept Ctrl+W; `/editor close` is the fallback).
	 */
	handleWorkspaceKey(data: string): { consume: boolean } | undefined {
		if (!this.host.hasWorkspace()) return undefined;
		const ready = this.host.workspaceReady();
		// Ctrl+P — Quick Open overlay (fallback: /quickopen; palette-safe).
		if (this.host.keybindings.matches(data, "quickOpen") && ready) {
			void this.host.openQuickOpen();
			return { consume: true };
		}
		if (!this.host.workspaceLayoutActive()) return undefined;
		// Ctrl+T / Ctrl+Shift+E / Alt+E: explorer toggle.
		if (this.matchesExplorerKey(data)) {
			this.host.setExplorerRequested(!this.host.explorerRequested());
			return { consume: true };
		}
		// Ctrl+W — close the active editor tab (dirty → Save/Discard/Cancel).
		// NOT in the chat composer: the composer's Editor owns Ctrl+W as
		// delete-word-backward, and stealing it there would break typing
		// (CodeRabbit pass-2 major; composer scoping per the ADR matrix).
		if (this.host.keybindings.matches(data, "closeTab") && this.host.activeRegion() !== "chat-composer") {
			this.host.closeActiveWorkspaceTab();
			return { consume: true };
		}
		// Alt+Q/W cycles focus without modifier-arrow ambiguity.
		const previousFocus = this.host.keybindings.matches(data, "focusPrevious");
		if (this.host.keybindings.matches(data, "focusNext") || previousFocus) {
			this.host.cycleFocus(previousFocus ? -1 : 1);
			return { consume: true };
		}
		// Pane modes are available from every workspace region. Text fields keep
		// their ordinary Ctrl+Arrow navigation.
		if (ready && this.host.keybindings.matches(data, "restoreSplit")) {
			this.host.restoreSplit();
			return { consume: true };
		}
		if (ready && this.host.keybindings.matches(data, "chatMaximize")) {
			this.host.toggleChatMaximize();
			return { consume: true };
		}
		if (ready && this.host.keybindings.matches(data, "editorMaximize")) {
			this.host.toggleEditorMaximize();
			return { consume: true };
		}
		if (!ready || this.host.activeRegion() !== "editor") return undefined;
		// Ctrl+Shift+A — attach the editor selection to the composer draft
		// (plan §20 explicit sharing). Delivered as a distinct CSI-u sequence
		// only under the Kitty keyboard protocol; legacy terminals send Ctrl+A,
		// which this never consumes (the editor's select-all stays intact).
		// /attach is the fallback everywhere.
		if (this.host.keybindings.matches(data, "attach")) {
			const result = this.host.attachSelectionToDraft();
			this.host.print(result.notice, result.ok ? "info" : "warning");
			return { consume: true };
		}
		// Ctrl+Space — completion at the cursor (explicit-only in v1; the
		// legacy NUL byte is accepted when the Kitty protocol is inactive).
		if (this.host.keybindings.matches(data, "complete")) {
			void this.host.startCompletion();
			return { consume: true };
		}
		// Alt+G/R: go to definition / find references.
		if (this.host.keybindings.matches(data, "references")) {
			void this.host.runDefinitionOrReferences("references");
			return { consume: true };
		}
		if (this.host.keybindings.matches(data, "definition")) {
			void this.host.runDefinitionOrReferences("definition");
			return { consume: true };
		}
		// Alt+M: rename preview (honest "unavailable" until supported).
		if (this.host.keybindings.matches(data, "rename")) {
			void this.host.runRenamePreview();
			return { consume: true };
		}
		// Ctrl+S — compare-and-swap save of the active buffer.
		if (this.host.keybindings.matches(data, "save")) {
			void this.host.saveActiveDocument();
			return { consume: true };
		}
		return undefined;
	}
}
