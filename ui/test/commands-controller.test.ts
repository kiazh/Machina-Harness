// Command controller behavior over a fake composition root.
//
// These tests cross the same seam as App will after integration: every call
// the controller makes goes through CommandHost, so submit-time routing,
// steering, blank-chat deferral and the secret history guard are observable
// without a terminal or a gateway.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { Component, Editor, TUI } from "@earendil-works/pi-tui";
import { ActivityStore } from "../src/activity.ts";
import { CommandController, commandCarriesSecret, type CommandHost, type LastPrompt } from "../src/app/commands.ts";
import { parseCommand, type ParsedCommand } from "../src/commands.ts";
import type { NoticeKind } from "../src/features/index.ts";
import { ImageDraft, type ImageAttachment } from "../src/images.ts";
import type { SessionInfo } from "../src/protocol.ts";

const SESSION: SessionInfo = {
	id: "session-a",
	title: "Build a calculator",
	model: "longcat",
	provider: "openrouter",
	status: "active",
	lastActivity: null,
};

interface FakeEditor {
	readonly editor: Editor;
	readonly history: string[];
	text(): string;
	setText(text: string): void;
}

function fakeEditor(initial = ""): FakeEditor {
	let value = initial;
	const history: string[] = [];
	const editor = {
		getText: () => value,
		getExpandedText: () => value,
		setText: (next: string) => { value = next; },
		addToHistory: (entry: string) => { history.push(entry); },
	};
	return {
		editor: editor as unknown as Editor,
		history,
		text: () => value,
		setText: (next: string) => { value = next; },
	};
}

interface HarnessOptions {
	session?: SessionInfo;
	running?: boolean;
	inFlight?: boolean;
	failSubmit?: Error;
}

function harness(options: HarnessOptions = {}) {
	const editor = fakeEditor();
	const activity = new ActivityStore();
	const notices: Array<{ text: string; kind: NoticeKind }> = [];
	const calls: {
		create: string[];
		submitted: Array<{ text: string; images: ImageAttachment[] }>;
		steered: string[];
		commands: ParsedCommand[];
		requests: Array<{ method: string; params: Record<string, unknown> | undefined }>;
		running: boolean[];
		commandRunning: boolean[];
		renders: number;
	} = {
		create: [],
		submitted: [],
		steered: [],
		commands: [],
		requests: [],
		running: [],
		commandRunning: [],
		renders: 0,
	};
	let current = options.session;
	let draft = new ImageDraft();
	let epoch = 0;
	let last: LastPrompt = { text: "", sessionId: undefined, images: [] };
	const tui = {
		terminal: { columns: 100, rows: 30 },
		requestRender() { calls.renders++; },
	} as unknown as TUI;

	const host: CommandHost = {
		tui,
		editor: editor.editor,
		activity,
		current: () => current,
		async request<T>(method: string, params?: Record<string, unknown>): Promise<T> {
			calls.requests.push({ method, params });
			return {} as T;
		},
		print(text, kind = "info") {
			notices.push({ text, kind });
		},
		transcriptNotice(text, kind) {
			notices.push({ text, kind });
		},
		isExiting: () => false,
		isTurnRunning: () => options.running ?? false,
		isInFlight: () => options.inFlight ?? false,
		visibleInFlight: () => options.inFlight ?? false,
		async ensureSession(text: string) {
			calls.create.push(text);
			if (!current) current = SESSION;
		},
		async submitTurn(text: string, images: ImageAttachment[] = []) {
			calls.submitted.push({ text, images });
			if (options.failSubmit) throw options.failSubmit;
		},
		async queueSteering(text: string) {
			calls.steered.push(text);
			return true;
		},
		setRunning(running: boolean) {
			calls.running.push(running);
		},
		setCommandRunning(active: boolean) {
			calls.commandRunning.push(active);
		},
		async inspectShellJob(_sessionId: string, _jobId: string) {},
		syncWorkspaceShell() {},
		async openDialog<T>(_create: (close: (value?: T) => void) => Component): Promise<T | undefined> {
			return undefined;
		},
		async runCommand(command: ParsedCommand) {
			calls.commands.push(command);
		},
		imageState: () => ({ pending: 0, epoch, settled: Promise.resolve() }),
		imageDraft: () => draft,
		setImageDraft(next: ImageDraft) {
			draft = next;
		},
		bumpImageEpoch() {
			epoch++;
			return epoch;
		},
		lastPrompt: () => last,
		rememberPrompt(text: string, images: ImageAttachment[], sessionId: string | undefined) {
			last = { text, images, sessionId };
		},
	};

	return {
		controller: new CommandController(host),
		calls,
		editor,
		activity,
		notices,
		current: () => current,
	};
}

test("plain submit materializes a blank chat once and reuses it", async () => {
	const { controller, calls, editor, current } = harness();

	await controller.submit("hello");
	assert.deepEqual(calls.create, ["hello"], "the first message creates the draft chat");
	assert.equal(current()?.id, SESSION.id);
	assert.deepEqual(calls.submitted, [{ text: "hello", images: [] }]);
	assert.deepEqual(editor.history, ["hello"]);
	assert.equal(editor.text(), "", "submit clears the composer only after routing");

	await controller.submit("again");
	assert.deepEqual(calls.create, ["hello"], "later messages reuse the visible session");
	assert.deepEqual(calls.submitted.map((entry) => entry.text), ["hello", "again"]);
	assert.deepEqual(editor.history, ["hello", "again"]);
});

test("a blank chat stays client-side until a real message arrives", async () => {
	const { controller, calls, editor, current } = harness();

	await controller.submit("   ");
	assert.deepEqual(calls.create, [], "whitespace submits nothing");
	assert.deepEqual(calls.submitted, []);
	assert.deepEqual(editor.history, []);
	assert.equal(current(), undefined);

	await controller.submit("/changes");
	assert.deepEqual(calls.create, [], "commands never materialize a draft chat");
	assert.equal(current(), undefined);
	assert.deepEqual(calls.commands, [{ name: "changes", args: "" }]);
});

test("plain text steers while a turn runs instead of submitting a turn", async () => {
	const { controller, calls, editor } = harness({ session: SESSION, running: true, inFlight: true });
	editor.setText("change direction");

	await controller.submit("change direction");
	assert.deepEqual(calls.steered, ["change direction"]);
	assert.deepEqual(calls.submitted, [], "running turns keep the steering path");
	assert.deepEqual(calls.create, []);
});

test("slash commands dispatch without creating a session", async () => {
	const { controller, calls, editor } = harness();

	for (const input of ["/changes", "/activity", "/skills", "/theme dark"]) {
		await controller.submit(input);
	}
	assert.deepEqual(calls.commands, [
		{ name: "changes", args: "" },
		{ name: "activity", args: "" },
		{ name: "skills", args: "" },
		{ name: "theme", args: "dark" },
	]);
	assert.deepEqual(calls.create, []);
	assert.deepEqual(calls.submitted, []);
	assert.deepEqual(editor.history, ["/changes", "/activity", "/skills", "/theme dark"]);
	assert.deepEqual(calls.commandRunning, [true, false, true, false, true, false, true, false]);
});

test("secret-bearing command forms never enter input history", async () => {
	const { controller, calls, editor } = harness({ session: SESSION });

	await controller.submit("/keys set OPENROUTER_API_KEY sk-live-123");
	await controller.submit("/provider add acme http://127.0.0.1:9000/v1 sk-live-456");
	await controller.submit("/sessions");

	assert.deepEqual(calls.commands.map((command) => command.name), ["keys", "provider", "sessions"]);
	assert.deepEqual(editor.history, ["/sessions"], "only the non-secret command is recalled");
	assert.ok(!editor.history.some((entry) => entry.includes("sk-live")));

	assert.equal(commandCarriesSecret(parseCommand("/keys set K V")), true);
	assert.equal(commandCarriesSecret(parseCommand("/KEYS SET K V")), true);
	assert.equal(commandCarriesSecret(parseCommand("/keys list")), false);
	assert.equal(commandCarriesSecret(parseCommand("/provider add acme http://x sk-1")), true);
	assert.equal(commandCarriesSecret(parseCommand("/provider add acme http://x")), false);
	assert.equal(commandCarriesSecret(undefined), false);
});

test("a failed submit restores the draft and surfaces the error", async () => {
	const failure = new Error("gateway exploded");
	const { controller, calls, editor, notices } = harness({ failSubmit: failure });
	editor.setText("hello draft");

	await controller.submit("hello draft");

	assert.deepEqual(calls.submitted, [{ text: "hello draft", images: [] }]);
	assert.equal(editor.text(), "hello draft", "pre-acceptance failure restores the draft");
	assert.ok(
		notices.some((notice) => notice.text === "gateway exploded" && notice.kind === "error"),
		"the failure reaches the transcript",
	);
	assert.deepEqual(calls.running, [false], "the running indicator is reconciled with the guard");
});
