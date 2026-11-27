import assert from "node:assert/strict";
import { test } from "node:test";
import { AppKeybindings } from "../src/keybindings.ts";
import {
	KeyboardRouter,
	type InlineCompletionSession,
	type KeyboardHost,
} from "../src/app/keyboard.ts";
import type { ScreenView } from "../src/app/contracts.ts";
import type { FocusRegion } from "../src/workspace/state.ts";

// Real default bindings: the precedence chain is what this suite pins, not a
// synthetic key map. Alt+Left/Right are the documented Activity↔Chat↔Changes
// strip; plain arrows must stay with the focused editor.
const ALT_LEFT = "\x1b[1;3D";
const ALT_RIGHT = "\x1b[1;3C";
const PLAIN_LEFT = "\x1b[D";
const PLAIN_RIGHT = "\x1b[C";
const PLAIN_UP = "\x1b[A";
const ESC = "\x1b";
const ENTER = "\r";
const CTRL_P = "\x10";
const CTRL_S = "\x13";
const CTRL_W = "\x17";
const ALT_Q = "\x1bq";
const ALT_W = "\x1bw";
const ALT_E = "\x1be";
const ALT_1 = "\x1b1";
const ALT_2 = "\x1b2";
const ALT_3 = "\x1b3";

interface FakeState {
	screen: ScreenView;
	region: FocusRegion;
	dialogs: Array<{ hidden: boolean }>;
	quickOpen: boolean;
	commandRunning: boolean;
	commandRunningAt: number;
	approvals: number;
	running: boolean;
	pendingAbortEscAt: number;
	lastPrompt: string;
	editorText: string;
	autocomplete: boolean;
	familyCount: number;
	problemsOpen: boolean;
	completion: InlineCompletionSession | undefined;
	workspace: boolean;
	ready: boolean;
	layoutActive: boolean;
	unsupported: boolean;
	explorerRequested: boolean;
	selection: boolean;
}

const baseState: FakeState = {
	screen: "chat",
	region: "chat-composer",
	dialogs: [],
	quickOpen: false,
	commandRunning: false,
	commandRunningAt: 0,
	approvals: 0,
	running: false,
	pendingAbortEscAt: 0,
	lastPrompt: "",
	editorText: "",
	autocomplete: false,
	familyCount: 1,
	problemsOpen: false,
	completion: undefined,
	workspace: false,
	ready: false,
	layoutActive: false,
	unsupported: false,
	explorerRequested: false,
	selection: false,
};

function harness(overrides: Partial<FakeState> = {}) {
	const state: FakeState = { ...baseState, ...overrides };
	const calls: string[] = [];
	const editorInputs: string[] = [];
	const keybindings = new AppKeybindings();
	const footer = { keyHint: "" };
	const statusPage = {
		handleInput(data: string): boolean {
			calls.push(`statusPage:${data}`);
			return true;
		},
	};
	const changesPage = {
		handleInput(data: string): boolean {
			calls.push(`changesPage:${data}`);
			return true;
		},
	};
	const host: KeyboardHost = {
		tui: { requestRender() {} },
		editor: {
			handleInput(data) {
				editorInputs.push(data);
			},
			getText: () => state.editorText,
			setText(text) {
				state.editorText = text;
			},
			isShowingAutocomplete: () => state.autocomplete,
		},
		footer,
		transcript: {
			toggleLastTool() {
				calls.push("toggleLastTool");
				return false;
			},
		},
		keybindings,

		screen: () => state.screen,
		activeRegion: () => state.region,
		syncShell() {},
		syncWorkspaceShellIfChanged() {},
		scheduleWorkspaceChromeSync() {},
		reconcileFocus() {},
		statusPage: () => statusPage,
		changesPage: () => changesPage,

		dialogCount: () => state.dialogs.length,
		hasVisibleDialog: () => state.dialogs.some(dialog => !dialog.hidden),
		quickOpenActive: () => state.quickOpen,
		dismissQuickOpen() {
			calls.push("dismissQuickOpen");
			state.quickOpen = false;
		},
		commandRunning: () => state.commandRunning,
		commandRunningAt: () => state.commandRunningAt,

		approvalDialogCount: () => state.approvals,
		isRunning: () => state.running,
		pendingAbortEscAt: () => state.pendingAbortEscAt,
		armPendingAbortEsc() {
			state.pendingAbortEscAt = Date.now();
			calls.push("armPendingAbortEsc");
		},
		clearPendingAbortEsc() {
			state.pendingAbortEscAt = 0;
			calls.push("clearPendingAbortEsc");
		},
		lastPrompt: () => state.lastPrompt,
		hasPendingImageLoads: () => false,
		cancel() {
			calls.push("cancel");
		},
		exit() {
			calls.push("exit");
		},
		clear() {
			calls.push("clear");
		},

		openStatus() {
			state.screen = "status";
			calls.push("openStatus");
		},
		openScreen(screen) {
			state.screen = screen;
			calls.push(`openScreen:${screen}`);
		},
		closeScreen() {
			state.screen = "chat";
			calls.push("closeScreen");
		},
		selectNav(view) {
			calls.push(`selectNav:${view}`);
			if (view === "chat") state.screen = "chat";
			if (view === "activity") state.screen = "status";
			if (view === "changes") state.screen = "changes";
		},
		openShortcutReference() {
			calls.push("openShortcutReference");
			return { consume: true };
		},

		familyCount: () => state.familyCount,
		cycleFamily(direction) {
			calls.push(`cycleFamily:${direction}`);
		},
		closeFamilyCurrent() {
			calls.push("closeFamilyCurrent");
		},
		openSubagentMenu() {
			calls.push("openSubagentMenu");
		},
		pasteFromClipboard(imageOnly) {
			calls.push(`pasteFromClipboard:${imageOnly}`);
		},
		clearImages() {
			calls.push("clearImages");
		},
		completionSession: () => state.completion,
		activeEditorDocumentId: () => state.completion?.documentId,
		acceptCompletion() {
			calls.push("acceptCompletion");
		},
		clearCompletion() {
			calls.push("clearCompletion");
			state.completion = undefined;
		},
		problemsOpen: () => state.problemsOpen,
		closeProblems() {
			state.problemsOpen = false;
			calls.push("closeProblems");
		},

		hasWorkspace: () => state.workspace,
		workspaceReady: () => state.ready,
		workspaceLayoutActive: () => state.layoutActive,
		workspaceUnsupported: () => state.unsupported,
		explorerRequested: () => state.explorerRequested,
		setExplorerRequested(requested) {
			state.explorerRequested = requested;
			calls.push(`setExplorerRequested:${requested}`);
		},
		restoreSplit() {
			calls.push("restoreSplit");
		},
		toggleChatMaximize() {
			calls.push("toggleChatMaximize");
		},
		toggleEditorMaximize() {
			calls.push("toggleEditorMaximize");
		},
		editorHasSelection: () => state.selection,
		handleEditorInput(data) {
			editorInputs.push(data);
			calls.push(`handleEditorInput:${data}`);
		},

		openQuickOpen() {
			calls.push("openQuickOpen");
		},
		closeActiveWorkspaceTab() {
			calls.push("closeActiveWorkspaceTab");
		},
		cycleFocus(direction) {
			calls.push(`cycleFocus:${direction}`);
		},
		attachSelectionToDraft() {
			calls.push("attachSelectionToDraft");
			return { ok: true, notice: "attached" };
		},
		startCompletion() {
			calls.push("startCompletion");
		},
		runDefinitionOrReferences(operation) {
			calls.push(`runDefinitionOrReferences:${operation}`);
		},
		runRenamePreview() {
			calls.push("runRenamePreview");
		},
		saveActiveDocument() {
			calls.push("saveActiveDocument");
		},
		print(text, kind) {
			calls.push(`print:${kind ?? "info"}:${text}`);
		},
	};
	const router = new KeyboardRouter(host);
	return { router, state, calls, editorInputs, footer, keybindings };
}

test("a pending approval owns chat keys but the strip may still browse away", () => {
	const { router, state, calls } = harness({ approvals: 1 });

	// Decisions and editing belong to the approval card.
	assert.equal(router.handle("x"), undefined);
	assert.equal(router.handle(ENTER), undefined);
	assert.equal(router.handle(ESC), undefined);
	assert.equal(state.screen, "chat");
	assert.deepEqual(calls, []);

	// Navigation is the documented exception: browse Activity and come back.
	assert.deepEqual(router.handle(ALT_LEFT), { consume: true });
	assert.equal(state.screen, "status");
	assert.deepEqual(calls, ["openStatus"]);

	// On the page, Esc closes the page without touching the approval.
	assert.deepEqual(router.handle(ESC), { consume: true });
	assert.equal(state.screen, "chat");
	assert.deepEqual(calls, ["openStatus", "closeScreen"]);
});

test("dialog precedence: visible dialogs own keys, hidden ones free the strip", () => {
	const { router, state, calls } = harness({ dialogs: [{ hidden: false }] });

	// A visible dialog receives even a navigation chord.
	assert.equal(router.handle(ALT_LEFT), undefined);
	assert.equal(state.screen, "chat");
	assert.deepEqual(calls, []);

	// A dialog hidden behind the approval dock frees the strip.
	state.dialogs = [{ hidden: true }];
	assert.deepEqual(router.handle(ALT_LEFT), { consume: true });
	assert.equal(state.screen, "status");

	// A visible dialog on a page owns Esc; the page must not close underneath.
	state.dialogs = [{ hidden: false }];
	assert.equal(router.handle(ESC), undefined);
	assert.equal(state.screen, "status");

	// Hidden again: Esc belongs to the page.
	state.dialogs = [{ hidden: true }];
	assert.deepEqual(router.handle(ESC), { consume: true });
	assert.equal(state.screen, "chat");
});

test("Activity ← Chat → Changes walk one strip and Esc returns to Chat", () => {
	const { router, state, calls } = harness();

	assert.deepEqual(router.handle(ALT_LEFT), { consume: true });
	assert.equal(state.screen, "status");
	assert.deepEqual(router.handle(ALT_RIGHT), { consume: true });
	assert.equal(state.screen, "chat", "Alt+Right from Activity steps back to Chat");

	assert.deepEqual(router.handle(ALT_RIGHT), { consume: true });
	assert.equal(state.screen, "changes");
	assert.deepEqual(router.handle(ALT_LEFT), { consume: true });
	assert.equal(state.screen, "chat", "Alt+Left from Changes steps back to Chat");

	assert.deepEqual(router.handle("\x1ba"), { consume: true });
	assert.equal(state.screen, "status", "Alt+A still opens Activity");
	assert.deepEqual(router.handle(ESC), { consume: true });
	assert.equal(state.screen, "chat");

	state.screen = "changes";
	assert.deepEqual(router.handle(ESC), { consume: true });
	assert.equal(state.screen, "chat", "Esc closes the open page");
	assert.deepEqual(calls, [
		"openStatus",
		"closeScreen",
		"openScreen:changes",
		"closeScreen",
		"openStatus",
		"closeScreen",
		"closeScreen",
	]);
});

test("plain arrows and editor text never navigate the shell", () => {
	const { router, state, calls, editorInputs } = harness({ editorText: "draft" });

	assert.equal(router.handle(PLAIN_LEFT), undefined);
	assert.equal(router.handle(PLAIN_RIGHT), undefined);
	assert.equal(router.handle(PLAIN_UP), undefined);
	assert.equal(state.screen, "chat");
	assert.deepEqual(calls, [], "plain editing keys are forwarded, not consumed");
	assert.deepEqual(editorInputs, [], "the toolkit dispatches them to the focused editor");

	const editor = harness({
		editorText: "const value = 1;",
		region: "editor",
		workspace: true,
		ready: true,
		layoutActive: true,
	});
	assert.equal(editor.router.handle("j"), undefined);
	assert.equal(editor.router.handle("k"), undefined);
	assert.equal(editor.router.handle(PLAIN_LEFT), undefined);
	assert.equal(editor.router.handle(PLAIN_RIGHT), undefined);
	assert.equal(editor.state.screen, "chat");
	assert.deepEqual(editor.calls, []);
	assert.deepEqual(editor.editorInputs, []);
});

test("Alt+arrows navigate while Windows modifier encodings stay editor text", () => {
	const { router, state, calls, editorInputs } = harness();

	assert.deepEqual(router.handle(ALT_LEFT), { consume: true });
	assert.equal(state.screen, "status");
	state.screen = "chat";

	// Legacy ConPTY forms are rewritten to the canonical Ctrl/Shift arrows and
	// handed to the composer; they must never be read as Alt navigation.
	assert.deepEqual(router.handle("\x1b[5D"), { consume: true });
	assert.deepEqual(router.handle("\x1b[6C"), { consume: true });
	assert.equal(state.screen, "chat");
	assert.deepEqual(editorInputs, ["\x1b[1;5D", "\x1b[1;6C"]);
	assert.deepEqual(calls, ["openStatus"]);
});

test("workspace keys delegate: quick open, save, focus, panes and explorer", () => {
	const { router, state, calls } = harness({
		region: "editor",
		workspace: true,
		ready: true,
		layoutActive: true,
	});

	assert.deepEqual(router.handle(CTRL_P), { consume: true });
	assert.deepEqual(router.handle(CTRL_S), { consume: true });
	assert.deepEqual(router.handle(ALT_Q), { consume: true });
	assert.deepEqual(router.handle(ALT_W), { consume: true });
	assert.deepEqual(router.handle(ALT_E), { consume: true });
	assert.deepEqual(router.handle(CTRL_W), { consume: true });
	assert.deepEqual(router.handle(ALT_1), { consume: true });
	assert.deepEqual(router.handle(ALT_2), { consume: true });
	assert.deepEqual(router.handle(ALT_3), { consume: true });

	assert.equal(state.explorerRequested, true);
	assert.deepEqual(calls, [
		"openQuickOpen",
		"saveActiveDocument",
		"cycleFocus:1",
		"cycleFocus:-1",
		"setExplorerRequested:true",
		"closeActiveWorkspaceTab",
		"toggleChatMaximize",
		"toggleEditorMaximize",
		"restoreSplit",
	]);

	// The composer keeps Ctrl+W as delete-word-backward.
	state.region = "chat-composer";
	const before = calls.length;
	assert.equal(router.handle(CTRL_W), undefined);
	assert.equal(calls.length, before, "Ctrl+W is not stolen from the composer");

	// Quick Open owns every key except Esc/Ctrl+C.
	state.quickOpen = true;
	assert.equal(router.handle("x"), undefined);
	assert.deepEqual(router.handle(ESC), { consume: true });
	assert.equal(calls.at(-1), "dismissQuickOpen");
});

test("key releases and auto-repeat never leak into navigation or the editor", () => {
	const { router, state, calls, editorInputs } = harness({ running: true, pendingAbortEscAt: Date.now() });

	assert.deepEqual(router.handle("\x1b[97;1:3u"), { consume: true }, "a key release is swallowed");
	assert.deepEqual(calls, []);
	assert.deepEqual(editorInputs, []);

	// A repeated Escape must not cancel twice or re-arm the abort timer.
	assert.deepEqual(router.handle("\x1b[27;1:2u"), { consume: true });
	assert.deepEqual(calls, []);
	assert.equal(state.running, true);

	// A repeated Enter with an approval dock present stays with the dock.
	state.approvals = 1;
	assert.deepEqual(router.handle("\x1b[13;1:2u"), { consume: true });
	assert.equal(state.screen, "chat");
});
