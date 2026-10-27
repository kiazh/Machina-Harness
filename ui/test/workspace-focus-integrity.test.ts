// Focus-integrity regressions for the integrated workspace (findings 1–8):
// focus reconciliation when the layout hides a region, F6 with a single
// region, composer restores through the region router, completion dismissal
// and type-to-filter, the save-conflict close continuation, and non-blocking
// music detection at startup. The real App runs over the FakeTUI/FakeClient
// harness (same shape as workspace-app-integration.test.ts).

import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, type Component, type OverlayHandle, type SelectItem, type TUI } from "@earendil-works/pi-tui";
import { App } from "../src/app.ts";
import type { GatewayClient } from "../src/gateway.ts";
import type { GatewayEvent, SessionInfo } from "../src/protocol.ts";
import type { CompletionSession } from "../src/editor/intelligence.ts";
import type { WorkspaceEvent } from "../src/workspace/protocol.ts";

const SESSION_A: SessionInfo = {
	id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
	title: "session A",
	model: "m",
	provider: "openrouter",
	status: "active",
	lastActivity: null,
};

const WORKSPACE_IDENTITY = {
	workspaceId: "ws1",
	clientId: "cl1",
	epoch: 1,
	rootLabel: "ws",
	resolvedRoot: "/ws",
	trusted: true,
	capabilities: ["list", "read", "save", "documentOpen", "intelligence"],
};

// ─── FakeTUI/FakeClient harness (focus + RPC bookkeeping only) ─────────────

class FakeTUI implements TUI {
	children: Component[] = [];
	focused: Component | null = null;
	overlay: OverlayHandle | undefined;
	/** Last overlay component passed to showOverlay (dialog render assertions). */
	overlayComponent: Component | undefined;
	inputListeners: Array<(data: string) => void> = [];
	started = false;
	stopped = false;
	mode = "fullscreen" as const;
	terminal = { columns: 160, rows: 48 } as TUI["terminal"];
	fullRedraws = 0;
	clearOnShrink = false;

	addChild(c: Component): void {
		this.children.push(c);
	}
	removeChild(c: Component): void {
		const index = this.children.indexOf(c);
		if (index >= 0) this.children.splice(index, 1);
	}
	clear(): void {
		this.children = [];
	}
	setFocus(c: Component | null): void {
		this.focused = c;
	}
	showOverlay(_c: Component): OverlayHandle {
		this.overlayComponent = _c;
		const handle: OverlayHandle = {
			hide: () => {
				if (this.overlay === handle) this.overlay = undefined;
			},
			setHidden: () => {},
			isHidden: () => false,
			focus: () => {},
			unfocus: () => {},
			isFocused: () => false,
			getBounds: () => ({ row: 0, col: 0, width: 80, height: 24 }),
		};
		this.overlay = handle;
		return handle;
	}
	hideOverlay(): void {
		this.overlay = undefined;
	}
	hasOverlay(): boolean {
		return this.overlay !== undefined;
	}
	start(): void {
		this.started = true;
	}
	stop(): void {
		this.stopped = true;
	}
	requestRender(): void {}
	addInputListener(listener: (data: string) => void): () => void {
		this.inputListeners.push(listener);
		return () => {};
	}
	removeInputListener(): void {}
	onTerminalColorSchemeChange(): () => void {
		return () => {};
	}
	setTerminalColorSchemeNotifications(): void {}
	queryTerminalColors(): Promise<never> {
		throw new Error("not implemented");
	}
	render(): string[] {
		return [];
	}
	invalidate(): void {}
	handleMouse(): undefined {
		return undefined;
	}
	getShowHardwareCursor(): boolean {
		return false;
	}
	setShowHardwareCursor(): void {}
	getClearOnShrink(): boolean {
		return this.clearOnShrink;
	}
	setClearOnShrink(enabled: boolean): void {
		this.clearOnShrink = enabled;
	}
	renderNow(): void {}

	/** Deliver one key exactly like the TUI input funnel does. */
	press(data: string): void {
		for (const listener of this.inputListeners) listener(data);
	}
}

class FakeClient {
	onEvent: ((event: GatewayEvent) => void) | undefined;
	onExit: ((code: number | null, stderr: string[]) => void) | undefined;
	onWorkspaceEvent: ((event: WorkspaceEvent) => void) | undefined;
	requests: Array<{ method: string; params: Record<string, unknown> }> = [];
	responses = new Map<string, unknown>();
	handlers = new Map<string, (params: Record<string, unknown>) => unknown | Promise<unknown>>();
	stopped = false;

	start(): void {}
	stderr(): string[] {
		return [];
	}
	async stop(): Promise<void> {
		this.stopped = true;
	}

	request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
		this.requests.push({ method, params });
		const handler = this.handlers.get(method);
		if (handler) {
			return Promise.resolve().then(() => handler(params)) as Promise<T>;
		}
		const response = this.responses.get(method);
		if (response === undefined) return Promise.reject(new Error(`unexpected request ${method}`));
		return Promise.resolve(response as T);
	}

	emit(event: GatewayEvent): void {
		this.onEvent?.(event);
	}
}

function configureClient(client: FakeClient): void {
	client.responses.set("initialize", {
		version: "1.0.0",
		model: "m",
		provider: "openrouter",
		cwd: "/ws",
		branch: "main",
	});
	client.responses.set("session.create", { session: SESSION_A });
	client.responses.set("prompt.submit", { turnId: "t" });
	client.responses.set("prompt.cancel", {});
	client.responses.set("config.get", { config: { nowplaying_enabled: false }, secrets: [] });
	client.responses.set("config.set", { model: "m", provider: "openrouter", key: "workspace_ui_state", value: "" });
	client.responses.set("workspace.open", { ...WORKSPACE_IDENTITY });
	client.responses.set("workspace.documentOpen", { acceptedVersion: 0, serverGeneration: 1, state: "ready" });
	client.responses.set("workspace.documentChange", { acceptedVersion: 1 });
	client.responses.set("workspace.documentClose", { closed: true, uri: "" });
	client.responses.set("workspace.close", { closed: true });
	client.responses.set("workspace.save", { ok: true, revision: "rev-2", savedUiVersion: 0 });
	client.responses.set("workspace.lspStatus", { servers: [] });
	client.responses.set("workspace.list", { entries: [], nextCursor: null, exclusions: [], truncated: false });
	client.responses.set("workspace.quickOpen", {
		entries: [],
		total: 0,
		truncated: false,
		reasons: [],
		indexing: false,
		scanned: 0,
		stale: false,
		workspaceEpoch: 1,
		exclusions: [],
	});
	client.handlers.set("workspace.read", (params) => ({
		uri: String(params.uri),
		text: "hello\n",
		revision: "rev-1",
		languageId: "plaintext",
		lineEnding: "LF",
		encoding: "utf-8",
		readOnly: false,
		sizeBytes: 6,
	}));
}

function makeApp(): { app: App; tui: FakeTUI; client: FakeClient } {
	const tui = new FakeTUI();
	const client = new FakeClient();
	configureClient(client);
	const app = new App(tui, client as unknown as GatewayClient);
	return { app, tui, client };
}

/** Let the background workspace chain settle (all RPCs are immediate fakes). */
async function settle(times = 40): Promise<void> {
	for (let index = 0; index < times; index++) await new Promise((resolve) => setImmediate(resolve));
}

interface Integration {
	controller: { phase: string; noteFocus(region: "chat-composer"): void; getLayoutInput(): { requestedMode: string; explorerRequested: boolean } };
	store: {
		insertText(id: string, text: string): unknown;
		getDocument(id: string): { text: string; version: number; dirty: boolean } | undefined;
		getViewState(id: string): { cursorOffset: number } | undefined;
		documents(): Array<{ id: string; uri: string; dirty: boolean }>;
	};
	tabs: { activeDocumentId(): string | null; count(): number };
	pane: { focused: boolean; handleInput(data: string): boolean; handleIntelligenceKey(data: string): string; render(width: number): string[]; invalidate(): void };
	view: { setView(state: unknown): void };
}

interface Internals {
	integration?: Integration;
	workspaceLayout?: { effectiveMode: string; editor?: { columns: number }; explorer?: { columns: number } };
	workspaceLayoutActive: boolean;
	activeRegion: string;
	completionSession?: CompletionSession;
	quickOpenPane?: unknown;
	exiting: boolean;
	editor: Component;
	dialogs: Array<{ cancel(): void }>;
	openWorkspacePath(path: string, options?: { preview?: boolean }): Promise<void>;
	noteRegionFocus(region: string): void;
	workspaceGeneration: number;
	isWorkspaceCurrent(gen: number): boolean;
	switchWorkspaceRoot(path: string): Promise<{ ok: boolean; message: string }>;
	closeWorkspaceTab(documentId: string): Promise<void>;
	pick: (title: string, items: SelectItem[]) => Promise<SelectItem | undefined>;
}

function internals(app: App): Internals {
	return app as unknown as Internals;
}

function workspaceOpened(app: App): boolean {
	return internals(app).integration !== undefined && internals(app).workspaceLayoutActive;
}

async function waitForWorkspace(app: App): Promise<void> {
	for (let index = 0; index < 200 && !workspaceOpened(app); index++) {
		await new Promise((resolve) => setImmediate(resolve));
	}
	assert.ok(workspaceOpened(app), "workspace opened in the background");
}

/** Script `pick` answers by value; every call consumes one entry. */
function scriptPicks(app: App, answers: Array<string | undefined>): void {
	(app as unknown as {
		pick: (title: string, items: SelectItem[]) => Promise<SelectItem | undefined>;
	}).pick = async () => {
		const value = answers.shift();
		return value === undefined ? undefined : { value, label: value };
	};
}

async function openEditorFile(app: App): Promise<{ integration: Integration; documentId: string }> {
	await internals(app).openWorkspacePath("src/a.ts");
	const integration = internals(app).integration!;
	const documentId = integration.tabs.activeDocumentId()!;
	assert.ok(documentId, "the document is open");
	return { integration, documentId };
}

const CtrlLeft = "\x1b[1;5D";
function pressGuide(tui: { press(data: string): void }, key: string): void {
	tui.press("\x0e");
	tui.press(key);
}
const ESC = "\x1b";
const ENTER = "\r";
const CTRL_W = "\x17";
const CTRL_T = "\x14";
const CTRL_P = "\x10";
const CTRL_SPACE = "\x00";

// ─── finding 1 + 2: focus reconciliation and single-region cycling ─────────

test("maximizing chat while the editor owns focus reconciles the region to the composer", async () => {
	const { app, tui } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	const { integration } = await openEditorFile(app);
	assert.equal(internals(app).activeRegion, "editor");
	assert.equal(tui.focused, integration.pane, "the pane owns keys while editing");

	pressGuide(tui, "c"); // F8: maximize chat
	assert.equal(internals(app).workspaceLayout?.effectiveMode, "chat");
	assert.equal(internals(app).workspaceLayout?.editor, undefined, "the editor region is hidden");
	assert.equal(internals(app).activeRegion, "chat-composer", "focus reconciled to the only visible region");
	assert.equal(tui.focused, internals(app).editor, "TUI focus left the unmounted pane");

	// The editor-scoped gates now read the composer: Ctrl+W must never close
	// the tab of a pane that no longer receives keys.
	tui.press(CTRL_W);
	assert.equal(integration.tabs.count(), 1, "Ctrl+W in the composer does not close the hidden editor's tab");

	// Ctrl+Left restores the split even after focus moves to the composer.
	pressGuide(tui, "s");
	assert.equal(internals(app).workspaceLayout?.effectiveMode, "split", "composer Ctrl+Left restores the split from maximized chat");
	await app.exit();
});

test("Ctrl+N then N re-asserts focus when only the composer remains visible", async () => {
	const { app, tui } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	await openEditorFile(app);

	// Close the explorer so a chat maximize leaves a single-region cycle.
	tui.press(CTRL_T);
	assert.equal(internals(app).workspaceLayout?.explorer, undefined, "the explorer drawer is closed");
	pressGuide(tui, "c");
	assert.equal(internals(app).workspaceLayout?.effectiveMode, "chat");
	assert.equal(internals(app).activeRegion, "chat-composer");

	// Simulate focus drift (a losing component cleared it); the shortcut hands the
	// only visible region its focus instead of early-returning.
	tui.setFocus(null);
	pressGuide(tui, "n");
	assert.equal(internals(app).activeRegion, "chat-composer");
	assert.equal(tui.focused, internals(app).editor, "focus shortcut re-asserted composer focus");
	await app.exit();
});

// ─── finding 3: restores go through the region router ──────────────────────

test("quick open dismiss restores the composer as the recorded region", async () => {
	const { app, tui } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	await openEditorFile(app);
	assert.equal(internals(app).activeRegion, "editor");

	tui.press(CTRL_P);
	await settle();
	assert.ok(internals(app).quickOpenPane, "the overlay is up");
	tui.press(ESC);
	await settle();
	assert.equal(internals(app).quickOpenPane, undefined);
	assert.equal(internals(app).activeRegion, "chat-composer", "the dismiss updated the recorded region");
	assert.equal(tui.focused, internals(app).editor);

	// Composer gates hold after the restore: Ctrl+Left keeps its word motion
	// and never touches the split.
	tui.press(CtrlLeft);
	assert.equal(internals(app).workspaceLayout?.effectiveMode, "split", "the panes key stays editor-gated");
	await app.exit();
});

test("closing the last dialog restores the composer as the recorded region", async () => {
	const { app, tui } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	await openEditorFile(app);
	pressGuide(tui, "n"); // editor → explorer
	assert.equal(internals(app).activeRegion, "explorer");

	pressGuide(tui, "h"); // shortcuts picker dialog
	assert.equal(internals(app).dialogs.length, 1, "a dialog is open");
	internals(app).dialogs[0]!.cancel();
	await settle();
	assert.equal(internals(app).dialogs.length, 0);
	assert.equal(internals(app).activeRegion, "chat-composer", "the dialog close reconciled the region");
	assert.equal(tui.focused, internals(app).editor);
	await app.exit();
});

test("releasing the last approval restores the composer as the recorded region", async () => {
	const { app, tui, client } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	await openEditorFile(app);
	assert.equal(internals(app).activeRegion, "editor");

	await app.submitTurn("run the reviewed operation");
	client.emit({
		type: "needs_approval",
		protocolVersion: 2,
		sessionId: SESSION_A.id,
		turnId: "t",
		requestId: "req-1",
		operation: "write_file",
		tool: "write_file",
		target: "a.ts",
		argv: [],
		cwd: "/ws",
		backend: "local",
		capabilities: ["file.write"],
	} as GatewayEvent);
	assert.equal(internals(app).activeRegion, "editor", "the card does not rewrite the region by itself");

	client.emit({
		type: "approval.resolved",
		protocolVersion: 2,
		sessionId: SESSION_A.id,
		turnId: "t",
		requestId: "req-1",
		status: "denied",
	} as GatewayEvent);
	assert.equal(internals(app).activeRegion, "chat-composer", "the dock release reconciled the region");
	assert.equal(tui.focused, internals(app).editor);
	await app.exit();
});

// ─── finding 4: completion never consumes keys outside the editor ──────────

function completionItems() {
	return [
		{ label: "alpha", textEdit: { range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } }, newText: "alpha" } },
		{ label: "beta", textEdit: { range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } }, newText: "beta" } },
	];
}

function armCompletion(client: FakeClient): void {
	client.handlers.set("workspace.intelligence", (params) => ({
		workspaceId: "ws1",
		source: "editor-buffer",
		uri: params.uri,
		documentVersion: params.version,
		serverGeneration: 1,
		workspaceEpoch: 1,
		operation: params.operation,
		state: "ready",
		freshness: "versioned",
		truncated: false,
		result: { items: completionItems().map((item) => ({ ...item, textEdit: { ...item.textEdit, range: { start: { line: 0, character: 0 }, end: params.position } } })) },
	}));
}

test("moving focus off the editor dismisses the inline completion", async () => {
	const { app, tui, client } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	await openEditorFile(app);
	armCompletion(client);
	tui.press(CTRL_SPACE);
	await settle();
	assert.ok(internals(app).completionSession, "the suggestion is active in the editor");

	pressGuide(tui, "n"); // editor → explorer: the list's region loses focus
	assert.equal(internals(app).activeRegion, "explorer");
	assert.equal(internals(app).completionSession, undefined, "the editor-scoped suggestion was dismissed");
	await app.exit();
});

test("an inline completion never consumes keys after the composer takes focus", async () => {
	const { app, tui, client } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	const { integration, documentId } = await openEditorFile(app);
	armCompletion(client);
	tui.press(CTRL_SPACE);
	await settle();
	assert.ok(internals(app).completionSession, "the suggestion is active in the editor");

	// Focus moved without the region seam (an overlay restore, a mouse path):
	// the TUI now targets the composer while activeRegion still says editor.
	tui.setFocus(internals(app).editor);
	tui.press(ENTER);
	await settle();
	assert.equal(internals(app).activeRegion, "chat-composer", "the region was reconciled from real focus");
	assert.equal(internals(app).completionSession, undefined, "the orphaned suggestion was dismissed");
	assert.equal(integration.store.getDocument(documentId)?.text, "hello\n", "nothing was applied by the stale popup");
	await app.exit();
});

// ─── finding 6: type-to-filter for completions ─────────────────────────────

for (const action of ["tab", "enter", "escape", "motion"] as const) {
	test(`automatic ghost text has no menu and handles ${action}`, async () => {
		const { app, tui, client } = makeApp();
		client.handlers.set("workspace.read", (params) => ({
			uri: String(params.uri), text: "", revision: "r", languageId: "typescript",
			lineEnding: "LF", encoding: "utf-8", readOnly: false, sizeBytes: 0,
		}));
		client.handlers.set("workspace.documentChange", (params) => ({ acceptedVersion: params.version }));
		armCompletion(client);
		await app.start();
		try {
			await waitForWorkspace(app);
			const { integration, documentId } = await openEditorFile(app);
			client.onWorkspaceEvent?.({ type: "workspace.lspState", workspaceId: "ws1", epoch: 1,
				profile: "typescript-language-server", state: "ready", generation: 1 });
			for (const key of ["a", "l"]) {
				tui.press(key);
				integration.pane.handleInput(key);
			}
			await new Promise((resolve) => setTimeout(resolve, 260));
			await settle();
			assert.equal(internals(app).completionSession?.inlinePreview(), "pha");
			assert.equal((app as unknown as { completionPopup?: unknown }).completionPopup, undefined);
			integration.pane.focused = true; // FakeTUI does not set Focusable.focused.
			assert.ok(integration.pane.render(80).some((row) => stripTerminalSequences(row).includes("alpha")));
			assert.equal(integration.store.getDocument(documentId)?.text, "al");
			const key = action === "tab" ? "\t" : action === "enter" ? ENTER : action === "escape" ? ESC : "\x1b[D";
			tui.press(key);
			if (action === "enter" || action === "motion") integration.pane.handleInput(key);
			assert.equal(internals(app).completionSession, undefined);
			assert.equal(integration.store.getDocument(documentId)?.text,
				action === "tab" ? "alpha" : action === "enter" ? "al\n" : "al");
			if (action === "tab") {
				integration.pane.handleInput("\x1a"); // undo the single accepted edit
				assert.equal(integration.store.getDocument(documentId)?.text, "al");
			}
		} finally {
			scriptPicks(app, ["discard"]);
			await app.exit();
		}
	});
}

// ─── finding 7: save-and-close survives a save conflict ────────────────────

test("save-and-close finishes after a conflict is overwritten", async () => {
	const { app, tui, client } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	const { integration, documentId } = await openEditorFile(app);
	integration.store.insertText(documentId, "dirty");
	assert.equal(integration.store.getDocument(documentId)?.dirty, true);

	let saves = 0;
	client.handlers.set("workspace.save", () => {
		saves += 1;
		if (saves === 1) {
			return { conflict: { diskRevision: "rev-2", baseRevision: "rev-1", reason: "disk changed" } };
		}
		return { ok: true, revision: "rev-3" };
	});
	scriptPicks(app, ["save", "overwrite-explicit"]);

	tui.press(CTRL_W); // editor region owns Ctrl+W
	await settle();
	await settle();
	assert.equal(saves, 2, "the conflict was overwritten explicitly");
	assert.equal(integration.tabs.count(), 0, "the user's close request survived the detour");
	assert.equal(integration.store.getDocument(documentId), undefined, "the buffer was released");
	await app.exit();
});

// ─── focus reconciliation across a pane unmount ─────────────────────────────

test("a hidden editor cannot keep keys: drift is reconciled before Ctrl+S saves", async () => {
	const { app, tui, client } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	const { integration, documentId } = await openEditorFile(app);
	integration.store.insertText(documentId, "dirty");
	assert.equal(integration.store.getDocument(documentId)?.dirty, true);

	pressGuide(tui, "c"); // F8: maximize chat, unmount the pane
	assert.equal(internals(app).workspaceLayout?.editor, undefined);

	// Two independent drift sources: the TUI still targets the removed pane,
	// and a late mouse seam reports the now-invisible editor region.
	tui.setFocus(integration.pane);
	internals(app).noteRegionFocus("editor");
	assert.equal(internals(app).activeRegion, "chat-composer", "an invisible region is clamped immediately");

	const savesBefore = client.requests.filter((entry) => entry.method === "workspace.save").length;
	tui.press("\x13"); // Ctrl+S
	await settle();
	assert.equal(internals(app).activeRegion, "chat-composer");
	assert.equal(tui.focused, internals(app).editor, "keys were handed to the composer, not the dead pane");
	assert.equal(
		client.requests.filter((entry) => entry.method === "workspace.save").length,
		savesBefore,
		"Ctrl+S in the composer never saves the hidden buffer",
	);
	// The buffer is (intentionally) still dirty; answer the quit review.
	scriptPicks(app, ["discard"]);
	await app.exit();
});

// ─── save-conflict continuations survive (and are fenced) ───────────────────

test("save-all-and-exit continues after a conflict is overwritten", async () => {
	const { app, tui, client } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	const { integration, documentId } = await openEditorFile(app);
	integration.store.insertText(documentId, "dirty");

	let saves = 0;
	client.handlers.set("workspace.save", () => {
		saves += 1;
		if (saves === 1) {
			return { conflict: { diskRevision: "rev-2", baseRevision: "rev-1", reason: "disk changed" } };
		}
		return { ok: true, revision: "rev-3" };
	});
	scriptPicks(app, ["save", "overwrite-explicit"]);

	await app.exit();
	assert.equal(saves, 2, "the conflict was overwritten explicitly");
	assert.equal(tui.stopped, true, "the requested exit completed after the detour");
});

test("a root switch retires a parked dirty-close conflict (no stale write)", async () => {
	const { app, tui, client } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	const { integration, documentId } = await openEditorFile(app);
	integration.store.insertText(documentId, "dirty");

	let saves = 0;
	client.handlers.set("workspace.save", () => {
		saves += 1;
		if (saves === 1) {
			return { conflict: { diskRevision: "rev-2", baseRevision: "rev-1", reason: "disk changed" } };
		}
		return { ok: true, revision: "rev-3" };
	});
	let releaseConflict: (() => void) | undefined;
	const answers: Array<string | undefined> = ["save"];
	internals(app).pick = async () => {
		const next = answers.shift();
		if (next !== undefined) return { value: next, label: next };
		return new Promise<SelectItem | undefined>((resolve) => {
			releaseConflict = () => resolve({ value: "overwrite-explicit", label: "Overwrite" });
		});
	};

	tui.press(CTRL_W);
	await settle();
	await settle();
	assert.equal(saves, 1, "the first save hit the conflict");
	assert.ok(releaseConflict, "the conflict picker is parked");

	await internals(app).switchWorkspaceRoot("/other");
	assert.ok(internals(app).workspaceGeneration >= 2, "the switch became the live lifecycle entry");
	releaseConflict!();
	await settle();
	await settle();
	assert.equal(saves, 1, "the retired close never wrote into the successor workspace");
	// The dirty buffer is retained across the switch; answer the quit review.
	scriptPicks(app, ["discard"]);
	await app.exit();
});

// ─── background restore focus ──────────────────────────────────────────────

test("a background restore opens remembered files without stealing composer focus", async () => {
	const { app, tui, client } = makeApp();
	const state = JSON.stringify({
		version: 1,
		byRoot: {
			"/ws": {
				version: 1,
				requestedMode: "split",
				splitRatio: 0.5,
				explorerRequested: false,
				openUris: ["file:///ws/remembered.ts"],
				activeUri: "file:///ws/remembered.ts",
			},
		},
	});
	client.handlers.set("config.get", () => ({
		config: { nowplaying_enabled: false, workspace_ui_state: state },
		secrets: [],
	}));
	await app.start();
	await waitForWorkspace(app);
	await settle();
	const integration = internals(app).integration!;
	assert.equal(integration.tabs.count(), 1, "the remembered file reopened");
	assert.equal(internals(app).activeRegion, "chat-composer", "the composer still owns the region");
	assert.equal(tui.focused, internals(app).editor, "the restored tab never steals focus");
	await app.exit();
});

// ─── dialog chrome sanitization ─────────────────────────────────────────────

test("a hostile dialog title cannot carry bidi/controls into the rendered menu", async () => {
	const { app, tui } = makeApp();
	await app.start();
	await waitForWorkspace(app);
	const hostile = "\u202Eevil\u0007\u001b]0;owned\u001b\\";
	const pending = internals(app).pick(`Save ${hostile}.py`, [
		{ value: "ok", label: `row ${hostile}`, description: `desc ${hostile}\x1b[31mred` },
	]);
	await settle();
	const overlay = tui.overlayComponent;
	assert.ok(overlay, "the dialog rendered");
	const rendered = overlay.render(80).join("\n");
	assert.ok(!/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/.test(rendered), "no bidi/isolate control survives");
	assert.ok(!rendered.includes("\x0e"), "no BEL survives");
	assert.ok(!rendered.includes("[31m"), "an SGR payload in a label is stripped, not painted");
	assert.match(rendered, /Save evil/, "the honest visible title remains");
	internals(app).dialogs[0]!.cancel();
	await pending;
	await app.exit();
});

// ─── finding 8: music detection never blocks session creation ──────────────

test("startup creates the session before music detection resolves", async () => {
	const { app, client } = makeApp();
	let releaseMusic: (() => void) | undefined;
	client.handlers.set("config.get", () => ({ config: { nowplaying_enabled: true }, secrets: [] }));
	client.handlers.set("nowplaying.get", () => new Promise((resolve) => {
		releaseMusic = () => resolve({ available: true, state: null, title: null, error: null });
	}));

	const started = app.start();
	await settle();
	assert.ok(
		client.requests.some((entry) => entry.method === "session.create"),
		"the session was created while music detection was still pending",
	);
	releaseMusic?.();
	await started;
	await app.exit();
});

test("Ctrl+Left keeps composer word motion when a requested split renders as chat", async () => {
	const { app, tui } = makeApp();
	await app.start();
	try {
		await waitForWorkspace(app);
		await openEditorFile(app);
		const state = internals(app);
		tui.setFocus(state.editor);
		state.noteRegionFocus("chat-composer");
		state.integration!.controller.noteFocus("chat-composer");
		tui.terminal = { columns: 60, rows: 48 } as TUI["terminal"];
		const keys = app as unknown as {
			syncWorkspaceShell(): void;
			handleWorkspaceKey(data: string): { consume: boolean } | undefined;
		};
		keys.syncWorkspaceShell();
		assert.equal(state.integration!.controller.getLayoutInput().requestedMode, "split");
		assert.equal(state.workspaceLayout?.effectiveMode, "chat");
		assert.equal(state.activeRegion, "chat-composer");
		assert.equal(keys.handleWorkspaceKey(CtrlLeft), undefined, "word-left reaches the composer");
	} finally {
		await app.exit();
	}
});

test("ready events replace an earlier starting status without a manual status command", async () => {
	const { app, client } = makeApp();
	client.handlers.set("workspace.read", (params) => ({
		uri: String(params.uri), text: "hello\n", revision: "r", languageId: "typescript",
		lineEnding: "LF", encoding: "utf-8", readOnly: false, sizeBytes: 6,
	}));
	await app.start();
	try {
		await waitForWorkspace(app);
		await openEditorFile(app);
		client.responses.set("workspace.lspStatus", { servers: [{
			profile: "typescript-language-server", languageId: "typescript", state: "starting", generation: 1,
		}] });
		const controller = internals(app).integration!.controller as unknown as {
			lspStatus(): Promise<unknown>;
		};
		await controller.lspStatus();
		const status = app as unknown as { lspStatusText(): string };
		assert.equal(status.lspStatusText(), "starting…");
		client.onWorkspaceEvent?.({
			type: "workspace.lspState", workspaceId: "ws1", epoch: 1,
			profile: "typescript-language-server", state: "ready", generation: 1,
		});
		assert.equal(status.lspStatusText(), "ready");
	} finally {
		await app.exit();
	}
});

for (const leaveEditor of [false, true]) {
	test(`typing ${leaveEditor ? "then leaving editor cancels" : "opens"} automatic completion`, async () => {
		const { app, tui, client } = makeApp();
		client.handlers.set("workspace.read", (params) => ({
			uri: String(params.uri), text: "", revision: "r", languageId: "typescript",
			lineEnding: "LF", encoding: "utf-8", readOnly: false, sizeBytes: 0,
		}));
		client.handlers.set("workspace.documentChange", (params) => ({ acceptedVersion: params.version }));
		armCompletion(client);
		await app.start();
		try {
			await waitForWorkspace(app);
			const { integration, documentId } = await openEditorFile(app);
			client.onWorkspaceEvent?.({
				type: "workspace.lspState", workspaceId: "ws1", epoch: 1,
				profile: "typescript-language-server", state: "ready", generation: 1,
			});
			const pane = integration.pane as unknown as { handleInput(data: string): boolean };
			for (const key of ["a", "l"]) {
				tui.press(key);
				pane.handleInput(key);
			}
			if (leaveEditor) pressGuide(tui, "n");
			await new Promise((resolve) => setTimeout(resolve, 220));
			await settle();
			const calls = client.requests.filter((r) => r.method === "workspace.intelligence" && r.params.operation === "completion");
			assert.equal(calls.length, leaveEditor ? 0 : 1);
			if (!leaveEditor) {
				assert.equal(internals(app).completionSession?.state, "visible");
				assert.deepEqual(internals(app).completionSession?.items().map((item) => item.label), ["alpha"]);
			}
			assert.equal(integration.store.getDocument(documentId)?.text, "al");
		} finally {
			scriptPicks(app, ["discard"]);
			await app.exit();
		}
	});
}

test("explorer mouse previews preserve explorer focus and keep Problems open", async () => {
	const { app } = makeApp();
	await app.start();
	try {
		await waitForWorkspace(app);
		await openEditorFile(app);
		const state = app as unknown as {
			openProblems(): void;
			setFocusForRegion(region: string): void;
			problemsOpen: boolean;
			activeRegion: string;
			integration: { explorerPane: { onPreviewFile(entry: unknown, focus: boolean): void } };
		};
		state.openProblems();
		state.setFocusForRegion("explorer");
		state.integration.explorerPane.onPreviewFile({ kind: "file", name: "b.ts", path: "b.ts" }, false);
		await settle();
		assert.equal(state.activeRegion, "explorer");
		assert.equal(state.problemsOpen, true);
	} finally {
		await app.exit();
	}
});

test("the editor completion command returns keyboard focus to its suggestions", async () => {
	const { app, tui, client } = makeApp();
	await app.start();
	try {
		await waitForWorkspace(app);
		await openEditorFile(app);
		armCompletion(client);
		tui.setFocus(internals(app).editor);
		internals(app).noteRegionFocus("chat-composer");
		app.workspace()?.completeAtCursor?.();
		await settle();
		assert.equal(internals(app).activeRegion, "editor");
		assert.equal(internals(app).completionSession?.state, "visible");
	} finally {
		await app.exit();
	}
});

test("a late completion response cannot resurrect ghost text after cursor motion", async () => {
	const { app, tui, client } = makeApp();
	let reply!: (value: unknown) => void;
	client.handlers.set("workspace.intelligence", () => new Promise((resolve) => { reply = resolve; }));
	await app.start();
	try {
		await waitForWorkspace(app);
		const { integration } = await openEditorFile(app);
		tui.press(CTRL_SPACE);
		await settle();
		tui.press("\x1b[C");
		integration.pane.handleInput("\x1b[C");
		reply({ workspaceId: "ws1", source: "editor-buffer", uri: "file:///ws/src/a.ts",
			documentVersion: 0, serverGeneration: 1, workspaceEpoch: 1,
			operation: "completion", state: "ready", freshness: "versioned", truncated: false,
			result: { items: [{ label: "alpha" }] } });
		await settle();
		assert.equal(internals(app).completionSession, undefined);
	} finally {
		reply?.({});
		await app.exit();
	}
});

for (const [text, expected] of [["a", 0], ["// alpha", 0], ['"alpha', 0], ["user.", 1]] as const) {
	test(`automatic requests are restrained for ${JSON.stringify(text)}`, async () => {
		const { app, tui, client } = makeApp();
		client.handlers.set("workspace.read", (params) => ({
			uri: String(params.uri), text: "", revision: "r", languageId: "typescript",
			lineEnding: "LF", encoding: "utf-8", readOnly: false, sizeBytes: 0,
		}));
		client.handlers.set("workspace.documentChange", (params) => ({ acceptedVersion: params.version }));
		armCompletion(client);
		await app.start();
		try {
			await waitForWorkspace(app);
			const { integration } = await openEditorFile(app);
			client.onWorkspaceEvent?.({ type: "workspace.lspState", workspaceId: "ws1", epoch: 1,
				profile: "typescript-language-server", state: "ready", generation: 1 });
			for (const key of text) {
				tui.press(key);
				integration.pane.handleInput(key);
			}
			await new Promise((resolve) => setTimeout(resolve, 260));
			await settle();
			assert.equal(client.requests.filter((r) => r.method === "workspace.intelligence"
				&& r.params.operation === "completion").length, expected);
		} finally {
			scriptPicks(app, ["discard"]);
			await app.exit();
		}
	});
}

test("switching files retires the previous inline completion before Tab", async () => {
	const { app, tui, client } = makeApp();
	armCompletion(client);
	await app.start();
	try {
		await waitForWorkspace(app);
		const { integration, documentId } = await openEditorFile(app);
		tui.press(CTRL_SPACE);
		await settle();
		assert.ok(internals(app).completionSession);
		await internals(app).openWorkspacePath("src/b.ts");
		assert.equal(internals(app).completionSession, undefined);
		tui.press("\t");
		assert.equal(integration.store.getDocument(documentId)?.text, "hello\n");
	} finally {
		scriptPicks(app, ["discard"]);
		await app.exit();
	}
});

test("Python ghost completion inserts a call with the cursor ready for arguments", async () => {
	const { app, tui, client } = makeApp();
	client.handlers.set("workspace.read", (params) => ({
		uri: String(params.uri), text: "pri", revision: "r", languageId: "python",
		lineEnding: "LF", encoding: "utf-8", readOnly: false, sizeBytes: 3,
	}));
	client.handlers.set("workspace.intelligence", (params) => ({
		workspaceId: "ws1", source: "editor-buffer", uri: params.uri, documentVersion: params.version,
		serverGeneration: 1, workspaceEpoch: 1, operation: "completion", state: "ready", freshness: "versioned",
		truncated: false, result: { items: [{ label: "print", kind: 3 }] },
	}));
	await app.start();
	try {
		await waitForWorkspace(app);
		const { integration, documentId } = await openEditorFile(app);
		integration.pane.handleInput("\x1b[F"); // End
		tui.press(CTRL_SPACE);
		await settle();
		assert.equal(internals(app).completionSession?.inlinePreview(), "nt()");
		tui.press("\t");
		assert.equal(integration.store.getDocument(documentId)?.text, "print()");
		assert.equal(integration.store.getViewState(documentId)?.cursorOffset, 6);
		integration.pane.handleInput('"hello"');
		assert.equal(integration.store.getDocument(documentId)?.text, 'print("hello")');
	} finally {
		scriptPicks(app, ["discard"]);
		await app.exit();
	}
});

test("Python block indentation and call completion work together", async () => {
	const { app, tui, client } = makeApp();
	client.handlers.set("workspace.read", (params) => ({
		uri: String(params.uri), text: "if ready:", revision: "r", languageId: "python",
		lineEnding: "LF", encoding: "utf-8", readOnly: false, sizeBytes: 9,
	}));
	client.handlers.set("workspace.documentChange", (params) => ({ acceptedVersion: params.version }));
	client.handlers.set("workspace.intelligence", (params) => ({
		workspaceId: "ws1", source: "editor-buffer", uri: params.uri, documentVersion: params.version,
		serverGeneration: 1, workspaceEpoch: 1, operation: "completion", state: "ready", freshness: "versioned",
		truncated: false, result: { items: [{ label: "print", kind: 3 }] },
	}));
	await app.start();
	try {
		await waitForWorkspace(app);
		await internals(app).openWorkspacePath("src/main.py");
		const integration = internals(app).integration!;
		const id = integration.tabs.activeDocumentId()!;
		integration.pane.handleInput("\x1b[F"); // End
		integration.pane.handleInput("\r");
		assert.equal(integration.store.getDocument(id)?.text, "if ready:\n    ");
		integration.pane.handleInput("pri");
		tui.press(CTRL_SPACE);
		await settle();
		assert.equal(internals(app).completionSession?.inlinePreview(), "nt()");
		tui.press("\t");
		integration.pane.handleInput("42");
		assert.equal(integration.store.getDocument(id)?.text, "if ready:\n    print(42)");
	} finally {
		scriptPicks(app, ["discard"]);
		await app.exit();
	}
});
