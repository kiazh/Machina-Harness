import assert from "node:assert/strict";
import { test } from "node:test";
import type { SelectItem } from "@earendil-works/pi-tui";
import { parseCommand } from "../src/commands.ts";
import { type AskTextOptions, type FeatureHost, type NoticeKind, runCommand } from "../src/features/index.ts";
import type { OAuthProgressHandle, OAuthProgressOptions } from "../src/features/types.ts";
import type { HostStatusView, SessionInfo } from "../src/protocol.ts";

const SESSION: SessionInfo = {
	id: "11111111-2222-3333-4444-555555555555",
	title: "current",
	model: "m",
	provider: "p",
	status: "active",
	lastActivity: null,
};

const RUNNING: HostStatusView = {
	platform: "windows",
	runtime: "wsl",
	distro: "Ubuntu",
	configured: true,
	running: true,
	session: "machina",
	workspace: "/mnt/c/Users/kiash",
	execution_mode: "ask",
	bind: "127.0.0.1",
	port: 7681,
	browser_url: "https://desktop.tailnet.ts.net",
	tailnet_url: "https://desktop.tailnet.ts.net",
	tailnet_name: "desktop.tailnet.ts.net",
	tailscale_connected: true,
	sshd_running: true,
	ssh_command: 'ssh kiash@desktop -t "wsl -d Ubuntu -- tmux attach -t machina"',
	runtime_ready: true,
	browser_ready: true,
	ssh_ready: true,
	missing: [],
};

class FakeHost implements FeatureHost {
	calls: Array<{ method: string; params: Record<string, unknown> }> = [];
	printed: Array<{ text: string; kind: NoticeKind }> = [];
	picks: Array<string | undefined> = [];
	textQueue: Array<string | undefined> = [];
	pickItems: SelectItem[][] = [];
	responses: Record<string, unknown> = {};
	copied: string[] = [];

	async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
		this.calls.push({ method, params });
		const response = this.responses[method];
		if (response === undefined) throw new Error(`unexpected request ${method}`);
		return response as T;
	}
	print(text: string, kind: NoticeKind = "info") {
		this.printed.push({ text, kind });
	}
	printMarkdown(_text: string) {}
	session() {
		return SESSION;
	}
	async submitTurn(_text: string) {}
	switchTo(_session: SessionInfo, _history: never[]) {}
	updateSession(_session: SessionInfo) {}
	async pick(_title: string, items: SelectItem[]) {
		this.pickItems.push(items);
		const value = this.picks.shift();
		return items.find((item) => item.value === value);
	}
	async pickMany(_title: string, _items: SelectItem[]) {
		return undefined;
	}
	async pickTheme() {
		return undefined;
	}
	async askText(_options: AskTextOptions) {
		return this.textQueue.shift();
	}
	async copyText(text: string) {
		this.copied.push(text);
		return true;
	}
	showOAuthProgress(_options: OAuthProgressOptions): OAuthProgressHandle {
		return { update: () => {}, dispose: () => {} };
	}
	async inspectOutput(_toolId?: string) {}
	profileId() {
		return "default";
	}
	setProfile(_id: string) {}
	sessionAgent() {
		return undefined;
	}
	async writeFile(path: string, _text: string) {
		return `/abs/${path}`;
	}
	onConfig(_result: unknown) {}
	clear() {}
	banner() {}
	restyle() {}
	async exit() {}
	last() {
		return this.printed.at(-1)!;
	}
}

const run = (text: string, host: FakeHost) => runCommand(parseCommand(text)!, host);

test("/host status prints the access paths without exposing the password", async () => {
	const host = new FakeHost();
	host.responses["host.status"] = { status: RUNNING };
	await run("/host status", host);
	assert.match(host.last().text, /Machina host: running/);
	assert.match(host.last().text, /https:\/\/desktop\.tailnet\.ts\.net/);
	assert.match(host.last().text, /ssh kiash@desktop/);
	assert.doesNotMatch(host.last().text, /password/i);
});

test("/host credentials reveals the generated browser login", async () => {
	const host = new FakeHost();
	host.responses["host.credentials"] = { username: "machina", password: "secret-token" };
	await run("/host credentials", host);
	assert.match(host.last().text, /Browser username: machina/);
	assert.match(host.last().text, /Browser password: secret-token/);
});

test("/host clients asks for a platform and prints the exact instructions", async () => {
	const host = new FakeHost();
	host.responses["host.instructions"] = { text: "MacBook instructions" };
	host.picks.push("macos");
	await run("/host clients", host);
	assert.deepEqual(host.calls.at(-1), { method: "host.instructions", params: { client: "macos" } });
	assert.match(host.last().text, /MacBook instructions/);
});

test("/host start and stop call the lifecycle methods", async () => {
	for (const verb of ["start", "stop"]) {
		const host = new FakeHost();
		host.responses[`host.${verb}`] = { status: { ...RUNNING, running: verb === "start" } };
		await run(`/host ${verb}`, host);
		assert.deepEqual(host.calls.at(-1), { method: `host.${verb}`, params: {} });
	}
});

test("bare /host with a running host opens the management menu", async () => {
	const host = new FakeHost();
	host.responses["host.status"] = { status: RUNNING };
	host.responses["host.stop"] = { status: { ...RUNNING, running: false } };
	host.picks.push("stop");
	await run("/host", host);
	assert.equal(host.pickItems[0]?.some((item) => item.value === "credentials"), true);
	assert.deepEqual(host.calls.at(-1), { method: "host.stop", params: {} });
});

test("/host add-key installs a client public key", async () => {
	const host = new FakeHost();
	host.responses["host.addKey"] = { status: RUNNING };
	host.textQueue.push("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest machina-macbook");
	await run("/host add-key", host);
	assert.deepEqual(host.calls.at(-1), {
		method: "host.addKey",
		params: { publicKey: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest machina-macbook" },
	});
	assert.match(host.last().text, /Key installed/);
});

test("bare /host offers to resume a setup that needed a restart", async () => {
	const host = new FakeHost();
	host.responses["host.status"] = { status: { ...RUNNING, pending_setup: true } };
	host.responses["host.plan"] = {
		status: { ...RUNNING, pending_setup: true },
		steps: [],
		pending: { reason: "Restart Windows, then run /host setup again.", options: {} },
	};
	host.responses["host.setup.resume"] = { operationId: "op-2" };
	host.responses["host.setup.status"] = {
		id: "op-2",
		state: "done",
		step: "done",
		message: "Machina host is ready.",
		url: "",
		error: "",
		restart_required: false,
		done: true,
		result: { status: RUNNING, instructions: "MacBook instructions" },
		log: [],
	};
	host.picks.push("resume", "yes");
	await run("/host", host);
	assert.equal(host.pickItems[0]?.[0]?.value, "resume");
	assert.deepEqual(host.calls.find((call) => call.method === "host.setup.resume"), {
		method: "host.setup.resume",
		params: {},
	});
	assert.match(host.last().text, /MacBook instructions/);
});

test("/host advanced passes custom port, session, and full mode", async () => {
	const host = new FakeHost();
	host.responses["host.status"] = { status: RUNNING };
	host.responses["host.plan"] = { status: RUNNING, steps: [] };
	host.responses["host.setup.start"] = { operationId: "op-3" };
	host.responses["host.setup.status"] = {
		id: "op-3",
		state: "done",
		step: "done",
		message: "Machina host is ready.",
		url: "",
		error: "",
		restart_required: false,
		done: true,
		result: { status: RUNNING, instructions: "MacBook instructions" },
		log: [],
	};
	host.picks.push("both", "macos", "full", "yes");
	host.textQueue.push("C:\\work", "9000", "remote", "");
	await run("/host advanced", host);
	assert.deepEqual(host.calls.at(-2), {
		method: "host.setup.start",
		params: {
			workspace: "C:\\work",
			mode: "full",
			access: "both",
			client: "macos",
			public_key: "",
			port: 9000,
			session: "remote",
			allow_full: true,
		},
	});
});

test("/host setup runs the wizard and prints client instructions", async () => {
	const host = new FakeHost();
	host.responses["host.status"] = { status: { ...RUNNING, running: false } };
	host.responses["host.plan"] = { status: { ...RUNNING, running: false }, steps: [] };
	host.responses["host.setup.start"] = { operationId: "op-1" };
	host.responses["host.setup.status"] = {
		id: "op-1",
		state: "done",
		step: "done",
		message: "Machina host is ready.",
		url: "",
		error: "",
		restart_required: false,
		done: true,
		result: { status: RUNNING, instructions: "MacBook instructions" },
		log: [],
	};
	host.picks.push("setup", "both", "macos", "ask", "yes");
	host.textQueue.push("C:\\work", "");
	await run("/host", host);
	assert.deepEqual(host.calls.at(-2), {
		method: "host.setup.start",
		params: {
			workspace: "C:\\work",
			mode: "ask",
			access: "both",
			client: "macos",
			public_key: "",
			port: 7681,
			session: "machina",
		},
	});
	assert.deepEqual(host.calls.at(-1), { method: "host.setup.status", params: { operationId: "op-1" } });
	assert.match(host.last().text, /MacBook instructions/);
});
