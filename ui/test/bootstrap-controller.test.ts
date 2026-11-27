// Bootstrap controller seam: boot order, the workspace-disabled path, start
// failure recovery, gateway-loss teardown, the optional now-playing bar, and
// the dirty-quit review. The fake host records calls instead of constructing
// the real App, so this file does not depend on the composition root.

import assert from "node:assert/strict";
import { test } from "node:test";
import type { SelectItem, TUI } from "@earendil-works/pi-tui";
import { AppBootstrap, type BootstrapHost, type BootstrapOptions, type WorkspaceReview } from "../src/app/bootstrap.ts";
import type { AppShell } from "../src/components/AppShell.ts";
import type { Footer } from "../src/components/Footer.ts";
import type { NowPlayingBar } from "../src/components/NowPlayingBar.ts";
import type { ComposerBox } from "../src/components.ts";
import type { AnimationCoordinator, AnimationSubscription } from "../src/design/motion.ts";
import type { DocumentRecord } from "../src/editor/model.ts";
import type { NoticeKind } from "../src/features/index.ts";
import type { GatewayClient } from "../src/gateway.ts";
import type { BindingOverrides } from "../src/keybindings.ts";
import type { GatewayEvent, InitializeResult, ResumeResult, SessionInfo } from "../src/protocol.ts";
import type { Transcript } from "../src/transcript.ts";
import type { TurnSessionStore } from "../src/turnSessionStore.ts";
import type { ConflictResolution, SaveConflictDescriptor, SaveOutcome } from "../src/workspace/controller.ts";
import type { LogSink, ScreenView } from "../src/app/contracts.ts";

const SESSION: SessionInfo = {
	id: "sess-1",
	title: "Existing chat",
	model: "m",
	provider: "p",
	status: "active",
	lastActivity: null,
};

const INIT: InitializeResult = {
	version: "test",
	model: "m",
	provider: "p",
	cwd: "/work",
	cwdFull: "/work",
	branch: "main",
};

function documentRecord(id: string, uri: string, dirty: boolean): DocumentRecord {
	return {
		id,
		workspaceId: "w",
		uri,
		languageId: "typescript",
		text: "",
		version: 1,
		savedRevision: null,
		savedTextHash: null,
		dirty,
		readOnly: false,
		lineEnding: "LF",
		encoding: "utf-8",
	};
}

class FakeHost implements BootstrapHost {
	/** Every observable call, in order; assertions pin relative order here. */
	readonly events: string[] = [];
	readonly notices: Array<{ text: string; kind: NoticeKind; markdown: boolean }> = [];
	readonly logEntries: Array<{ level: string; message: string }> = [];
	readonly requests: Array<{ source: "client" | "app"; method: string; params: Record<string, unknown> }> = [];
	readonly workspaceRoots: string[] = [];
	readonly savedDocuments: string[] = [];
	readonly subscriptions: string[] = [];

	routes = new Map<string, (params: Record<string, unknown>) => unknown>();
	stderrLines: string[] = [];
	options: BootstrapOptions;
	defaultProfile = "tester";

	readonly footerValue = {
		chatStatus: {},
		cwd: "",
		branch: "",
		model: "",
		status: "offline",
		keyHint: "",
		dispose: (): void => {
			this.footerDisposed = true;
			this.record("footer:dispose");
		},
	};

	footerDisposed = false;
	statusDisposed = false;
	transcriptSuspended = false;
	transcriptCleared = 0;
	clientStopped = false;
	exitResolved = false;
	exiting = false;
	running = true;
	keyLeaderCleared = 0;
	keyLeaderTimerCleared = 0;
	abortEscCleared = 0;
	quickOpenClosed = 0;
	dialogsCancelled = 0;
	completionsDiscarded = 0;
	approvalsInvalidated = 0;
	inFlightTimersCleared = 0;
	allInFlightCleared = 0;
	workspaceTimerDisposed = 0;
	workspaceIntegrationDisposed = 0;
	fencesBegun = 0;
	cursorSynced = 0;
	cursorStopped = 0;
	prefilled: string | undefined;
	focusRegion: string | undefined;
	switchedTo: string | undefined;
	showedNewChat = 0;
	review: WorkspaceReview | undefined;
	pickResult: SelectItem | undefined;
	pickedTitle: string | undefined;
	failNowPlayingBar = false;
	onInputCalls = 0;
	onEventCalls = 0;

	constructor(options: BootstrapOptions = {}) {
		this.options = options;
	}

	readonly tui = {
		terminal: { columns: 100, rows: 30 },
		setClearOnShrink: (enabled: boolean) => this.record(`tui:setClearOnShrink:${enabled}`),
		setLayoutRoot: () => this.record("tui:setLayoutRoot"),
		addChild: () => this.record("tui:addChild"),
		setFocus: () => this.record("tui:setFocus"),
		addInputListener: () => {
			this.record("tui:addInputListener");
			return () => this.record("tui:removeInputListener");
		},
		start: () => this.record("tui:start"),
		stop: () => this.record("tui:stop"),
		requestRender: () => this.record("tui:requestRender"),
	} as unknown as TUI;

	readonly client = {
		request: <T>(method: string, params: Record<string, unknown> = {}, _timeoutMs?: number): Promise<T> =>
			this.rpc<T>("client", method, params),
		start: () => this.record("client:start"),
		stop: async () => {
			this.clientStopped = true;
			this.record("client:stop");
		},
		stderr: () => this.stderrLines,
		onEvent: undefined,
		onExit: undefined,
	} as unknown as GatewayClient;

	readonly log: LogSink = {
		enabled: true,
		error: (message) => this.logLine("error", message),
		warn: (message) => this.logLine("warn", message),
		info: (message) => this.logLine("info", message),
		debug: (message) => this.logLine("debug", message),
		trace: (message) => this.logLine("trace", message),
	};

	readonly footer: Footer = this.footerValue as unknown as Footer;

	readonly transcript: Transcript = {
		addNotice: (text: string, kind: NoticeKind = "info") => {
			this.notices.push({ text, kind, markdown: false });
			this.record(`notice:${text}`);
		},
		addMarkdown: (text: string, _label?: string) => {
			this.notices.push({ text, kind: "plain", markdown: true });
			this.record(`markdown:${text}`);
		},
		clear: () => {
			this.transcriptCleared += 1;
			this.record("transcript:clear");
		},
		suspend: () => {
			this.transcriptSuspended = true;
			this.record("transcript:suspend");
		},
	} as unknown as Transcript;

	readonly shell = {
		sync: () => this.record("shell:sync"),
		root: {},
	} as unknown as AppShell;

	readonly coordinator = {
		subscribe: (key: string): AnimationSubscription => {
			this.subscriptions.push(key);
			this.record(`coordinator:subscribe:${key}`);
			return { stop: () => this.record(`coordinator:stop:${key}`) };
		},
		dispose: () => this.record("coordinator:dispose"),
	} as unknown as AnimationCoordinator;

	readonly editor = {
		setText: (text: string) => {
			this.prefilled = text;
		},
	} as unknown as import("@earendil-works/pi-tui").Editor;

	private readonly nowPlayingBarValue = {
		state: undefined,
		visible: false,
		onToggle: undefined,
	};

	get nowPlayingBar(): NowPlayingBar {
		if (this.failNowPlayingBar) throw new Error("no audio backend");
		return this.nowPlayingBarValue as unknown as NowPlayingBar;
	}

	readonly status = {
		dispose: (): void => {
			this.statusDisposed = true;
			this.record("status:dispose");
		},
	};

	readonly turnSession = {
		clearInFlightTimers: () => {
			this.inFlightTimersCleared += 1;
			this.record("turnSession:clearInFlightTimers");
		},
		clearAllInFlight: () => {
			this.allInFlightCleared += 1;
			this.record("turnSession:clearAllInFlight");
		},
	} as unknown as TurnSessionStore;

	readonly transcriptView = {} as import("@earendil-works/pi-tui").Container;
	readonly composer = {} as ComposerBox;

	record(name: string): void {
		this.events.push(name);
	}

	private logLine(level: string, message: string): void {
		this.logEntries.push({ level, message });
		this.record(`log:${level}:${message}`);
	}

	private async rpc<T>(source: "client" | "app", method: string, params: Record<string, unknown>): Promise<T> {
		this.requests.push({ source, method, params });
		this.record(`${source}:request:${method}`);
		const handler = this.routes.get(method);
		if (!handler) throw new Error(`unexpected ${source} request: ${method}`);
		return (await handler(params)) as T;
	}

	onInput(data: string): { consume: boolean } | undefined {
		this.onInputCalls += 1;
		this.record(`app:onInput:${data}`);
		return undefined;
	}

	onEvent(event: GatewayEvent): void {
		this.onEventCalls += 1;
		void event;
		this.record("app:onEvent");
	}

	syncCursorBlink(): void {
		this.cursorSynced += 1;
		this.record("app:syncCursorBlink");
	}

	stopCursorBlink(): void {
		this.cursorStopped += 1;
		this.record("app:stopCursorBlink");
	}

	async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
		return this.rpc<T>("app", method, params);
	}

	current(): SessionInfo | undefined {
		return this.switchedTo === undefined ? undefined : { ...SESSION, id: this.switchedTo };
	}

	switchTo(session: SessionInfo, history: ResumeResult["history"]): void {
		this.switchedTo = session.id;
		void history;
		this.record(`app:switchTo:${session.id}`);
	}

	showNewChat(): void {
		this.showedNewChat += 1;
		this.record("app:showNewChat");
	}

	applyKeybindings(overrides: BindingOverrides): void {
		void overrides;
		this.record("app:applyKeybindings");
	}

	restyle(): void {
		this.record("app:restyle");
	}

	screen(): ScreenView {
		return "chat";
	}

	setFocusForRegion(region: "chat-composer"): void {
		this.focusRegion = region;
		this.record(`app:setFocusForRegion:${region}`);
	}

	startWorkspace(root: string): void {
		this.workspaceRoots.push(root);
		this.record(`workspace:start:${root}`);
	}

	beginWorkspaceFence(): number {
		this.fencesBegun += 1;
		this.record("workspace:beginFence");
		return this.fencesBegun;
	}

	captureWorkspaceFence(): number {
		this.record("workspace:captureFence");
		return 1;
	}

	isWorkspaceCurrent(gen: number): boolean {
		void gen;
		return true;
	}

	workspaceReview(): WorkspaceReview | undefined {
		return this.review;
	}

	async resolveWorkspaceSaveConflict(conflict: SaveConflictDescriptor): Promise<ConflictResolution | undefined> {
		this.record(`workspace:resolveConflict:${conflict.documentId}`);
		return { status: "saved", documentId: conflict.documentId, revision: "r" };
	}

	disposeWorkspaceTimer(): void {
		this.workspaceTimerDisposed += 1;
		this.record("workspace:disposeTimer");
	}

	async disposeWorkspaceIntegration(): Promise<void> {
		this.workspaceIntegrationDisposed += 1;
		this.record("workspace:disposeIntegration");
	}

	discardCompletion(): void {
		this.completionsDiscarded += 1;
		this.record("app:discardCompletion");
	}

	clearPendingKeyLeaderTimer(): void {
		this.keyLeaderTimerCleared += 1;
		this.record("app:clearPendingKeyLeaderTimer");
	}

	clearPendingKeyLeader(): void {
		this.keyLeaderCleared += 1;
		this.record("app:clearPendingKeyLeader");
	}

	clearPendingAbortEsc(): void {
		this.abortEscCleared += 1;
		this.record("app:clearPendingAbortEsc");
	}

	closeQuickOpen(): void {
		this.quickOpenClosed += 1;
		this.record("app:closeQuickOpen");
	}

	cancelAllDialogs(): void {
		this.dialogsCancelled += 1;
		this.record("app:cancelAllDialogs");
	}

	invalidateApprovals(): void {
		this.approvalsInvalidated += 1;
		this.record("app:invalidateApprovals");
	}

	setRunning(running: boolean): void {
		this.running = running;
		this.record(`app:setRunning:${running}`);
	}

	isExiting(): boolean {
		return this.exiting;
	}

	markExiting(): void {
		this.exiting = true;
		this.record("app:markExiting");
	}

	resolveExitPromise(): void {
		this.exitResolved = true;
		this.record("app:resolveExit");
	}

	async pick(title: string, items: SelectItem[]): Promise<SelectItem | undefined> {
		this.pickedTitle = title;
		this.record(`app:pick:${items.map((item) => item.value).join(",")}`);
		return this.pickResult;
	}

	// ─── helpers ────────────────────────────────────────────────────────────

	index(name: string, fromEnd = false): number {
		const index = fromEnd ? this.events.lastIndexOf(name) : this.events.indexOf(name);
		assert.notEqual(index, -1, `expected event ${name}\n${this.events.join("\n")}`);
		return index;
	}

	happensBefore(first: string, second: string): void {
		assert.ok(this.index(first) < this.index(second), `${first} must happen before ${second}`);
	}

	notice(text: string): { text: string; kind: NoticeKind; markdown: boolean } | undefined {
		return this.notices.find((entry) => entry.text.includes(text));
	}

	clearCalls(): void {
		this.events.length = 0;
		this.notices.length = 0;
		this.requests.length = 0;
	}
}

function successRoutes(host: FakeHost, config: Record<string, unknown> = {}): void {
	host.routes.set("initialize", () => INIT);
	host.routes.set("config.get", () => ({ config, secrets: [] }));
	host.routes.set("profile.get", () => ({}));
	host.routes.set("session.resume", () => ({ session: SESSION, history: [] }));
	host.routes.set("mode.get", () => ({ mode: "full" }));
	host.routes.set("nowplaying.get", () => ({
		available: true,
		state: "playing",
		title: "Song",
		artist: "Artist",
		elapsed: 1,
		duration: 100,
	}));
}

function withoutWorkspaceEnv(): void {
	delete process.env.MACHINA_WORKSPACE;
	delete process.env.MACHINA_TRANSPARENT_BACKGROUND;
}

test("start follows the established boot order", async () => {
	withoutWorkspaceEnv();
	const host = new FakeHost({ sessionId: "sess-1" });
	successRoutes(host, { theme: "codex", transparent_background: true });
	const bootstrap = new AppBootstrap(host);
	try {
		await bootstrap.start();

		host.happensBefore("tui:setClearOnShrink:true", "shell:sync");
		host.happensBefore("shell:sync", "tui:addChild");
		host.happensBefore("tui:addChild", "tui:setFocus");
		host.happensBefore("tui:setFocus", "app:syncCursorBlink");
		host.happensBefore("app:syncCursorBlink", "tui:addInputListener");
		host.happensBefore("tui:start", "client:start");
		host.happensBefore("client:start", "client:request:initialize");
		host.happensBefore("client:request:initialize", "client:request:config.get");
		host.happensBefore("client:request:config.get", "client:request:nowplaying.get");
		host.happensBefore("client:request:nowplaying.get", "client:request:profile.get");
		host.happensBefore("client:request:profile.get", "client:request:session.resume");
		host.happensBefore("client:request:session.resume", "app:switchTo:sess-1");
		host.happensBefore("app:switchTo:sess-1", "app:request:mode.get");
		host.happensBefore("app:request:mode.get", "workspace:start:/work");

		const fullHostNotice = host.events.findIndex((event) => event.startsWith("notice:FULL HOST"));
		assert.ok(fullHostNotice > host.index("app:request:mode.get"), "mode notice follows the session-scoped mode read");
		assert.ok(fullHostNotice < host.index("workspace:start:/work"), "workspace starts after the mode notice");

		host.happensBefore("workspace:start:/work", "log:info:ready");
		assert.equal(host.index("tui:requestRender", true), host.events.length - 1, "the final render is the last boot act");

		assert.equal(host.footerValue.status, "ready");
		assert.equal(host.footerValue.branch, "main");
		assert.deepEqual(host.workspaceRoots, ["/work"]);
		assert.equal(host.current()?.id, "sess-1");
		assert.equal(host.showedNewChat, 0);
		const initialize = host.requests.find((request) => request.method === "initialize");
		assert.deepEqual(initialize?.params, { protocolVersion: 2, model: undefined, provider: undefined, mode: undefined });
		assert.equal(host.onInputCalls, 0, "start only registers the listener");
		assert.equal(host.onEventCalls, 0, "start only wires the client callbacks");
	} finally {
		await bootstrap.exit();
	}
});

test("workspace-disabled boots chat without a workspace surface", async () => {
	withoutWorkspaceEnv();
	process.env.MACHINA_WORKSPACE = "0";
	const host = new FakeHost();
	successRoutes(host);
	const bootstrap = new AppBootstrap(host);
	try {
		await bootstrap.start();

		assert.equal(host.workspaceRoots.length, 0, "the flag stops surface construction");
		assert.equal(host.showedNewChat, 1, "a blank chat is still client-side only");
		assert.equal(host.current(), undefined);
		const mode = host.requests.find((request) => request.method === "mode.get");
		assert.deepEqual(mode?.params, {}, "no session means a global mode read");
		host.happensBefore("app:showNewChat", "app:request:mode.get");
		host.happensBefore("app:request:mode.get", "log:info:ready");
		assert.equal(host.index("tui:requestRender", true), host.events.length - 1);
	} finally {
		delete process.env.MACHINA_WORKSPACE;
		await bootstrap.exit();
	}
});

test("a failed start reports an honest notice and still reaches ready", async () => {
	withoutWorkspaceEnv();
	const host = new FakeHost();
	host.stderrLines = ["spawn failed", "line two"];
	host.routes.set("initialize", () => {
		throw new Error("gateway down");
	});
	const bootstrap = new AppBootstrap(host);
	try {
		await bootstrap.start();

		assert.equal(host.footerValue.status, "offline");
		assert.equal(host.transcriptCleared, 1);
		assert.match(host.notice("Could not start: gateway down")?.text ?? "", /gateway down/);
		assert.equal(host.notice("gateway down")?.kind, "error");
		assert.ok(host.notice("spawn failed\nline two"), "the stderr tail is shown");
		assert.ok(host.notice("Run `machina doctor`"), "the actionable next step is shown");
		const failure = host.logEntries.find((entry) => entry.message === "start.failed");
		assert.equal(failure?.level, "error");
		assert.equal(host.workspaceRoots.length, 0, "no initialized root means chat-only");
		assert.equal(host.requests.some((request) => request.method === "session.resume"), false);
		assert.ok(host.events.includes("log:info:ready"), "boot still settles so Ctrl+C can exit");
		assert.equal(host.index("tui:requestRender", true), host.events.length - 1);
	} finally {
		await bootstrap.exit();
	}
});

test("gateway exit retires local state offline once", async () => {
	withoutWorkspaceEnv();
	const host = new FakeHost();
	successRoutes(host);
	const bootstrap = new AppBootstrap(host);
	try {
		await bootstrap.start();
		host.clearCalls();
		const stderr = Array.from({ length: 10 }, (_, index) => `line ${index + 1}`);

		bootstrap.onGatewayExit(1, stderr);

		host.happensBefore("workspace:beginFence", "workspace:disposeTimer");
		host.happensBefore("workspace:disposeTimer", "app:clearPendingKeyLeaderTimer");
		host.happensBefore("app:clearPendingKeyLeaderTimer", "app:discardCompletion");
		host.happensBefore("app:discardCompletion", "app:stopCursorBlink");
		host.happensBefore("app:stopCursorBlink", "footer:dispose");
		host.happensBefore("footer:dispose", "status:dispose");
		host.happensBefore("status:dispose", "transcript:suspend");
		host.happensBefore("transcript:suspend", "workspace:disposeIntegration");
		host.happensBefore("workspace:disposeIntegration", "app:invalidateApprovals");
		host.happensBefore("app:invalidateApprovals", "turnSession:clearAllInFlight");
		host.happensBefore("turnSession:clearAllInFlight", "app:setRunning:false");
		assert.equal(host.index("tui:requestRender", true), host.events.length - 1);

		assert.equal(host.footerValue.status, "offline");
		assert.equal(host.running, false);
		assert.equal(host.workspaceTimerDisposed, 1);
		assert.equal(host.workspaceIntegrationDisposed, 1);
		assert.equal(host.approvalsInvalidated, 1);
		assert.equal(host.allInFlightCleared, 1);
		assert.equal(host.transcriptSuspended, true);
		assert.equal(host.statusDisposed, true);
		assert.equal(host.footerDisposed, true);
		assert.ok(host.notice("The gateway stopped (exit code 1)."));
		const tail = host.notice("line 3");
		assert.equal(tail?.text, "line 3\nline 4\nline 5\nline 6\nline 7\nline 8\nline 9\nline 10");
		assert.ok(host.notice("Press Ctrl+C to exit"));

		const afterFirst = host.events.length;
		bootstrap.onGatewayExit(null, []);
		assert.equal(host.events.length, afterFirst, "the second exit source is inert");
	} finally {
		if (!host.exiting) await bootstrap.exit();
	}
});

test("a failed now-playing startup is optional and never blocks chat", async () => {
	withoutWorkspaceEnv();
	const host = new FakeHost({ sessionId: "sess-1" });
	successRoutes(host);
	host.failNowPlayingBar = true;
	const bootstrap = new AppBootstrap(host);
	try {
		await bootstrap.start();

		assert.equal(host.current()?.id, "sess-1", "session creation is not delayed");
		assert.deepEqual(host.workspaceRoots, ["/work"], "workspace startup is not delayed");
		assert.ok(host.events.includes("log:info:ready"));
		assert.equal(bootstrap.music(), undefined, "no controller is published when startup fails");
		assert.equal(host.subscriptions.length, 0, "no animation demand for a dead bar");
	} finally {
		await bootstrap.exit();
	}
});

test("dirty-exit cancel leaves the app and all buffers untouched", async () => {
	withoutWorkspaceEnv();
	const host = new FakeHost();
	successRoutes(host);
	host.review = {
		documents: () => [documentRecord("doc-1", "file:///work/main.ts", true)],
		save: async () => {
			throw new Error("save must not run");
		},
	};
	host.pickResult = { value: "cancel", label: "Cancel" };
	const bootstrap = new AppBootstrap(host);
	try {
		await bootstrap.start();
		host.clearCalls();

		await bootstrap.exit();

		assert.equal(host.pickedTitle, "1 unsaved file: main.ts");
		assert.deepEqual(host.events.filter((event) => event.startsWith("app:pick:")), ["app:pick:save,discard,cancel"]);
		assert.equal(host.exiting, false);
		assert.equal(host.footerDisposed, false);
		assert.equal(host.clientStopped, false);
		assert.equal(host.events.includes("tui:stop"), false);
		assert.equal(host.events.includes("app:resolveExit"), false);
	} finally {
		host.review = undefined;
		await bootstrap.exit();
	}
});

test("dirty-exit save-all saves sequentially before teardown", async () => {
	withoutWorkspaceEnv();
	const host = new FakeHost();
	successRoutes(host);
	const first = documentRecord("doc-1", "file:///work/a.ts", true);
	const second = documentRecord("doc-2", "file:///work/b.ts", true);
	host.review = {
		documents: () => [first, second],
		save: async (documentId: string): Promise<SaveOutcome> => {
			host.savedDocuments.push(documentId);
			host.record(`workspace:save:${documentId}`);
			return { status: "saved", documentId, revision: "r1" };
		},
	};
	host.pickResult = { value: "save", label: "Save all & exit" };
	const bootstrap = new AppBootstrap(host);
	try {
		await bootstrap.start();
		host.clearCalls();

		await bootstrap.exit();

		assert.deepEqual(host.savedDocuments, ["doc-1", "doc-2"]);
		host.happensBefore("app:pick:save,discard,cancel", "workspace:captureFence");
		host.happensBefore("workspace:captureFence", "workspace:save:doc-1");
		host.happensBefore("workspace:save:doc-1", "workspace:save:doc-2");
		host.happensBefore("workspace:save:doc-2", "app:markExiting");
		host.happensBefore("app:markExiting", "footer:dispose");
		host.happensBefore("footer:dispose", "status:dispose");
		host.happensBefore("status:dispose", "transcript:suspend");
		host.happensBefore("transcript:suspend", "app:stopCursorBlink");
		host.happensBefore("app:stopCursorBlink", "coordinator:dispose");
		host.happensBefore("coordinator:dispose", "app:cancelAllDialogs");
		host.happensBefore("app:cancelAllDialogs", "tui:stop");
		host.happensBefore("tui:stop", "workspace:disposeIntegration");
		host.happensBefore("workspace:disposeIntegration", "client:stop");
		host.happensBefore("client:stop", "app:resolveExit");
		assert.equal(host.exiting, true);
		assert.equal(host.exitResolved, true);
	} finally {
		host.review = undefined;
	}
});

test("print renders plain reports as markdown and keeps the render request", () => {
	const host = new FakeHost();
	const bootstrap = new AppBootstrap(host);

	bootstrap.print("## Report\n\n| a | b |", "plain");
	assert.equal(host.notices[0]?.markdown, true, "plain command output parses as markdown");
	assert.equal(host.events.at(-1), "tui:requestRender");

	host.events.length = 0;
	bootstrap.print("───  Table\n" + "row", "plain");
	assert.equal(host.notices[1]?.markdown, false, "the fixed-width /skills table stays literal");
	assert.equal(host.notices[1]?.kind, "plain");

	host.events.length = 0;
	bootstrap.prefillInput("hello");
	assert.equal(host.prefilled, "hello");
	assert.equal(host.focusRegion, "chat-composer");
	assert.equal(host.events.at(-1), "tui:requestRender");
});
