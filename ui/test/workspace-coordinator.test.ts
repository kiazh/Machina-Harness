// Workspace coordinator contract tests.
//
// The host is a fake composition root; the integration keeps a REAL EditorStore
// (completion sessions own buffer state) while the controller, view, pane and
// diagnostics model are narrow fakes. Every case asserts a behavior the moved
// App code had: focus geometry, save/conflict sequencing, Quick Open request
// binding, completion acceptance, and the LSP status projection.

import assert from "node:assert/strict";
import { test } from "node:test";
import type { Component, Editor, SelectItem, TUI } from "@earendil-works/pi-tui";
import type { AppShell } from "../src/components/AppShell.ts";
import {
	WorkspaceCoordinator,
	type WorkspaceControllerPort,
	type WorkspaceEditorIntegration,
	type WorkspaceHost,
	type WorkspacePanePort,
	type WorkspaceViewPort,
} from "../src/app/workspace.ts";
import { EditorStore, type DocumentRecord, type EditorViewState } from "../src/editor/model.ts";
import type { IntelligenceOperation } from "../src/editor/intelligence.ts";
import type {
	CloseOutcome,
	ConflictResolution,
	IntelligenceOutcome,
	OpenUriOutcome,
	SaveOutcome,
	WorkspaceLspStatusSnapshot,
	WorkspacePhase,
} from "../src/workspace/controller.ts";
import type { FocusRegion, LayoutInput, LayoutResult } from "../src/workspace/state.ts";
import type {
	LspPosition,
	WorkspaceIntelligenceResult,
	WorkspaceOpenResult,
	WorkspaceQuickOpenResult,
} from "../src/workspace/protocol.ts";

// ─── fixtures ──────────────────────────────────────────────────────────────

const URI = "file:///w/src/main.ts";
const IDENTITY: WorkspaceOpenResult = {
	workspaceId: "ws-1",
	clientId: "client-1",
	epoch: 3,
	rootLabel: "w",
	resolvedRoot: "/w",
	trusted: true,
	capabilities: [],
};

const CHAT_ONLY: LayoutResult = { effectiveMode: "chat", explorerOverlay: false };
const SPLIT: LayoutResult = {
	effectiveMode: "split",
	chat: { x: 0, y: 0, columns: 60, rows: 20 },
	editor: { x: 61, y: 0, columns: 59, rows: 20 },
	explorer: { x: 92, y: 0, columns: 28, rows: 16 },
	explorerOverlay: false,
};

function component(name: string): Component {
	return { render: () => [name], invalidate() {} };
}

function openDocument(store: EditorStore, text = "const x = 1;"): DocumentRecord {
	const result = store.openDocument({
		workspaceId: "ws-1",
		uri: URI,
		text,
		languageId: "typescript",
		revision: "rev-1",
	});
	assert.ok(result.ok, "document should open");
	return result.document;
}

interface ControllerCalls {
	saved: string[];
	resolved: Array<[string, string]>;
	edits: string[];
	focus: FocusRegion[];
	opened: string[];
}

interface ControllerHarness {
	controller: WorkspaceControllerPort;
	calls: ControllerCalls;
}

interface ControllerOptions {
	identity?: WorkspaceOpenResult;
	phase?: WorkspacePhase;
	save?(documentId: string): Promise<SaveOutcome>;
	resolveConflict?(documentId: string, choice: string): Promise<ConflictResolution>;
	request?(
		operation: IntelligenceOperation,
		uri: string,
		version: number | null,
		position?: LspPosition,
	): Promise<IntelligenceOutcome>;
	lspStatusSnapshot?(): WorkspaceLspStatusSnapshot;
	openUri?(uri: string): Promise<OpenUriOutcome>;
}

function controllerHarness(options: ControllerOptions = {}): ControllerHarness {
	const calls: ControllerCalls = { saved: [], resolved: [], edits: [], focus: [], opened: [] };
	const phase = options.phase ?? "ready";
	const controller: WorkspaceControllerPort = {
		get phase() {
			return phase;
		},
		identity: options.identity ?? IDENTITY,
		generation: 1,
		isCurrent: () => true,
		request: options.request ?? (async () => ({ status: "no-workspace" })),
		save: options.save ?? (async (documentId) => {
			calls.saved.push(documentId);
			return { status: "saved", documentId, revision: "rev-2" };
		}),
		resolveConflict: options.resolveConflict ?? (async (documentId, choice) => {
			calls.resolved.push([documentId, choice]);
			return { status: "kept", documentId };
		}),
		closeTab: async (documentId): Promise<CloseOutcome> => ({ status: "closed", documentId, activeDocumentId: null }),
		resolveClose: async (documentId): Promise<CloseOutcome> => ({ status: "closed", documentId, activeDocumentId: null }),
		openUri: options.openUri ?? (async (uri) => {
			calls.opened.push(uri);
			return { status: "focused", documentId: "doc-1", uri };
		}),
		noteEdit: (documentId) => { calls.edits.push(documentId); },
		noteFocus: (region) => { calls.focus.push(region); },
		getLayoutInput: (): LayoutInput => ({
			columns: 0,
			rows: 0,
			requestedMode: "split",
			splitRatio: 0.5,
			explorerRequested: true,
			preferredPane: "chat",
		}),
		lspStatusSnapshot: options.lspStatusSnapshot ?? (() => ({ servers: [], profiles: {} })),
	};
	return { controller, calls };
}

interface ViewCalls {
	inline: Array<string | undefined>;
	documents: string[];
	released: string[];
	views: EditorViewState[];
}

interface IntegrationHarness {
	integration: WorkspaceEditorIntegration;
	paneState: { problems: Component | undefined; lsp: string | undefined };
	viewCalls: ViewCalls;
	problemRefreshes: Array<[string, number]>;
	controllerCalls: ControllerCalls;
}

function integrationHarness(
	store: EditorStore,
	controller: ControllerHarness,
	activeDocumentId: () => string | null,
): IntegrationHarness {
	const paneState: { problems: Component | undefined; lsp: string | undefined } = {
		problems: undefined,
		lsp: undefined,
	};
	const viewCalls: ViewCalls = { inline: [], documents: [], released: [], views: [] };
	const problemRefreshes: Array<[string, number]> = [];

	const pane: WorkspacePanePort = {
		render: () => [],
		invalidate() {},
		setProblems: (value) => { paneState.problems = value; },
		setLspStatus: (value) => { paneState.lsp = value; },
	};
	const view: WorkspaceViewPort = {
		setInlineCompletion: (text) => { viewCalls.inline.push(text); },
		allowsAutomaticCompletion: () => true,
		releaseDocument: (documentId) => { viewCalls.released.push(documentId); },
		setDocument: (documentId) => { viewCalls.documents.push(documentId); },
		setView: (state) => { viewCalls.views.push(state); },
	};

	const integration: WorkspaceEditorIntegration = {
		controller: controller.controller,
		store,
		tabs: { activeDocumentId },
		view,
		pane,
		explorerPane: component("explorer"),
		problemsPane: component("problems"),
		problemsModel: {
			refresh: async (uri, version) => {
				problemRefreshes.push([uri, version ?? -1]);
				return { ok: true };
			},
		},
	};
	return {
		integration,
		paneState,
		viewCalls,
		problemRefreshes,
		controllerCalls: controller.calls,
	};
}

type RequestFn = (method: string, params: Record<string, unknown>, timeoutMs: number) => Promise<unknown>;

interface HostState {
	integration: WorkspaceEditorIntegration | undefined;
	layout: LayoutResult | undefined;
	current: boolean;
	exiting: boolean;
	overlay: boolean;
	pickResult: SelectItem | undefined;
	requests: Array<{ method: string; params: Record<string, unknown>; timeoutMs: number }>;
	picks: Array<{ title: string; items: SelectItem[] }>;
	prints: Array<[string, string | undefined]>;
	focus: Component[];
	composer: Editor;
	restoredSplits: number;
	paused: number;
	renders: number;
	inserted: string[];
	hiddenOverlays: number;
}

interface HostHarness {
	host: WorkspaceHost;
	state: HostState;
}

function hostHarness(options: {
	integration?: WorkspaceEditorIntegration;
	layout?: LayoutResult;
	request?: RequestFn;
} = {}): HostHarness {
	const state: HostState = {
		integration: options.integration,
		layout: options.layout,
		current: true,
		exiting: false,
		overlay: false,
		pickResult: undefined,
		requests: [],
		picks: [],
		prints: [],
		focus: [],
		composer: {
			render: () => [],
			invalidate() {},
			insertTextAtCursor: (text: string) => { state.inserted.push(text); },
		} as unknown as Editor,
		restoredSplits: 0,
		paused: 0,
		renders: 0,
		inserted: [],
		hiddenOverlays: 0,
	};
	const tui = {
		terminal: { columns: 120, rows: 40 },
		requestRender: () => { state.renders += 1; },
		setFocus: (target: Component) => { state.focus.push(target); },
		getFocusedComponent: () => state.focus.at(-1) ?? null,
		showOverlay: () => ({ hide: () => { state.hiddenOverlays += 1; }, focus() {} }),
	} as unknown as TUI;
	const request = async <T>(method: string, params: Record<string, unknown>, timeoutMs: number): Promise<T> => {
		state.requests.push({ method, params, timeoutMs });
		const handler = options.request;
		if (!handler) throw new Error(`unexpected request: ${method}`);
		return await handler(method, params, timeoutMs) as T;
	};
	const host: WorkspaceHost = {
		tui,
		composerEditor: state.composer,
		bindings: { matches: () => false },
		integration: () => state.integration,
		layout: () => state.layout,
		unsupported: () => false,
		captureFence: () => 7,
		isCurrent: () => state.current,
		isExiting: () => state.exiting,
		overlayActive: () => state.overlay,
		restoreSplit: () => { state.restoredSplits += 1; },
		syncShellIfChanged: () => {},
		syncCursorBlink: () => {},
		pauseEditorCursorBlink: () => { state.paused += 1; },
		cursorBlockVisible: () => true,
		pick: async (title, items) => {
			state.picks.push({ title, items });
			return state.pickResult;
		},
		print: (text, kind) => { state.prints.push([text, kind]); },
		request,
	};
	return { host, state };
}

interface Deferred<T> {
	promise: Promise<T>;
	resolve(value: T): void;
}

function deferred<T>(): Deferred<T> {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => { resolve = done; });
	return { promise, resolve };
}

function quickOpenResult(name: string): WorkspaceQuickOpenResult {
	return {
		entries: [{ path: `src/${name}`, name, directory: "src" }],
		total: 1,
		truncated: false,
		reasons: [],
		indexing: false,
		scanned: 1,
		stale: false,
		workspaceEpoch: IDENTITY.epoch,
		exclusions: [],
	};
}

function flush(): Promise<void> {
	return new Promise((done) => setImmediate(done));
}

// ─── focus geometry ────────────────────────────────────────────────────────

test("focus router reconciles geometry, cycles regions and never steals from overlays", () => {
	const store = new EditorStore();
	const doc = openDocument(store);
	const controller = controllerHarness();
	const integration = integrationHarness(store, controller, () => doc.id);
	const harness = hostHarness({ integration: integration.integration, layout: SPLIT });
	const coordinator = new WorkspaceCoordinator(harness.host);

	assert.deepEqual(coordinator.focusRegions(), ["chat-composer", "editor", "explorer"]);
	assert.equal(coordinator.activeRegion, "chat-composer");

	// Explicit focus records the region and retargets the TUI.
	coordinator.setFocusForRegion("editor");
	assert.equal(coordinator.activeRegion, "editor");
	assert.equal(harness.state.focus.at(-1), integration.integration.pane);
	assert.deepEqual(controller.calls.focus, ["editor"]);

	// Cycle order follows the composed geometry.
	coordinator.cycleFocus(1);
	assert.equal(coordinator.activeRegion, "explorer");
	coordinator.cycleFocus(1);
	assert.equal(coordinator.activeRegion, "chat-composer");

	// The Problems region joins the cycle only while its section is mounted.
	coordinator.setFocusForRegion("editor");
	coordinator.openProblems();
	assert.equal(coordinator.problemsVisible(), true);
	assert.equal(integration.paneState.problems, integration.integration.problemsPane);
	assert.equal(coordinator.activeRegion, "problems");
	assert.deepEqual(coordinator.focusRegions(), ["chat-composer", "editor", "explorer", "problems"]);

	// A dialog/approval owns focus: a stale geometry must not steal it.
	harness.state.overlay = true;
	harness.state.layout = CHAT_ONLY;
	coordinator.reconcileFocus();
	assert.equal(coordinator.activeRegion, "problems", "overlay focus is never reconciled away");

	// Once the overlay clears, reconciliation retargets the always-live composer.
	harness.state.overlay = false;
	coordinator.reconcileFocus();
	assert.equal(coordinator.activeRegion, "chat-composer");
	assert.equal(harness.state.focus.at(-1), harness.state.composer);

	// Mouse focus from a hidden pane clamps instead of arming editor gates.
	coordinator.noteRegionFocus("explorer");
	assert.equal(coordinator.activeRegion, "chat-composer");
});

// ─── save success and conflict ─────────────────────────────────────────────

test("save routes compare-and-swap outcomes and an explicit conflict choice", async () => {
	const store = new EditorStore();
	const doc = openDocument(store);
	const conflict = {
		documentId: doc.id,
		uri: doc.uri,
		diskRevision: "disk-2",
		baseRevision: "rev-1",
		reason: "changed-on-disk",
	};
	const outcomes: SaveOutcome[] = [
		{ status: "saved", documentId: doc.id, revision: "rev-2" },
		{ status: "conflict", conflict },
	];
	const controller = controllerHarness({
		save: async (documentId) => {
			controller.calls.saved.push(documentId);
			return outcomes.shift() ?? { status: "not-found", documentId };
		},
		resolveConflict: async (documentId, choice) => {
			controller.calls.resolved.push([documentId, choice]);
			return { status: "reloaded", documentId, revision: "disk-2" };
		},
	});
	const integration = integrationHarness(store, controller, () => doc.id);
	const harness = hostHarness({ integration: integration.integration, layout: SPLIT });
	const coordinator = new WorkspaceCoordinator(harness.host);

	const renderedBefore = harness.state.renders;
	assert.equal(await coordinator.saveActiveDocument(), undefined);
	assert.equal(controller.calls.saved.length, 1, "one compare-and-swap save for the active buffer");
	assert.ok(harness.state.renders > renderedBefore, "a saved buffer repaints the status row");

	// A conflict is never auto-resolved: the explicit picker decides.
	harness.state.pickResult = { value: "reload", label: "Reload disk" };
	await coordinator.saveDocument(doc.id);
	assert.deepEqual(controller.calls.resolved, [[doc.id, "reload"]]);
	assert.equal(harness.state.picks.at(-1)?.title, "Save conflict: main.ts");
	assert.equal(integration.viewCalls.views.at(-1)?.documentId, doc.id, "reload refreshes the editor view");

	// MCH-012: a retired fence drops the save continuation silently.
	const rendersBeforeStale = harness.state.renders;
	harness.state.current = false;
	assert.equal(await coordinator.saveDocument(doc.id), false);
	assert.equal(harness.state.renders, rendersBeforeStale, "a stale save publishes nothing");
});

// ─── quick open sequencing ─────────────────────────────────────────────────

test("quick open binds one request per pane and drops a dismissed pane's response", async () => {
	const store = new EditorStore();
	const doc = openDocument(store);
	const controller = controllerHarness();
	const integration = integrationHarness(store, controller, () => doc.id);
	const first = deferred<WorkspaceQuickOpenResult>();
	const second = deferred<WorkspaceQuickOpenResult>();
	const replies = [first.promise, second.promise];
	const harness = hostHarness({
		integration: integration.integration,
		layout: SPLIT,
		request: async (method) => {
			assert.equal(method, "workspace.quickOpen");
			const reply = replies.shift();
			assert.ok(reply, "no unexpected quick-open request");
			return await reply;
		},
	});
	const coordinator = new WorkspaceCoordinator(harness.host);

	await coordinator.openQuickOpen();
	assert.equal(harness.state.requests.length, 1);
	assert.deepEqual(harness.state.requests[0]?.params, {
		workspaceId: IDENTITY.workspaceId,
		epoch: IDENTITY.epoch,
		query: "",
		limit: 50,
	});
	assert.equal(harness.state.requests[0]?.timeoutMs, 120_000);
	assert.equal(coordinator.quickOpenActive(), true);

	// A second open joins the existing overlay instead of stacking requests.
	await coordinator.openQuickOpen();
	assert.equal(harness.state.requests.length, 1);

	// Close dismisses the pane, then focus returns to the composer.
	coordinator.closeQuickOpen();
	assert.equal(coordinator.quickOpenActive(), false);
	assert.equal(harness.state.hiddenOverlays, 1);
	assert.equal(coordinator.activeRegion, "chat-composer");

	// Reopen: a new pane, a new request sequence.
	await coordinator.openQuickOpen();
	assert.equal(harness.state.requests.length, 2);

	second.resolve(quickOpenResult("b.ts"));
	await flush();
	assert.deepEqual(coordinator.quickOpenView()?.entries.map((entry) => entry.name), ["b.ts"]);

	// The dismissed pane's late answer can never replace the live pane's list.
	first.resolve(quickOpenResult("a.ts"));
	await flush();
	assert.deepEqual(coordinator.quickOpenView()?.entries.map((entry) => entry.name), ["b.ts"]);
});

// ─── completion acceptance ─────────────────────────────────────────────────

test("completion capture accepts through the version-checked session and refreshes the view", async () => {
	const store = new EditorStore();
	const doc = openDocument(store);
	store.setSelection(doc.id, { cursorOffset: 7 });
	const completion: WorkspaceIntelligenceResult = {
		workspaceId: IDENTITY.workspaceId,
		source: "editor-buffer",
		uri: doc.uri,
		documentVersion: doc.version,
		serverGeneration: 1,
		workspaceEpoch: IDENTITY.epoch,
		operation: "completion",
		state: "ready",
		freshness: "versioned",
		truncated: false,
		result: {
			isIncomplete: false,
			items: [{
				label: "xyz",
				kind: 6,
				textEdit: {
					range: { start: { line: 0, character: 6 }, end: { line: 0, character: 7 } },
					newText: "xyz",
				},
			}],
			omitted: 0,
		},
	};
	const controller = controllerHarness({
		request: async (operation, uri, version, position) => {
			assert.equal(operation, "completion");
			assert.equal(uri, doc.uri);
			assert.equal(version, doc.version);
			assert.deepEqual(position, { line: 0, character: 7 });
			return { status: "ok", result: completion };
		},
	});
	const integration = integrationHarness(store, controller, () => doc.id);
	const harness = hostHarness({ integration: integration.integration, layout: SPLIT });
	const coordinator = new WorkspaceCoordinator(harness.host);

	coordinator.setFocusForRegion("editor");
	await coordinator.startCompletion();
	assert.equal(coordinator.completionActive(), true);
	assert.equal(integration.viewCalls.inline.at(-1), "yz", "the inline suffix is shown before acceptance");

	coordinator.acceptCompletion();
	assert.equal(coordinator.completionActive(), false);
	assert.equal(store.getDocument(doc.id)?.text, "const xyz = 1;");
	assert.equal(store.getDocument(doc.id)?.version, 1);
	assert.deepEqual(controller.calls.edits, [doc.id]);
	assert.equal(harness.state.paused, 1, "editing retires the blink render demand");
	assert.equal(integration.viewCalls.views.at(-1)?.cursorOffset, 9, "the caret follows the applied edit");
});

// ─── LSP status ────────────────────────────────────────────────────────────

test("LSP status projects the active document's server state onto the pane", () => {
	const store = new EditorStore();
	const doc = openDocument(store);
	const controller = controllerHarness({
		lspStatusSnapshot: () => ({
			servers: [{ profile: "ts", languageId: "typescript", state: "starting", generation: 1 }],
			profiles: {},
		}),
	});
	const integration = integrationHarness(store, controller, () => doc.id);
	const harness = hostHarness({ integration: integration.integration, layout: SPLIT });
	const coordinator = new WorkspaceCoordinator(harness.host);

	assert.equal(coordinator.lspStatusText(), "starting…", "the raw state is shown without the language name");
	coordinator.refreshLspStatus();
	assert.equal(integration.paneState.lsp, "starting…");
});

// ─── surface wiring ────────────────────────────────────────────────────────

test("the surface host binds workspace callbacks to the coordinator's single state", () => {
	const store = new EditorStore();
	const doc = openDocument(store);
	const controller = controllerHarness();
	const integration = integrationHarness(store, controller, () => doc.id);
	const harness = hostHarness({ integration: integration.integration, layout: SPLIT });
	const coordinator = new WorkspaceCoordinator(harness.host);
	const hints: string[] = [];
	let preferencesLoaded = 0;
	let preferencesApplied = 0;

	const surface = coordinator.workspaceSurfaceHost({
		shell: {} as unknown as AppShell,
		setWorkspaceHints: (value) => { hints.push(value); },
		loadUiPreferences: () => { preferencesLoaded += 1; },
		applyUiPreferences: () => { preferencesApplied += 1; },
	});

	assert.equal(surface.activeRegion(), "chat-composer");
	assert.equal(surface.completionActive(), false);
	assert.deepEqual(surface.focusRegions(), ["chat-composer", "editor", "explorer"]);

	// The seam and the coordinator never keep competing copies of the region.
	surface.setFocusForRegion("editor");
	assert.equal(coordinator.activeRegion, "editor");
	assert.equal(surface.activeRegion(), "editor");

	surface.clearCompletion();
	assert.equal(surface.completionActive(), false);
	assert.equal(hints.length, 0);
	assert.equal(preferencesLoaded, 0);
	assert.equal(preferencesApplied, 0);
});
