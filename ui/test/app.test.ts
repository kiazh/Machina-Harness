import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { stripTerminalSequences, type Component, type OverlayHandle, type SelectItem, type TUI } from "@earendil-works/pi-tui";
import { App } from "../src/app.ts";
import { JsonRpcChannel } from "../src/gateway.ts";
import type { GatewayClient } from "../src/gateway.js";
import type { ChangeTurnInfo, GatewayEvent, ReviewRunInfo, SessionInfo } from "../src/protocol.js";

const SESSION_A: SessionInfo = {
	id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
	title: "session A",
	model: "m",
	provider: "openrouter",
	status: "active",
	lastActivity: null,
};

const SESSION_B: SessionInfo = {
	id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
	title: "session B",
	model: "m",
	provider: "openrouter",
	status: "active",
	lastActivity: null,
};

class FakeTUI implements TUI {
	children: Component[] = [];
	focused: Component | null = null;
	overlay: OverlayHandle | undefined;
	inputListeners: Array<(data: string) => void> = [];
	started = false;
	stopped = false;
	renderCount = 0;
	mode = "fullscreen" as const;
	terminal = {} as TUI["terminal"];
	fullRedraws = 0;

	addChild(c: Component): void {
		this.children.push(c);
	}
	removeChild(c: Component): void {
		const i = this.children.indexOf(c);
		if (i >= 0) this.children.splice(i, 1);
	}
	clear(): void {
		this.children = [];
	}
	setFocus(c: Component | null): void {
		this.focused = c;
	}
	showOverlay(_c: Component): OverlayHandle {
		const handle: OverlayHandle = {
			hide: () => {},
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
	clearOnShrink = false;
	start(): void {
		this.started = true;
	}
	stop(): void {
		this.stopped = true;
	}
	requestRender(): void {
		this.renderCount++;
	}
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
}

class FakeClient {
	onEvent: ((event: GatewayEvent) => void) | undefined;
	onExit: ((code: number | null, stderr: string[]) => void) | undefined;
	requests: Array<{ method: string; params: Record<string, unknown> }> = [];
	responses = new Map<string, unknown>();

	start(): void {}
	stderr(): string[] {
		return [];
	}
	async stop(): Promise<void> {}

	request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
		this.requests.push({ method, params });
		const response = this.responses.get(method);
		if (response === undefined) return Promise.reject(new Error(`unexpected request ${method}`));
		return Promise.resolve(response as T);
	}

	/** Simulate the gateway sending an event. */
	emit(event: GatewayEvent): void {
		this.onEvent?.(event);
	}
}

/**
 * GatewayClient stand-in backed by the real transport-agnostic channel, so
 * request-timeout semantics under fake timers are the production ones.
 */
class ChannelClient {
	readonly frames: Array<{ id: number; method: string; params: Record<string, unknown> }> = [];
	readonly channel = new JsonRpcChannel((line) => {
		this.frames.push(JSON.parse(line) as (typeof this.frames)[number]);
	});

	start(): void {}
	stderr(): string[] {
		return [];
	}
	async stop(): Promise<void> {}

	request<T>(method: string, params: Record<string, unknown> = {}, timeoutMs?: number): Promise<T> {
		return this.channel.request<T>(method, params, timeoutMs);
	}

	/** Answer the most recent request frame with *result*. */
	reply(result: unknown): void {
		const frame = this.frames.at(-1)!;
		this.channel.handleLine(JSON.stringify({ jsonrpc: "2.0", id: frame.id, result }));
	}
}

function makeApp(session: SessionInfo, options?: ConstructorParameters<typeof App>[2]) {
	const tui = new FakeTUI();
	const client = new FakeClient();
	client.responses.set("initialize", {
		version: "1.0.0",
		model: "m",
		provider: "openrouter",
		cwd: "/tmp",
		branch: "main",
	});
	client.responses.set("session.create", { session });
	client.responses.set("prompt.submit", {});
	const app = new App(tui, client as unknown as GatewayClient, options);
	return { app, tui, client };
}

const ev = (e: Record<string, unknown>) => ({ sessionId: SESSION_A.id, turnId: "t", ...e }) as GatewayEvent;

function widgetWith<K extends string>(tui: FakeTUI, property: K): Component & Record<K, unknown> {
	const direct = tui.children.find((child) => property in child);
	if (direct) return direct as Component & Record<K, unknown>;
	// The editor lives nested inside the composer box.
	for (const child of tui.children) {
		if (child && typeof child === "object" && "editor" in child) {
			const inner = (child as { editor: unknown }).editor;
			if (inner && typeof inner === "object" && property in inner) {
				return inner as Component & Record<K, unknown>;
			}
		}
	}
	assert.fail(`widget with ${property} exists`);
}

test("a slash command loading owns input until its dialog opens", async () => {
	const { app, tui } = makeApp(SESSION_A);
	await app.start();
	try {
		const state = app as unknown as { commandRunning: boolean; onInput(data: string): { consume: boolean } | undefined };
		const editor = widgetWith(tui, "getText") as unknown as { getText(): string };
		state.commandRunning = true;
		assert.deepEqual(state.onInput("/"), { consume: true });
		assert.equal(editor.getText(), "", "a slash typed during loading never reaches the composer");
		state.commandRunning = false;
		assert.equal(state.onInput("/"), undefined, "the composer regains normal handling after the command settles");
	} finally {
		await app.exit();
	}
});

test("a slow command releases ordinary typing after the grace window", async () => {
	const { app } = makeApp(SESSION_A);
	await app.start();
	try {
		const state = app as unknown as {
			commandRunning: boolean;
			commandRunningAt: number;
			onInput(data: string): { consume: boolean } | undefined;
		};
		state.commandRunning = true;
		state.commandRunningAt = Date.now() - 2_000;
		assert.equal(state.onInput("a"), undefined, "typing reaches the composer after the grace window");
		assert.deepEqual(state.onInput("\r"), { consume: true }, "Enter still belongs to the command");
		state.commandRunningAt = Date.now();
		assert.deepEqual(state.onInput("b"), { consume: true }, "the initial window still owns keys");
	} finally {
		await app.exit();
	}
});

test("start() enables shrink-clearing so closed menus leave no ghost rows", async () => {
	const { app, tui } = makeApp(SESSION_A);
	await app.start();
	assert.equal(tui.clearOnShrink, true);
	await app.exit();
});

test("plain command reports render through Markdown", async () => {
	const { app } = makeApp(SESSION_A);
	await app.start();
	try {
		app.print("## Growth report\n\n- **RSI:** on\n\n| Decision | Rating |\n|---|---|\n| `abcdef12` | unrated |", "plain");
		const transcript = (app as unknown as {
			transcript: { container: { children: Component[] } };
		}).transcript;
		const report = transcript.container.children.at(-1)!;
		const rendered = stripTerminalSequences(report.render(80).join("\n"));
		assert.match(rendered, /Growth report/);
		assert.match(rendered, /output/);
		assert.match(rendered, /RSI:/);
		assert.match(rendered, /unrated/);
	} finally {
		await app.exit();
	}
});

test("Activity ← Chat → Changes walk one strip and keep the composer draft", async () => {
	const { app } = makeApp(SESSION_A);
	await app.start();
	try {
		const input = (app as unknown as { onInput(data: string): unknown }).onInput.bind(app);
		const view = () => (app as unknown as { view: string }).view;
		const editor = (app as unknown as { editor: { getText(): string; setText(text: string): void } }).editor;
		editor.setText("work in progress");
		assert.equal(input("\x1b[D"), undefined, "a plain left arrow without Alt stays unbound");
		input("\x1b[1;3D"); // Alt+Left
		assert.equal(view(), "status", "Alt+Left from Chat opens Activity");
		input("\x1b[1;3C"); // Alt+Right
		assert.equal(view(), "chat", "Alt+Right steps back toward Chat");
		input("\x1b[1;3C");
		assert.equal(view(), "changes", "Alt+Right from Chat opens Changes");
		input("\x1b[1;3D");
		assert.equal(view(), "chat", "Alt+Left steps back toward Chat");
		assert.equal(editor.getText(), "work in progress");
		editor.setText("");
		input("\x1b[D");
		assert.equal(view(), "chat", "plain arrows never navigate the shell");
	} finally {
		await app.exit();
	}
});

test("Alt+1/2/3 switch the workspace layout, not the screen", async () => {
	const { app } = makeApp(SESSION_A);
	await app.start();
	try {
		const input = (app as unknown as { onInput(data: string): unknown }).onInput.bind(app);
		const view = () => (app as unknown as { view: string }).view;
		input("\x1b1");
		input("\x1b2");
		input("\x1b3");
		assert.equal(view(), "chat", "layout keys never leave Chat");
	} finally {
		await app.exit();
	}
});

test("plain arrows keep editing when the composer has text", async () => {
	const { app } = makeApp(SESSION_A);
	await app.start();
	try {
		const input = (app as unknown as { onInput(data: string): unknown }).onInput.bind(app);
		const view = () => (app as unknown as { view: string }).view;
		input("a");
		input("\x1b[D");
		input("\x1b[C");
		assert.equal(view(), "chat");
	} finally {
		await app.exit();
	}
});

test("Ctrl+V, Alt+V, and clicking Paste image all attach without sending", async () => {
	let reads = 0;
	const { app, client } = makeApp(SESSION_A, {
		readClipboardImage: async () => {
			reads++;
			return { name: "copied.png", mediaType: "image/png", data: "iVBORw0KGgo=" };
		},
	});
	await app.start();
	try {
		const state = app as unknown as { onInput(data: string): unknown; editor: { getText(): string }; composer: Component };
		state.onInput("\x16");
		await new Promise(resolve => setImmediate(resolve));
		state.onInput("\x1bv");
		await new Promise(resolve => setImmediate(resolve));
		const x = stripTerminalSequences(state.composer.render(80)[0]!).indexOf("Paste image") + 1;
		assert.ok(x > 0);
		state.composer.handleMouse?.({ type: "click", button: "left", x, y: 0, width: 80, height: 3 } as never);
		await new Promise(resolve => setImmediate(resolve));
		assert.equal(reads, 3);
		assert.equal(state.editor.getText(), "[image 1] [image 2] [image 3] ");
		assert.ok(!client.requests.some(r => r.method === "prompt.submit"));
	} finally {
		await app.exit();
	}
});

for (const state of ["idle chat", "busy chat", "no chat"] as const) {
test(`review stays inline and Fix never opens a new chat (${state})`, async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 80, rows: 24 } as TUI["terminal"];
	await app.start();
	try {
		if (state !== "no chat") {
			await app.submitTurn("Original conversation");
			if (state === "idle chat") {
				client.emit(ev({ type: "message.complete", text: "done", tokens: 0, iterations: 1, toolCalls: 0, cancelled: false }));
			}
		}
		client.responses.set("prompt.steer", { queued: true, items: [] });
		const turnId = "a".repeat(32);
		const finding = {
			rule: "hardcoded-secret",
			severity: "high" as const,
			title: "Possible API secret key added",
			body: "Remove the value from source and rotate it if it was real.",
			file: "portfolio-plan.md",
			line: 0,
			fingerprint: "abc123",
		};
		const review: ReviewRunInfo = {
			id: "review-1",
			snapshotId: turnId,
			sessionId: SESSION_A.id,
			engine: "rules",
			status: "complete",
			provider: "hermes",
			model: "tencent/hy4-flash:free",
			createdAt: new Date().toISOString(),
			startedAt: new Date().toISOString(),
			finishedAt: new Date().toISOString(),
			summary: "1 finding: 1 high",
			findings: [finding, { ...finding, title: "Second finding from the full review", fingerprint: "second" }],
			error: null,
		};
		const turn: ChangeTurnInfo = {
			id: turnId,
			sessionId: SESSION_A.id,
			prompt: "do it again",
			createdAt: new Date().toISOString(),
			provider: "hermes",
			model: "tencent/hy4-flash:free",
			status: "complete",
			stats: { files: 1, additions: 0, deletions: 0 },
			files: [],
			review: null,
		};
		client.responses.set("changes.list", { turns: [turn] });
		client.responses.set("changes.diff", { files: [] });
		client.responses.set("review.start", { review });
		const internals = app as unknown as { onInput(data: string): unknown; view: string };
		const editor = (app as unknown as { editor: { getText(): string; setText(text: string): void } }).editor;
		editor.setText("unsent draft");
		const settle = () => new Promise(resolve => setImmediate(resolve));
		const before = client.requests.length;
		app.openChanges();
		await settle();
		if (state === "no chat") {
			// Changes belong to a chat: with no chat there is nothing to review.
			const screen = stripTerminalSequences(tui.focused!.render(80).join("\n"));
			assert.match(screen, /No chat yet/);
			assert.doesNotMatch(screen, /Possible API secret/);
			internals.onInput("\r");
			await settle();
			assert.ok(!client.requests.slice(before).some(r => r.method.startsWith("prompt.")));
			assert.equal(internals.view, "changes");
			assert.equal(editor.getText(), "unsent draft");
			return;
		}
		assert.ok(!client.requests.slice(before).some(r => r.method === "changes.diff"));
		client.responses.set("changes.list", { turns: [{ ...turn, review: { ...review, findings: [finding], findingsCount: 2 } }] });
		internals.onInput("r");
		await settle();
		const modelStart = client.requests.length;
		internals.onInput("m");
		await settle();
		const modelRequest = client.requests.slice(modelStart).find(r => r.method === "review.start");
		assert.equal(modelRequest?.params.engine, "model");
		app.openChanges(); // refresh with the compact response of the same review
		await settle();
		assert.equal(tui.hasOverlay(), false);
		assert.match(stripTerminalSequences(tui.focused!.render(80).join("\n")), /Possible API secret/);
		internals.onInput("n");
		assert.ok(stripTerminalSequences(tui.focused!.render(80).join("\n")).includes("Second finding from the full review"), "refresh must keep the full review navigable");
		internals.onInput("p");
		tui.focused!.render(80);
		assert.ok(!client.requests.slice(before).some(r => r.method.startsWith("prompt.")));
		internals.onInput("\r");
		await settle();
		const requests = client.requests.slice(before);
		assert.ok(!requests.some(r => r.method === "session.create"));
		const repair = requests.find(r => r.method === "prompt.submit" || r.method === "prompt.steer");
		assert.equal(repair?.method, state === "busy chat" ? "prompt.steer" : "prompt.submit");
		assert.equal(repair?.params.sessionId, SESSION_A.id);
		assert.match(String(repair?.params.text), /\[HIGH\] Possible API secret key added/);
		assert.equal(internals.view, "chat");
		assert.equal(editor.getText(), "unsent draft");
	} finally {
		await app.exit();
	}
});
}

test("Activity shows the command and the shell's output before its metadata", async () => {
	const { app, tui, client } = makeApp(SESSION_A);
	tui.terminal = { columns: 100, rows: 26 } as TUI["terminal"];
	client.responses.set("shell.jobs", { jobs: [{ id: "job-1", command: "npm run check", status: "exited", output: "1282 tests passed", returncode: 0 }] });
	await app.start();
	try {
		await app.submitTurn("Check the workspace");
		app.openStatus();
		await new Promise(resolve => setImmediate(resolve));
		const page = tui.focused as Component & { handleInput(data: string): void };
		page.handleInput("4");
		const text = () => stripTerminalSequences(page.render(100).join("\n"));
		assert.match(text(), /❯ npm run check/);
		assert.match(text(), /1282 tests passed/);
		assert.doesNotMatch(text(), /Return code/);
		page.handleInput(" ");
		assert.match(text(), /Return code: 0/);
	} finally {
		await app.exit();
	}
});

test("a diff file omitted by the budget is fetched by path", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 110, rows: 26 } as TUI["terminal"];
	await app.start();
	try {
		await app.submitTurn("Touch two files");
		const turn: ChangeTurnInfo = {
			id: "b".repeat(32), sessionId: SESSION_A.id, prompt: "Touch two files",
			createdAt: new Date().toISOString(), provider: "fixture", model: "preview", status: "complete",
			stats: { files: 2, additions: 2, deletions: 0 },
			files: [
				{ path: "big.ts", change: "modified", additions: 1, deletions: 0, binary: false },
				{ path: "small.ts", change: "modified", additions: 1, deletions: 0, binary: false },
			],
			review: null,
		};
		client.responses.set("changes.list", { turns: [turn] });
		client.responses.set("changes.diff", {
			files: [{ path: "big.ts", change: "modified", binary: false, truncated: true, text: "+big content\n" }],
			truncated: true,
		});
		const settle = () => new Promise(resolve => setImmediate(resolve));
		app.openChanges();
		await settle();
		const page = tui.focused as Component & { handleInput(data: string): void; render(width: number): string[] };
		const text = () => stripTerminalSequences(page.render(110).join("\n"));
		page.handleInput("2");
		await settle();
		assert.match(text(), /big content/);
		client.responses.set("changes.diff", {
			files: [{ path: "small.ts", change: "modified", binary: false, truncated: false, text: "+small tail\n" }],
		});
		page.handleInput("]");
		await settle();
		assert.match(text(), /small tail/);
		const calls = client.requests.filter(request => request.method === "changes.diff");
		assert.equal(calls.at(-1)?.params.path, "small.ts");
	} finally {
		await app.exit();
	}
});

test("the Changes count refreshes after a completed turn while Chat is open", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	try {
		client.responses.set("changes.list", {
			turns: [{
				id: "t".repeat(32), sessionId: SESSION_A.id, prompt: "Edit a file",
				createdAt: new Date().toISOString(), provider: "fixture", model: "preview", status: "complete",
				stats: { files: 1, additions: 1, deletions: 0 }, files: [], review: null,
			}],
		});
		await app.submitTurn("Edit a file");
		client.emit(ev({ type: "message.complete", text: "done", tokens: 0, iterations: 1, toolCalls: 1, cancelled: false }));
		await new Promise(resolve => setImmediate(resolve));
		const count = (app as unknown as { navSnapshot(): { changeCount: number } }).navSnapshot().changeCount;
		assert.equal(count, 1, "the command strip counts this chat's changed prompt without opening Changes");
	} finally {
		await app.exit();
	}
});

test("a stale list response cannot erase a review this UI just ran", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 110, rows: 26 } as TUI["terminal"];
	await app.start();
	try {
		await app.submitTurn("Review the installer");
		const turnId = "d".repeat(32);
		const finding = {
			rule: "hardcoded-secret", severity: "high" as const, title: "Possible API secret key added",
			body: "Remove it.", file: "install.sh", line: 4, fingerprint: "f1",
		};
		const review: ReviewRunInfo = {
			id: "review-local", snapshotId: turnId, sessionId: SESSION_A.id, engine: "rules", status: "complete",
			provider: "fixture", model: "preview", createdAt: new Date().toISOString(),
			startedAt: new Date().toISOString(), finishedAt: new Date().toISOString(),
			summary: "1 finding", findings: [finding], error: null,
		};
		const turn: ChangeTurnInfo = {
			id: turnId, sessionId: SESSION_A.id, prompt: "Review the installer",
			createdAt: new Date().toISOString(), provider: "fixture", model: "preview", status: "complete",
			stats: { files: 1, additions: 1, deletions: 0 },
			files: [{ path: "install.sh", change: "modified", additions: 1, deletions: 0, binary: false }],
			review: null,
		};
		client.responses.set("changes.list", { turns: [turn] });
		client.responses.set("changes.diff", { files: [] });
		client.responses.set("review.start", { review });
		const settle = () => new Promise(resolve => setImmediate(resolve));
		app.openChanges();
		await settle();
		const page = tui.focused as Component & { handleInput(data: string): void; render(width: number): string[] };
		const text = () => stripTerminalSequences(page.render(110).join("\n"));
		page.handleInput("r");
		await settle();
		assert.match(text(), /Possible API secret/)
		// A poll issued before the run returns "no review"; it must not erase it.
		client.responses.set("changes.list", { turns: [{ ...turn, review: null }] });
		app.openChanges();
		await settle();
		assert.match(text(), /Possible API secret/, "the local run survives an older list response");
		// A genuinely newer run does replace it.
		client.responses.set("changes.list", {
			turns: [{ ...turn, review: { ...review, id: "review-newer", status: "cancelled", findings: [], finishedAt: new Date(Date.now() + 5_000).toISOString() } }],
		});
		app.openChanges();
		await settle();
		assert.match(text(), /Review cancelled/);
	} finally {
		await app.exit();
	}
});

test("Clear handled hides sent findings without offering them again", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 100, rows: 26 } as TUI["terminal"];
	await app.start();
	try {
		await app.submitTurn("Fix the installer");
		client.emit(ev({ type: "message.complete", text: "done", tokens: 0, iterations: 1, toolCalls: 0, cancelled: false }));
		const finding = {
			rule: "hardcoded-secret", severity: "high" as const, title: "Possible API secret key added",
			body: "Remove it.", file: "install.sh", line: 4, fingerprint: "abc123",
		};
		const second = { ...finding, title: "Unquoted variable expands", fingerprint: "def456" };
		const turnId = "a".repeat(32);
		const turn: ChangeTurnInfo = {
			id: turnId, sessionId: SESSION_A.id, prompt: "Fix the installer",
			createdAt: new Date().toISOString(), provider: "fixture", model: "preview", status: "complete",
			stats: { files: 1, additions: 1, deletions: 0 },
			files: [{ path: "install.sh", change: "modified", additions: 1, deletions: 0, binary: false }],
			review: {
				id: "review-clear", snapshotId: turnId, sessionId: SESSION_A.id, engine: "rules", status: "complete",
				provider: "fixture", model: "preview", createdAt: new Date().toISOString(),
				startedAt: new Date().toISOString(), finishedAt: new Date().toISOString(),
				summary: "2 findings", findings: [finding, second], findingsCount: 2, error: null,
			},
		};
		client.responses.set("changes.list", { turns: [turn] });
		client.responses.set("changes.diff", { files: [] });
		const settle = () => new Promise(resolve => setImmediate(resolve));
		app.openChanges();
		await settle();
		const page = tui.focused as Component & { handleInput(data: string): void; render(width: number): string[] };
		const text = () => stripTerminalSequences(page.render(100).join("\n"));
		page.handleInput("3");
		page.render(100);
		page.handleInput("\r");
		await settle();
		assert.equal(client.requests.filter(request => request.method === "prompt.submit").length, 2);
		app.openChanges();
		await settle();
		assert.match(text(), /Sent {2}Possible API secret/);
		assert.match(text(), /Unquoted variable expands/);
		page.handleInput("c");
		await settle();
		assert.doesNotMatch(text(), /Clear handled|Sent {2}Possible API secret/);
		page.handleInput("n");
		assert.match(text(), /Unquoted variable expands/);
		assert.doesNotMatch(text(), /Possible API secret key added/, "a cleared finding is never offered again");
	} finally {
		await app.exit();
	}
});

test("a compact review is hydrated before finding navigation", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 100, rows: 26 } as TUI["terminal"];
	await app.start();
	try {
		await app.submitTurn("Fix the installer");
		client.emit(ev({ type: "message.complete", text: "done", tokens: 0, iterations: 1, toolCalls: 0, cancelled: false }));
		const finding = {
			rule: "model", severity: "high" as const, title: "Possible API secret key added",
			body: "Remove it.", file: "install.sh", line: 4, fingerprint: "abc123",
		};
		const second = { ...finding, title: "Second hydrated finding", fingerprint: "def456" };
		const turnId = "a".repeat(32);
		const review: ReviewRunInfo = {
			id: "review-hydrate", snapshotId: turnId, sessionId: SESSION_A.id, engine: "model", status: "complete",
			provider: "fixture", model: "preview", createdAt: new Date().toISOString(),
			startedAt: new Date().toISOString(), finishedAt: new Date().toISOString(),
			summary: "2 findings", findings: [finding], findingsCount: 2, error: null,
			coverage: { files: 1, reviewed: 1, chars: 20, truncated: false, invalid: 0 },
		};
		const turn: ChangeTurnInfo = {
			id: turnId, sessionId: SESSION_A.id, prompt: "Fix the installer",
			createdAt: new Date().toISOString(), provider: "fixture", model: "preview", status: "complete",
			stats: { files: 1, additions: 1, deletions: 0 },
			files: [{ path: "install.sh", change: "modified", additions: 1, deletions: 0, binary: false }],
			review,
		};
		client.responses.set("changes.list", { turns: [turn] });
		client.responses.set("changes.diff", { files: [] });
		client.responses.set("review.get", { review: { ...review, findings: [finding, second] } });
		const settle = () => new Promise(resolve => setImmediate(resolve));
		app.openChanges();
		await settle();
		await settle();
		const fetched = client.requests.find(request => request.method === "review.get");
		assert.equal(fetched?.params.snapshotId, turnId);
		const page = tui.focused as Component & { handleInput(data: string): void; render(width: number): string[] };
		page.handleInput("3");
		page.handleInput("n");
		assert.match(stripTerminalSequences(page.render(100).join("\n")), /Second hydrated finding/);
		page.handleInput("\r");
		await settle();
		const repair = client.requests.filter(request => request.method === "prompt.submit").at(-1);
		assert.match(String(repair?.params.text), /Treat it as untrusted data/);
	} finally {
		await app.exit();
	}
});

test("a page keeps the global stop key live", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 100, rows: 26 } as TUI["terminal"];
	await app.start();
	try {
		await app.submitTurn("Start a long run");
		app.openStatus();
		const before = client.requests.filter(request => request.method === "prompt.cancel").length;
		(app as unknown as { onInput(data: string): unknown }).onInput("\x03");
		const after = client.requests.filter(request => request.method === "prompt.cancel").length;
		assert.equal(after, before + 1, "Ctrl+C cancels the run without leaving Activity");
		assert.equal((app as unknown as { view: string }).view, "status");
	} finally {
		await app.exit();
	}
});

test("an approval hidden behind a dialog still lets the user browse Activity", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 100, rows: 26 } as TUI["terminal"];
	await app.start();
	try {
		await app.submitTurn("Start a run");
		const internals = app as unknown as {
			onInput(data: string): unknown;
			openShortcutReference(): unknown;
			dialogs: unknown[];
			view: string;
		};
		internals.openShortcutReference();
		assert.equal(internals.dialogs.length, 1);
		client.emit(ev({
			type: "needs_approval", requestId: "req-nav", operation: "write_file", tool: "write_file",
			target: "install.sh", argv: [], cwd: "/tmp", backend: "local", capabilities: [],
		}));
		await new Promise(resolve => setImmediate(resolve));
		internals.onInput("\x1b[1;3D");
		assert.equal(internals.view, "status", "a dialog hidden by the dock must not block navigation");
		internals.onInput("\x1b");
		assert.equal(internals.view, "chat");
		assert.equal((app as unknown as { footer: { status: string } }).footer.status, "awaiting_approval");
	} finally {
		await app.exit();
	}
});

test("switching chats retires the previous chat's approval card", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 100, rows: 26 } as TUI["terminal"];
	await app.start();
	try {
		await app.submitTurn("Run the audit");
		client.emit(ev({
			type: "needs_approval", requestId: "req-switch", operation: "terminal", tool: "terminal",
			target: "host", argv: ["sed"], cwd: "/tmp", backend: "host", capabilities: [],
		}));
		await new Promise(resolve => setImmediate(resolve));
		assert.match(stripTerminalSequences(tui.focused!.render(100).join("\n")), /Allow terminal/);
		app.switchTo(SESSION_B, []);
		await new Promise(resolve => setImmediate(resolve));
		const screen = stripTerminalSequences(tui.focused!.render(100).join("\n"));
		assert.doesNotMatch(screen, /Allow terminal/);
		assert.notEqual((app as unknown as { footer: { status: string } }).footer.status, "awaiting_approval");
	} finally {
		await app.exit();
	}
});

test("a dialog hidden by an approval reappears when the approval resolves elsewhere", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 100, rows: 26 } as TUI["terminal"];
	await app.start();
	try {
		await app.submitTurn("Start a run");
		const internals = app as unknown as {
			onInput(data: string): unknown;
			openShortcutReference(): unknown;
			dialogs: Array<{ hidden: boolean }>;
			view: string;
		};
		internals.openShortcutReference();
		client.emit(ev({
			type: "needs_approval", requestId: "req-restore", operation: "write_file", tool: "write_file",
			target: "install.sh", argv: [], cwd: "/tmp", backend: "local", capabilities: [],
		}));
		await new Promise(resolve => setImmediate(resolve));
		assert.equal(internals.dialogs[0]?.hidden, true, "the dock hides the draft form on Chat");
		internals.onInput("\x1b[1;3D");
		client.emit(ev({ type: "message.complete", text: "done", tokens: 0, iterations: 1, toolCalls: 0, cancelled: false }));
		await new Promise(resolve => setImmediate(resolve));
		assert.equal(internals.dialogs[0]?.hidden, false, "an externally ended approval restores the draft");
		assert.equal(internals.view, "status");
	} finally {
		await app.exit();
	}
});

test("Changes hands a failed review to the current chat, never a new one", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 100, rows: 26 } as TUI["terminal"];
	await app.start();
	try {
		await app.submitTurn("Original conversation");
		client.emit(ev({ type: "message.complete", text: "done", tokens: 0, iterations: 1, toolCalls: 0, cancelled: false }));
		const turn: ChangeTurnInfo = {
			id: "c".repeat(32),
			sessionId: SESSION_A.id,
			prompt: "Check the installer",
			createdAt: new Date().toISOString(),
			provider: "fixture",
			model: "preview",
			status: "complete",
			stats: { files: 1, additions: 1, deletions: 0 },
			files: [{ path: "install.sh", change: "modified", additions: 1, deletions: 0, binary: false }],
			review: {
				id: "review-failed",
				snapshotId: "c".repeat(32),
				sessionId: SESSION_A.id,
				engine: "rules",
				status: "failed",
				provider: "fixture",
				model: "preview",
				createdAt: new Date().toISOString(),
				startedAt: new Date().toISOString(),
				finishedAt: new Date().toISOString(),
				summary: "rules engine crashed",
				findings: [],
				error: "rules engine crashed while parsing the diff",
			},
		};
		client.responses.set("changes.list", { turns: [turn] });
		client.responses.set("changes.diff", { files: [] });
		const settle = () => new Promise(resolve => setImmediate(resolve));
		app.openChanges();
		await settle();
		const page = tui.focused as Component & { handleInput(data: string): void; render(width: number): string[] };
		page.handleInput("3");
		const screen = stripTerminalSequences(page.render(100).join("\n"));
		assert.match(screen, /rules engine crashed while parsing the diff/);
		assert.match(screen, /Fix this/);
		const sessionsBefore = client.requests.filter(request => request.method === "session.create").length;
		client.responses.delete("prompt.submit");
		page.handleInput("\r");
		await settle();
		const rejected = stripTerminalSequences(page.render(100).join("\n"));
		assert.ok(rejected.includes("Fix this"), "a rejected handoff stays actionable");
		assert.ok(!rejected.includes("Sent to chat"));
		client.responses.set("prompt.submit", {});
		page.handleInput("\r");
		await settle();
		const submits = client.requests.filter(request => request.method === "prompt.submit");
		assert.equal(submits.length, 3, "one original turn, one rejected handoff, and one accepted handoff");
		assert.equal(submits.at(-1)?.params.sessionId, SESSION_A.id);
		assert.match(String(submits.at(-1)?.params.text), /rules engine crashed while parsing the diff/);
		assert.match(String(submits.at(-1)?.params.text), /install\.sh/);
		const sessionsAfter = client.requests.filter(request => request.method === "session.create").length;
		assert.equal(sessionsAfter, sessionsBefore, "the fix never opens a new chat");
	} finally {
		await app.exit();
	}
});

test("Changes does not display another chat's cached or late results", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 100, rows: 26 } as TUI["terminal"];
	await app.start();
	try {
		await app.submitTurn("Open the first chat");
		const oldTurn = {
			id: "old-turn", sessionId: SESSION_A.id, prompt: "Previous chat changed this",
			stats: { additions: 1, deletions: 0 }, files: [],
		};
		let finishOld!: (result: unknown) => void;
		const settle = () => new Promise(resolve => setImmediate(resolve));
		client.responses.set("changes.list", { turns: [oldTurn] });
		client.responses.set("changes.diff", { files: [] });
		app.openChanges();
		await settle();
		const text = () => stripTerminalSequences(tui.focused!.render(100).join("\n"));
		assert.match(text(), /Previous chat changed this/);
		client.responses.set("changes.list", new Promise(resolve => { finishOld = resolve; }));
		app.openChanges();
		client.responses.set("changes.list", { turns: [{ ...oldTurn, id: "new-turn", sessionId: SESSION_B.id, prompt: "Current chat changed this" }] });
		app.switchTo(SESSION_B, []);
		app.openChanges();
		assert.doesNotMatch(text(), /Previous chat changed this/);
		finishOld({ turns: [oldTurn] });
		await settle();
		assert.match(text(), /Current chat changed this/);
		assert.doesNotMatch(text(), /Previous chat changed this/);
	} finally {
		await app.exit();
	}
});

test("status sessions are listed with the current session first but only in-flight turns are active", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	tui.terminal = { columns: 110, rows: 26 } as TUI["terminal"];
	client.responses.set("interop.status", { platforms: [] });
	client.responses.set("mcp.list", { servers: [] });
	client.responses.set("shell.jobs", { jobs: [] });
	client.responses.set("prompt.steering", { items: [] });
	client.responses.set("session.list", { sessions: [SESSION_B, SESSION_A] });
	await app.start();
	try {
		await app.submitTurn("Open the current chat");
		app.openStatus();
		await new Promise(resolve => setImmediate(resolve));
		const rows = tui.focused!.render(110).map(stripTerminalSequences);
		const active = rows.findIndex(row => /❯ session A\s+active/.test(row));
		const inactive = rows.findIndex(row => /session B\s+inactive/.test(row));
		assert.ok(active >= 0 && inactive > active, "the running current session comes first and the saved session stays inactive");
		assert.ok(rows.some(row => row.includes("Project: tmp")));
	} finally {
		await app.exit();
	}
});

test("Escape requires two deliberate presses to stop a running turn", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	await app.submitTurn("work");
	client.emit(ev({ type: "message.start" }));
	const input = (app as unknown as { onInput(data: string): unknown }).onInput.bind(app);
	input("\x1b");
	assert.equal(client.requests.filter((request) => request.method === "prompt.cancel").length, 0);
	input("\x1b");
	assert.equal(client.requests.filter((request) => request.method === "prompt.cancel").length, 1);
	await app.exit();
});

test("a Kiro quota error switches the current chat to the fallback", async () => {
	const { app, client } = makeApp({ ...SESSION_A, provider: "kiro", model: "auto" });
	await app.start();
	client.responses.set("prompt.submit", { turnId: "t1" });
	await app.submitTurn("hello");
	client.responses.set("auth.kiro.fallback", {
		provider: "chatgpt",
		model: "gpt-6.1-sol",
		reason: null,
	});
	client.responses.set("session.setModel", {
		sessionId: SESSION_A.id,
		model: "gpt-6.1-sol",
		provider: "chatgpt",
	});

	client.emit(
		ev({
			turnId: "t1",
			type: "error",
			message:
				"agent turn failed: Kiro usage limit reached (HTTP 402): Monthly request limit exceeded.",
		}),
	);
	await new Promise((resolve) => setTimeout(resolve, 0));

	assert.ok(
		client.requests.some((request) => request.method === "auth.kiro.fallback"),
		"fallback route was requested",
	);
	const switched = client.requests.find((request) => request.method === "session.setModel");
	assert.equal(switched?.params.provider, "chatgpt");
	assert.equal(switched?.params.model, "gpt-6.1-sol");
	await app.exit();
});

test("a blank startup chat is not persisted until the first message", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	assert.equal(app.session(), undefined, "no session is opened for an empty chat");
	assert.equal(
		client.requests.filter((request) => request.method === "session.create").length,
		0,
		"starting the CLI never writes a session",
	);

	client.responses.set("prompt.submit", { turnId: "t1" });
	await app.submitTurn("first real message");
	const created = client.requests.filter((request) => request.method === "session.create");
	assert.equal(created.length, 1, "the first message creates exactly one session");
	assert.equal(created[0]!.params.title, "first real message", "the message seeds the title");
	assert.equal(app.session()?.id, SESSION_A.id);

	client.emit(ev({ type: "message.start", turnId: "t1" }));
	client.emit(ev({
		type: "message.complete",
		turnId: "t1",
		text: "done",
		tokens: 0,
		iterations: 1,
		toolCalls: 0,
		cancelled: false,
	}));
	await app.submitTurn("second message");
	assert.equal(
		client.requests.filter((request) => request.method === "session.create").length,
		1,
		"later messages reuse the session",
	);
	await app.exit();
});

test("/new keeps its title as a client-side draft", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	app.newSession("Named draft");
	assert.equal(app.session(), undefined, "/new does not open a session");
	assert.equal(
		client.requests.filter((request) => request.method === "session.create").length,
		0,
		"/new stays client-side until a message is sent",
	);

	client.responses.set("prompt.submit", { turnId: "t2" });
	await app.submitTurn("hello");
	const created = client.requests.filter((request) => request.method === "session.create");
	assert.equal(created.length, 1);
	assert.equal(created[0]!.params.title, "Named draft", "the pending title survives until creation");
	await app.exit();
});

test("Enter during a turn queues steering and clears the accepted draft", async () => {
	const { app, tui, client } = makeApp(SESSION_A);
	await app.start();
	await app.submitTurn("work");
	client.responses.set("prompt.steer", { queued: true, items: [{ id: "q1", text: "change direction" }] });
	const editor = widgetWith(tui, "setText") as unknown as { setText(text: string): void; getText(): string };
	editor.setText("change direction");
	await (app as unknown as { submit(text: string): Promise<void> }).submit("change direction");
	assert.equal(client.requests.filter((r) => r.method === "prompt.submit").length, 1);
	assert.equal(client.requests.find((r) => r.method === "prompt.steer")?.params.text, "change direction");
	assert.equal(editor.getText(), "");
	client.emit(ev({ type: "steering.update", items: [], delivered: [{ id: "q1", text: "change direction" }] }));
	client.emit(ev({ type: "message.complete", text: "done", tokens: 0, iterations: 1, toolCalls: 0, cancelled: false }));
	await app.exit();
});

test("steering racing with turn completion starts the next turn once", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	await app.submitTurn("work");
	client.emit(ev({ type: "message.start" }));
	client.responses.set("prompt.steer", { queued: false });
	client.responses.set("prompt.steering", { items: [] });
	await (app as unknown as { submit(text: string): Promise<void> }).submit("late steering");
	client.emit(ev({ type: "message.complete", text: "done", tokens: 0, iterations: 1, toolCalls: 0, cancelled: false }));
	await new Promise((resolve) => setTimeout(resolve, 20));
	const calls = client.requests.filter((r) => r.method === "prompt.submit");
	assert.equal(calls.length, 2);
	assert.equal(calls[1]?.params.text, "late steering");
	await app.exit();
});

test("a fast delivery event cannot be overwritten by the queue RPC snapshot", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	await app.submitTurn("work");
	const original = client.request.bind(client);
	client.request = async <T>(method: string, params: Record<string, unknown> = {}): Promise<T> => {
		if (method !== "prompt.steer") return original<T>(method, params);
		const item = { id: "fast", text: "steer" };
		client.emit(ev({ type: "steering.update", items: [item], delivered: [] }));
		client.emit(ev({ type: "steering.update", items: [], delivered: [item] }));
		return { queued: true, items: [item] } as T;
	};
	await (app as unknown as { submit(text: string): Promise<void> }).submit("steer");
	const state = app as unknown as { activity: { queue(id: string): unknown[] } };
	assert.deepEqual(state.activity.queue(SESSION_A.id), []);
	await app.exit();
});

test("a failed follow-up read leaves queued steering available for retry", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	app.switchTo(SESSION_A, []);
	const state = app as unknown as { activity: { setQueue(id: string, items: unknown[]): void; queue(id: string): unknown[] }; flushSteering(id: string): Promise<void> };
	const item = { id: "retained", text: "follow up" };
	state.activity.setQueue(SESSION_A.id, [item]);
	await state.flushSteering(SESSION_A.id);
	assert.deepEqual(state.activity.queue(SESSION_A.id), [item]);
	assert.equal(client.requests.filter((r) => r.method === "prompt.submit").length, 0);
	client.responses.set("prompt.steering", { items: [item] });
	await state.flushSteering(SESSION_A.id);
	assert.equal(client.requests.find((r) => r.method === "prompt.submit")?.params.steeringId, item.id);
	assert.deepEqual(state.activity.queue(SESSION_A.id), []);
	await app.exit();
});

test("large queued prompts are not combined beyond the gateway input limit", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	app.switchTo(SESSION_A, []);
	const items = [{ id: "one", text: "a".repeat(12000) }, { id: "two", text: "b".repeat(12000) }];
	const state = app as unknown as { activity: { setQueue(id: string, items: unknown[]): void; queue(id: string): unknown[] }; flushSteering(id: string): Promise<void> };
	state.activity.setQueue(SESSION_A.id, items);
	client.responses.set("prompt.steering", { items });
	await state.flushSteering(SESSION_A.id);
	const sent = client.requests.find((r) => r.method === "prompt.submit");
	assert.equal(sent?.params.text, items[0]!.text);
	assert.equal(sent?.params.steeringId, "one");
	assert.deepEqual(state.activity.queue(SESSION_A.id), [items[1]]);
	await app.exit();
});

test("retired steering and tool events cannot mutate the current activity", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	client.responses.set("prompt.submit", { turnId: "old" });
	await app.submitTurn("first");
	client.emit(ev({ type: "message.start", turnId: "old" }));
	client.emit(ev({ type: "message.complete", turnId: "old", text: "done", tokens: 0, iterations: 1, toolCalls: 0, cancelled: false }));
	client.responses.set("prompt.submit", { turnId: "new" });
	await app.submitTurn("second");
	client.emit(ev({ type: "message.start", turnId: "new" }));
	client.emit(ev({ type: "tool.start", turnId: "new", id: "live", name: "read_file", args: {} }));
	client.emit(ev({ type: "steering.update", turnId: "old", items: [{ id: "stale", text: "old" }], delivered: [{ id: "stale", text: "old" }] }));
	client.emit(ev({ type: "message.complete", turnId: "old", text: "done", tokens: 0, iterations: 1, toolCalls: 0, cancelled: false }));
	const state = app as unknown as {
		activity: { queue(id: string): unknown[] };
		turnSession: { activeTools: Map<string, Map<string, string>> };
	};
	assert.deepEqual(state.activity.queue(SESSION_A.id), []);
	assert.equal(state.turnSession.activeTools.get(SESSION_A.id)?.has("live"), true);
	await app.exit();
});

test("an interrupted turn does not restart itself to consume queued steering", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	await app.submitTurn("work");
	client.emit(ev({ type: "message.start" }));
	client.emit(ev({ type: "steering.update", items: [{ id: "q", text: "pending" }], delivered: [] }));
	client.emit(ev({ type: "message.complete", text: "", tokens: 0, iterations: 1, toolCalls: 0, cancelled: true }));
	await new Promise((resolve) => setTimeout(resolve, 20));
	assert.equal(client.requests.filter((r) => r.method === "prompt.submit").length, 1);
	await app.exit();
});

test("cancel() does not set running=false while the turn is still in-flight", async () => {
	const { app, tui, client } = makeApp(SESSION_A);
	await app.start();

	// Submit a prompt — this sets running=true and sends prompt.submit
	const submitPromise = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("hello");
	// Let the prompt.submit request go through
	await new Promise((r) => setTimeout(r, 10));

	// Now cancel — the turn is still in-flight, so running should stay true
	(app as unknown as { cancel: () => void }).cancel();

	// AH-031: the composer stays enabled during turns so safe commands work;
	// running/footer still reflect the in-flight turn.
	const editor = widgetWith(tui, "disableSubmit");
	assert.equal(editor.disableSubmit, false, "editor stays enabled for safe commands during turn");

	// Footer status should still be "working"
	const footer = widgetWith(tui, "status");
	assert.equal(footer.status, "working", "footer status stays working after cancel while turn is in-flight");

	// Clean up: resolve the prompt.submit so the promise doesn't hang
	client.responses.set("prompt.submit", {});
	await submitPromise;
	await app.exit();
});

test("cancelled turn stays tracked until completion before another prompt is submitted", async () => {
	const { app, client } = makeApp(SESSION_A);
	client.responses.set("prompt.cancel", {});
	await app.start();
	const prompt = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn.bind(app);
	await prompt("first");
	(app as unknown as { cancel: () => void }).cancel();
	await prompt("too soon");
	assert.equal(client.requests.filter((item) => item.method === "prompt.submit").length, 1);
	client.emit(ev({ type: "message.complete", text: "", tokens: 0, iterations: 0, toolCalls: 0, cancelled: true }));
	await prompt("after completion");
	assert.equal(client.requests.filter((item) => item.method === "prompt.submit").length, 2);
	await app.exit();
});

test("message.complete for a non-current session is still processed (in-flight turn tracking)", async () => {
	const { app, tui, client } = makeApp(SESSION_A);
	await app.start();

	// Submit a prompt on session A
	const submitPromise = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("hello");
	await new Promise((r) => setTimeout(r, 10));

	// Switch to session B while the turn is still in flight
	client.responses.set("session.resume", { session: SESSION_B, history: [] });
	app.switchTo(SESSION_B, []);

	// Now the completion event arrives for session A (the old session)
	client.emit(
		ev({ type: "message.complete", text: "done", tokens: 42, iterations: 1, toolCalls: 0, cancelled: false }),
	);

	// The app should have processed it: running should be false
	const editor = widgetWith(tui, "disableSubmit");
	assert.equal(editor.disableSubmit, false, "running is false after in-flight turn completes on old session");

	// Clean up
	client.responses.set("prompt.submit", {});
	await submitPromise;
	await app.exit();
});

test("events for sessions with no in-flight turn are ignored", async () => {
	const { app, tui, client } = makeApp(SESSION_A);
	await app.start();

	// Emit an event for a session that has no in-flight turn
	client.emit(
		ev({ type: "message.complete", text: "done", tokens: 42, iterations: 1, toolCalls: 0, cancelled: false }),
	);

	// The app should NOT have processed it: running should still be false (it was never set)
	// and no tokens should have been added
	const footer = widgetWith(tui, "tokens");
	assert.equal(footer.tokens, 0, "no tokens added for event from session with no in-flight turn");

	await app.exit();
});

test("the context gauge follows live usage and refreshes after compaction", async () => {
	const { app, tui, client } = makeApp(SESSION_A);
	await app.start();
	await app.submitTurn("hello");
	client.emit(ev({ type: "message.start" }));
	const footer = widgetWith(tui, "contextTokens") as unknown as {
		tokens: number; contextTokens: number; contextPercent: number | null;
	};
	assert.equal(footer.contextTokens, 0);

	client.emit(ev({ type: "usage", tokens: 1_200, contextTokens: 10_000, contextPercent: 5 }));
	assert.equal(footer.contextTokens, 10_000);
	assert.equal(footer.contextPercent, 5);

	client.emit(ev({ type: "message.complete", tokens: 1_200, text: "done", iterations: 1, toolCalls: 0, cancelled: false }));
	client.responses.set("context.get", { totalTokens: 8_000, budget: 200_000 });
	client.emit(ev({ type: "context.compacted" }));
	await Promise.resolve();
	await Promise.resolve();
	assert.equal(footer.contextTokens, 8_000);
	assert.equal(footer.contextPercent, 4);
	assert.equal(footer.tokens, 1_200, "compaction does not change accounting");

	client.responses.set("context.compress", { compressed: true });
	client.responses.set("context.get", { totalTokens: 4_000, budget: 200_000 });
	await app.request("context.compress", { sessionId: SESSION_A.id });
	await Promise.resolve();
	assert.equal(footer.contextTokens, 4_000);
	await app.exit();
});

test("late events from a previous session do not enter the current transcript", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();
	await (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("hello");
	app.switchTo(SESSION_B, []);

	const internal = app as unknown as { transcript: { apply: (event: GatewayEvent) => unknown } };
	let applied = 0;
	internal.transcript.apply = () => {
		applied++;
		return undefined;
	};
	client.emit(ev({ type: "message.delta", text: "from A" }));
	client.emit(ev({ type: "message.complete", text: "from A", tokens: 3, iterations: 1, toolCalls: 0, cancelled: false }));
	assert.equal(applied, 0);
	await app.exit();
});

// ─── Bug fix tests ─────────────────────────────────────────────────────────

test("onGatewayExit clears in-flight tracking so session is not permanently locked", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();

	// Submit a prompt — this adds to inFlight
	const submitPromise = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("hello");
	await new Promise((r) => setTimeout(r, 10));

	// Simulate gateway exit
	client.onExit!(1, ["some error"]);

	// Now a new prompt should be accepted (inFlight was cleared)
	client.responses.set("prompt.submit", {});
	await submitPromise;

	const submitPromise2 = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("second");
	await new Promise((r) => setTimeout(r, 10));
	client.responses.set("prompt.submit", {});
	await submitPromise2;

	const submits = client.requests.filter((r) => r.method === "prompt.submit");
	assert.equal(submits.length, 2, "both prompts were submitted after gateway exit cleared in-flight");
	await app.exit();
});

test("cancel() targets the in-flight session, not the current session", async () => {
	const { app, client } = makeApp(SESSION_A);
	await app.start();

	// Submit a prompt on session A
	const submitPromise = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("hello");
	await new Promise((r) => setTimeout(r, 10));

	// Switch to session B while A's turn is still in flight
	app.switchTo(SESSION_B, []);

	// Cancel — should target session A (the in-flight one), not B (the current one)
	(app as unknown as { cancel: () => void }).cancel();

	const cancels = client.requests.filter((r) => r.method === "prompt.cancel");
	assert.equal(cancels.length, 1, "one cancel request sent");
	assert.equal(cancels[0]!.params.sessionId, SESSION_A.id, "cancel targets the in-flight session A, not current session B");

	client.responses.set("prompt.submit", {});
	await submitPromise;
	await app.exit();
});

test("submit() does not clear editor when a turn is in-flight (lost input fix)", async () => {
	const { app, tui, client } = makeApp(SESSION_A);
	await app.start();

	// Submit a prompt — this sets running=true and adds to inFlight
	const submitPromise = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("first");
	await new Promise((r) => setTimeout(r, 10));

	// Try to submit again — should be rejected, editor text preserved
	const editor = widgetWith(tui, "getText") as Component & Record<"getText", () => string>;
	// Set some text in the editor
	const editorWidget = widgetWith(tui, "setText") as Component & Record<"setText", (text: string) => void>;
	editorWidget.setText("second prompt");

	// Call submit directly
	await (app as unknown as { submit: (text: string) => Promise<void> }).submit("second prompt");

	// Editor text should still be "second prompt" (not cleared)
	assert.equal(editor.getText(), "second prompt", "editor text preserved when submit rejected due to in-flight");

	// Only one prompt.submit should have been sent
	const submits = client.requests.filter((r) => r.method === "prompt.submit");
	assert.equal(submits.length, 1, "only one prompt.submit sent");

	client.responses.set("prompt.submit", {});
	await submitPromise;
	await app.exit();
});

test("error event keeps in-flight until completion (AH-032)", async () => {
	const { app, tui, client } = makeApp(SESSION_A);
	await app.start();

	// Submit a prompt
	const submitPromise = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("hello");
	await new Promise((r) => setTimeout(r, 10));

	// Emit an error event for the current session — turn stays in-flight
	// until the terminal message.complete (which carries final accounting
	// and tool-card cleanup). Clearing early would drop that completion.
	client.emit(ev({ type: "error", message: "something went wrong" }));

	// running stays true; composer stays enabled for safe commands (AH-031).
	const editor = widgetWith(tui, "disableSubmit");
	assert.equal(editor.disableSubmit, false, "editor stays enabled after error event");

	// A new prompt is still rejected while the turn is in-flight.
	client.responses.set("prompt.submit", {});
	await submitPromise;

	const submitPromise2 = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("second");
	await new Promise((r) => setTimeout(r, 10));
	// Still only one prompt.submit: the second was rejected as in-flight.
	let submits = client.requests.filter((r) => r.method === "prompt.submit");
	assert.equal(submits.length, 1, "second prompt blocked until terminal completion");
	await submitPromise2;

	// Terminal completion clears the turn; a new prompt is accepted.
	client.emit(
		ev({ type: "message.complete", text: "", tokens: 0, iterations: 0, toolCalls: 0, cancelled: false }),
	);
	const submitPromise3 = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("third");
	await new Promise((r) => setTimeout(r, 10));
	client.responses.set("prompt.submit", {});
	await submitPromise3;

	submits = client.requests.filter((r) => r.method === "prompt.submit");
	assert.equal(submits.length, 2, "prompt accepted after terminal completion");
	await app.exit();
});

test("growth.status quietly pulses the footer after retirement without transcript notices", async () => {
	const { app, client, tui } = makeApp(SESSION_A);
	await app.start();
	app.switchTo(SESSION_A, []);
	const internal = app as unknown as {
		transcript: { addNotice(text: string, kind?: unknown): void };
	};
	const notices: Array<[string, unknown]> = [];
	const orig = internal.transcript.addNotice.bind(internal.transcript);
	internal.transcript.addNotice = (text: string, kind?: unknown) => {
		notices.push([text, kind]);
		orig(text, kind);
	};
	const footer = widgetWith(tui, "tokens");
	const before = footer.render(80).join("\n");
	const rendersBefore = tui.renderCount;
	// No turn is in flight: background evidence still receives a quiet pulse.
	client.emit(ev({ type: "growth.status", stage: "consolidation_done", detail: "3 new memories" }));
	assert.equal(notices.length, 0);
	assert.notEqual(footer.render(80).join("\n"), before);
	assert.ok(tui.renderCount > rendersBefore, "an idle growth acknowledgment requests its first visible frame");
	for (let index = 0; index < 20; index++) client.emit(ev({ type: "growth.status", stage: "rsi_updated" }));
	assert.equal(notices.length, 0);
	// Another session's training never interrupts this transcript.
	client.emit({
		sessionId: SESSION_B.id,
		turnId: "t",
		type: "growth.status",
		stage: "consolidation_done",
		detail: "1 new memory",
	});
	assert.equal(notices.length, 0);
	await app.exit();
});

test("exit() calls the input listener disposer", async () => {
	const { app } = makeApp(SESSION_A);
	await app.start();

	// Track whether the disposer was called
	let disposerCalled = false;
	const originalDisposer = (app as unknown as { inputListenerDisposer?: () => void }).inputListenerDisposer;
	if (originalDisposer) {
		(app as unknown as { inputListenerDisposer: () => void }).inputListenerDisposer = () => {
			disposerCalled = true;
			originalDisposer();
		};
	}

	await app.exit();
	assert.equal(disposerCalled, true, "input listener disposer was called on exit");
});

test("local watchdog warns once and retains the guard (never ownership)", async (t) => {
	t.mock.timers.enable({ apis: ["Date", "setInterval", "setTimeout"], now: 1000 });
	const { app, client } = makeApp(SESSION_A);
	await app.start();

	client.responses.set("prompt.submit", { turnId: "t" });
	const submitPromise = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("hello");
	await submitPromise;

	const internal = app as unknown as {
		turnSession: { inFlight: Set<string> };
		transcript: { apply: (event: GatewayEvent) => unknown };
	};
	// Exercise the real production watchdog callback with fake time.
	t.mock.timers.tick(300_000);
	assert.equal(internal.turnSession.inFlight.has(SESSION_A.id), true, "guard retained after the diagnostic deadline");
	await submitPromise.catch(() => {});
	const second = (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("too soon");
	await second;
	assert.equal(
		client.requests.filter((item) => item.method === "prompt.submit").length,
		1,
		"no resubmission is advertised or accepted on clock alone",
	);

	// A second deadline never repeats the warning.
	let applied = 0;
	const origApply = internal.transcript.apply.bind(internal.transcript);
	internal.transcript.apply = (event) => {
		applied++;
		return origApply(event);
	};
	t.mock.timers.tick(300_000);
	assert.equal(applied, 0, "watchdog warns once and never fabricates turn events");

	// The matching completion still retires and accounts.
	client.emit(ev({ type: "message.start", turnId: "t" }));
	client.emit(ev({ type: "message.complete", text: "late", tokens: 11, iterations: 1, toolCalls: 0, cancelled: false }));
	client.responses.set("prompt.submit", { turnId: "t2" });
	await (app as unknown as { submitTurn: (text: string) => Promise<void> }).submitTurn("after");
	assert.equal(client.requests.filter((item) => item.method === "prompt.submit").length, 2);
	await app.exit();
});

test("writeFile creates new files but never overwrites existing ones", async () => {
	const { app } = makeApp(SESSION_A);
	const dir = await mkdtemp(join(tmpdir(), "machina-writefile-"));
	try {
		const existing = join(dir, "AGENTS.md");
		await writeFile(existing, "# important existing instructions\n", "utf8");
		await assert.rejects(
			() => app.writeFile(existing, "# replacement\n"),
			/Refusing to overwrite existing file/,
		);
		assert.equal(
			await readFile(existing, "utf8"),
			"# important existing instructions\n",
			"existing content must survive a refused write",
		);

		// Fresh files (including missing parent directories) still work.
		const fresh = join(dir, "nested", "notes.md");
		assert.equal(await app.writeFile(fresh, "created\n"), fresh);
		assert.equal(await readFile(fresh, "utf8"), "created\n");
	} finally {
		await rm(dir, { recursive: true, force: true });
	}
});

test("interop.run client clock outlasts the backend run deadline (H22)", async (t) => {
	t.mock.timers.enable({ apis: ["setTimeout"] });
	const tui = new FakeTUI();
	const client = new ChannelClient();
	const app = new App(tui, client as unknown as GatewayClient);

	// /harnesses passes the gateway-surfaced deadline as `timeout` seconds.
	const pending = app.request<{ ok: boolean }>("interop.run", {
		platform: "codex",
		prompt: "run a long task",
		timeout: 300,
	});
	const frame = client.frames[0]!;
	assert.equal(frame.method, "interop.run");

	let settled: unknown;
	void pending.then(
		() => { settled = "resolved"; },
		(error) => { settled = error; },
	);

	// The old fixed 120s budget aborted here while the harness kept running.
	t.mock.timers.tick(120_000);
	await Promise.resolve();
	assert.equal(settled, undefined, "must not abort before the backend deadline");

	// At the backend deadline the client is still waiting for the reply.
	t.mock.timers.tick(180_000);
	await Promise.resolve();
	assert.equal(settled, undefined, "must outlast the backend deadline");

	// The late-but-successful reply lands instead of being discarded.
	client.reply({ ok: true, exitCode: 0 });
	assert.deepEqual(await pending, { ok: true, exitCode: 0 });
});

test("interop.run without a reply still stops waiting on the client clock (H22)", async (t) => {
	t.mock.timers.enable({ apis: ["setTimeout"] });
	const tui = new FakeTUI();
	const client = new ChannelClient();
	const app = new App(tui, client as unknown as GatewayClient);

	const pending = app.request("interop.run", { platform: "codex", prompt: "x", timeout: 300 });
	const rejection = pending.catch((error: Error) => error);

	t.mock.timers.tick(300_000 + 60_000);
	const error = await rejection;
	assert.match((error as Error).message, /interop\.run/);
});

for (const command of ["/settings", "/config"]) {
	test(`${command} cannot change gateway defaults until the active turn stops`, async () => {
		const { app, client } = makeApp(SESSION_A);
		client.responses.set("prompt.cancel", {});
		client.responses.set("config.set", { key: "model", value: "new-model", model: "new-model", provider: "openrouter" });
		client.responses.set("session.setModel", {});
		await app.start();
		try {
			const actions = app as unknown as {
				submit(text: string): Promise<void>;
				submitTurn(text: string): Promise<void>;
				cancel(): void;
				pick(title: string, items: SelectItem[]): Promise<SelectItem | undefined>;
			};
			let menuCalls = 0;
			actions.pick = async (_title, items) => {
				menuCalls++;
				if (menuCalls === 1) return items.find((item) => item.value === "cat-chat");
				if (menuCalls === 2) return items.find((item) => item.value === "model");
				if (menuCalls === 3) return items.find((item) => item.value.includes("::") && !item.value.startsWith("__section__"));
				return undefined;
			};
			await actions.submitTurn("hello");
			await actions.submit(command);
			assert.equal(menuCalls, 0, "the settings menu stays closed during the turn");
			assert.ok(!client.requests.some((entry) => entry.method === "config.set"));

			actions.cancel();
			await actions.submit(command);
			assert.equal(menuCalls, 0, "a cancel request alone does not finish the turn");
			assert.ok(!client.requests.some((entry) => entry.method === "config.set"));

			client.emit(ev({ type: "message.complete", text: "", tokens: 0, iterations: 0, toolCalls: 0, cancelled: true }));
			await actions.submit(command);
			const writes = client.requests.filter((entry) => entry.method === "config.set");
			assert.deepEqual(writes.map((entry) => entry.params.key), ["provider", "model"]);
			assert.ok(writes.every((entry) => entry.params.persist === true));
			assert.ok(client.requests.some((entry) => entry.method === "session.setModel"));
		} finally {
			await app.exit();
		}
	});
}
