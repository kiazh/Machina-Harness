// /auth: the ChatGPT-account sign-in command surface. The gateway is faked;
// no network, no browser, and no token material anywhere in the flow.

import assert from "node:assert/strict";
import { test } from "node:test";
import type { SelectItem } from "@earendil-works/pi-tui";
import { parseCommand } from "../src/commands.ts";
import {
	authCommand,
	formatAuthExpiry,
	renderAuthStatus,
	renderKiroStatus,
	renderKiroUsage,
} from "../src/features/auth.ts";
import {
	type FeatureHost,
	type NoticeKind,
	runCommand,
	SLASH_COMMANDS,
} from "../src/features/index.ts";
import type { AskTextOptions, OAuthProgressHandle, OAuthProgressOptions } from "../src/features/types.ts";
import type {
	ChatGPTAuthStatus,
	GatewayEvent,
	KiroGatewayStatus,
	KiroUsage,
	SessionInfo,
} from "../src/protocol.ts";

const SESSION: SessionInfo = {
	id: "11111111-2222-3333-4444-555555555555",
	title: "current",
	model: "m",
	provider: "openrouter",
	status: "active",
	lastActivity: null,
};

const SIGNED_IN: ChatGPTAuthStatus = {
	signedIn: true,
	source: "machina",
	expired: false,
	expiresAt: 1_800_000_000,
	accountId: "acct-ui",
	lastRefresh: "2026-10-14T00:00:00Z",
};

const SIGNED_OUT: ChatGPTAuthStatus = {
	signedIn: false,
	source: null,
	expired: null,
	expiresAt: null,
	accountId: null,
	lastRefresh: null,
};

const KIRO_STOPPED: KiroGatewayStatus = {
	cliInstalled: true,
	loginDetected: true,
	reachable: false,
	managed: false,
	state: "stopped",
	baseUrl: "http://127.0.0.1:8000/v1",
	autostart: true,
	launchConfigured: true,
	detail: "Kiro login detected. Start the local gateway with /auth kiro start.",
	logPath: "/x/.machina/logs/kiro-gateway.log",
};

const KIRO_RUNNING: KiroGatewayStatus = {
	...KIRO_STOPPED,
	reachable: true,
	managed: true,
	state: "running",
	detail: "Local gateway is running (started by Machina).",
};

const KIRO_USAGE: KiroUsage = {
	subscription: "KIRO STUDENT",
	plan: "Q_DEVELOPER_STANDALONE_STUDENT",
	used: 37.5,
	limit: 1000,
	remaining: 962.5,
	percentUsed: 3.75,
	unit: "Credits",
	nextResetAt: "2026-12-01T00:00:00+00:00",
	stale: false,
};

class AuthFakeHost implements FeatureHost {
	calls: Array<{ method: string; params: Record<string, unknown> }> = [];
	printed: Array<{ text: string; kind: NoticeKind }> = [];
	responses: Record<string, unknown | ((params: Record<string, unknown>) => unknown)> = {};
	handlers = new Set<(event: GatewayEvent) => void>();

	async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
		this.calls.push({ method, params });
		const response = this.responses[method];
		if (response === undefined) throw new Error(`unexpected request ${method}`);
		return (typeof response === "function"
			? (response as (p: Record<string, unknown>) => unknown)(params)
			: response) as T;
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
		return items[0];
	}
	async pickMany(_title: string, _items: SelectItem[]) {
		return undefined;
	}
	async pickTheme() {
		return undefined;
	}
	async askText(_options: AskTextOptions): Promise<string | undefined> {
		return undefined;
	}
	async inspectOutput(_toolId?: string) {}
	profileId() {
		return "default";
	}
	setProfile(_userId: string) {}
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
	showOAuthProgress(_options: OAuthProgressOptions): OAuthProgressHandle {
		return { update() {}, dispose() {} };
	}
	onGatewayEvent(handler: (event: GatewayEvent) => void): () => void {
		this.handlers.add(handler);
		return () => {
			this.handlers.delete(handler);
		};
	}
	emit(event: GatewayEvent): void {
		for (const handler of [...this.handlers]) handler(event);
	}
	text(): string {
		return this.printed.map((item) => item.text).join("\n");
	}
}

const run = (text: string, host: AuthFakeHost) => runCommand(parseCommand(text)!, host);

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

function completedEvent(accountId: string | null, expiry: number | null): GatewayEvent {
	return {
		protocolVersion: 2,
		sessionId: "",
		turnId: "",
		type: "auth.chatgpt.completed",
		account_id: accountId,
		expiry,
	};
}

function failedEvent(reason: string): GatewayEvent {
	return { protocolVersion: 2, sessionId: "", turnId: "", type: "auth.chatgpt.failed", reason };
}

// ─── status card ─────────────────────────────────────────────────────────────


test("/auth (bare) prints the signed-in status card with actions", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.status"] = { status: SIGNED_IN };
	host.responses["auth.kiro.status"] = { status: KIRO_STOPPED };
	await run("/auth status", host);
	assert.equal(host.calls[0]!.method, "auth.chatgpt.status");
	assert.deepEqual(
		host.calls.slice(1).map((call) => call.method),
		["auth.grok.status", "auth.copilot.status", "auth.kiro.status"],
	);
	const text = host.text();
	assert.match(text, /signed in via Machina token store/);
	assert.match(text, /acct-ui/);
	assert.match(text, /Expires:?\s+2027-01-15T08:00:00Z/);
	assert.match(text, /Last refresh:?\s+2026-10-14T00:00:00Z/);
	assert.match(text, /Kiro account: local proxy stopped/);
	assert.match(text, /Actions: \/auth sign-in · \/auth import-codex · \/auth sign-out/);
});

test("/auth status reports an honest signed-out state", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.status"] = { status: SIGNED_OUT };
	await run("/auth status", host);
	assert.match(host.text(), /not signed in/);
	assert.match(host.text(), /\/auth sign-in/);
});

test("/auth reports an expired token honestly", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.status"] = {
		status: { ...SIGNED_IN, source: "codex", expired: true },
	};
	await run("/auth status", host);
	assert.match(host.text(), /signed in via Codex CLI credentials \(expired — refreshes on next use\)/);
});

// ─── sign-in ─────────────────────────────────────────────────────────────────


test("/auth sign-in prints the URL and settles on the completed event", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.login"] = { url: "https://auth.example/x?state=s", state: "s" };
	const pending = run("/auth sign-in", host);
	await flush();
	assert.deepEqual(host.calls[0], { method: "auth.chatgpt.login", params: {} });
	assert.match(host.text(), /Open this in your browser/);
	assert.match(host.text(), /https:\/\/auth\.example\/x\?state=s/);
	assert.match(host.text(), /Waiting for the sign-in/);

	host.emit(completedEvent("acct-ui", 1_800_000_000));
	await pending;
	assert.match(host.text(), /Signed in to OpenAI \(acct-ui\)/);
	assert.match(host.text(), /expires 2027-01-15T08:00:00Z/);
	assert.match(host.text(), /Choose a ChatGPT model with \/models to use the account/);
	assert.equal(host.handlers.size, 0, "the settle subscription must be released");
});

test("/auth login is an alias with the same RPC", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.login"] = { url: "https://auth.example/y", state: "s" };
	const pending = run("/auth login", host);
	await flush();
	host.emit(completedEvent(null, null));
	await pending;
	assert.equal(host.calls[0]!.method, "auth.chatgpt.login");
	assert.match(host.text(), /Signed in to OpenAI\./);
});

test("/auth sign-in surfaces a failed event with guidance", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.login"] = { url: "https://auth.example/z", state: "s" };
	const pending = run("/auth sign-in", host);
	await flush();
	host.emit(failedEvent("timeout"));
	await pending;
	assert.match(host.text(), /timed out before the browser callback/);
	assert.equal(host.handlers.size, 0);
});

test("/auth sign-in matches its own events only", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.login"] = { url: "https://auth.example/w", state: "s" };
	const pending = run("/auth sign-in", host);
	await flush();
	host.emit({ protocolVersion: 2, sessionId: "other", turnId: "t", type: "usage", tokens: 1 });
	host.emit({ protocolVersion: 2, sessionId: "other", turnId: "t", type: "message.delta", text: "hi" });
	host.emit(completedEvent("acct-ui", null));
	await pending;
	assert.match(host.text(), /Signed in to OpenAI \(acct-ui\)/);
});

// ─── import / sign-out ───────────────────────────────────────────────────────


test("/auth import-codex reports the import and the resulting status", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.importCodex"] = { imported: true, status: SIGNED_IN };
	await run("/auth import-codex", host);
	assert.equal(host.calls[0]!.method, "auth.chatgpt.importCodex");
	assert.equal(host.printed[0]!.kind, "success");
	assert.match(host.text(), /Imported the Codex CLI's OpenAI credentials/);
	assert.match(host.text(), /acct-ui/);
});

test("/auth import-codex is honest when nothing was imported", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.importCodex"] = { imported: false, status: SIGNED_OUT };
	await run("/auth import-codex", host);
	assert.equal(host.printed[0]!.kind, "warning");
	assert.match(host.text(), /Nothing imported/);
});

test("/auth sign-out reports the removal and codex files left alone", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.logout"] = {
		removed: true,
		status: { ...SIGNED_IN, source: "codex" },
	};
	await run("/auth sign-out", host);
	assert.deepEqual(host.calls, [{ method: "auth.chatgpt.logout", params: {} }]);
	assert.match(host.text(), /token store was removed/);
	assert.match(host.text(), /Codex CLI credentials \(~\/\.codex\/auth\.json\) were left untouched/);
	assert.match(host.text(), /Codex CLI credentials remain/);
});

test("/auth logout alias reports an idempotent sign-out", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.logout"] = { removed: false, status: SIGNED_OUT };
	await run("/auth logout", host);
	assert.equal(host.calls[0]!.method, "auth.chatgpt.logout");
	assert.match(host.text(), /Already signed out/);
});

test("unknown /auth options are rejected with the available subcommands", async () => {
	const host = new AuthFakeHost();
	await run("/auth nope", host);
	assert.match(host.text(), /Unknown \/auth option "nope"/);
	assert.match(host.text(), /status, sign-in, import-codex, sign-out/);
});

// ─── completions and help wiring ─────────────────────────────────────────────


test("/auth offers completion-friendly subcommands", async () => {
	const completions = (await authCommand.getArgumentCompletions!("")) ?? [];
	const values = completions.map((item) => item.value);
	assert.deepEqual(values, ["status", "chatgpt", "grok", "copilot", "kiro", "sign-in", "import-codex", "sign-out"]);
	const signIn = (await authCommand.getArgumentCompletions!("sign-i")) ?? [];
	assert.deepEqual(
		signIn.map((item) => item.value),
		["sign-in"],
	);
	const signOut = (await authCommand.getArgumentCompletions!("sign-o")) ?? [];
	assert.deepEqual(
		signOut.map((item) => item.value),
		["sign-out"],
	);
	assert.equal(await authCommand.getArgumentCompletions!("zzz"), null);
	assert.equal(await authCommand.getArgumentCompletions!("sign-in "), null);
	assert.ok(SLASH_COMMANDS.some((command) => command.name === "auth"));
});

test("/provider points OpenAI-account models at /auth", async () => {
	const host = new AuthFakeHost();
	host.responses["config.get"] = { config: { provider: "openrouter" }, secrets: [] };
	await run("/provider", host);
	assert.match(host.text(), /OpenAI account models need no API key — sign in with \/auth/);
});

test("/keys list points OpenAI-account models at /auth", async () => {
	const host = new AuthFakeHost();
	host.responses["secrets.list"] = { secrets: [], envFile: "/x/.env" };
	await run("/keys list", host);
	assert.match(host.text(), /OpenAI account models need no key — sign in with \/auth/);
});

// ─── pure renderers ──────────────────────────────────────────────────────────


test("renderAuthStatus and formatAuthExpiry handle unknown values", () => {
	assert.equal(formatAuthExpiry(null), "unknown");
	assert.equal(formatAuthExpiry(0), "1970-01-01T00:00:00Z");
	assert.match(renderAuthStatus(SIGNED_OUT), /not signed in/);
	assert.match(renderAuthStatus(SIGNED_IN), /signed in via Machina token store/);
});

// ─── Grok and Copilot subscription surfaces ────────────────────────────────

test("/auth grok sign-in waits for the device-flow event and activates the provider", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.grok.status"] = { status: { signedIn: false, source: null } };
	host.responses["auth.grok.login"] = {
		url: "https://accounts.x.ai/device",
		userCode: "ABCD-EFGH",
		verificationUri: "https://accounts.x.ai/device",
		expiresIn: 300,
	};
	host.responses["models.list"] = { provider: "grok", source: "models.dev", models: [{ id: "grok-4.7" }] };
	host.responses["config.set"] = { key: "provider", value: "grok" };
	host.responses["session.setModel"] = { sessionId: SESSION.id, model: "grok-4.7", provider: "grok" };
	const pending = run("/auth grok sign-in", host);
	await flush();
	assert.match(host.text(), /ABCD-EFGH/);
	host.emit({
		protocolVersion: 2,
		sessionId: "",
		turnId: "",
		type: "auth.grok.completed",
		account_id: "grok-user",
		expiry: 1_800_000_000,
	});
	await pending;
	assert.match(host.text(), /Signed in to Grok/);
	assert.equal(host.handlers.size, 0);
});

test("/auth copilot status is shown alongside the other providers", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.status"] = { status: SIGNED_OUT };
	host.responses["auth.grok.status"] = { status: { signedIn: false, source: null } };
	host.responses["auth.copilot.status"] = {
		status: { signedIn: true, source: "gh", accountId: "octocat", tokenFamily: "fine-grained" },
	};
	await run("/auth status", host);
	assert.match(host.text(), /GitHub Copilot: signed in \(octocat, fine-grained\)/);
	assert.match(host.text(), /\/auth grok/);
});

// ─── Kiro local-gateway lifecycle ───────────────────────────────────────────

test("/auth kiro status is a single bounded RPC", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.kiro.status"] = { status: KIRO_STOPPED };
	await run("/auth kiro status", host);
	assert.deepEqual(host.calls, [{ method: "auth.kiro.status", params: {} }]);
	const text = host.text();
	assert.match(text, /Kiro account: local proxy stopped/);
	assert.match(text, /Endpoint: http:\/\/127\.0\.0\.1:8000\/v1/);
	assert.match(text, /\/auth kiro start/);
	assert.match(text, /Community interop only/);
});

test("/auth kiro start launches the proxy and activates Kiro", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.kiro.start"] = {
		started: true,
		ready: true,
		status: KIRO_RUNNING,
	};
	host.responses["models.list"] = {
		provider: "kiro",
		source: "live",
		models: [{ id: "auto" }],
		fetchError: null,
	};
	host.responses["config.set"] = { key: "provider", value: "kiro" };
	host.responses["session.setModel"] = {
		sessionId: SESSION.id,
		model: "auto",
		provider: "kiro",
	};

	await run("/auth kiro start", host);

	assert.equal(host.calls[0]!.method, "auth.kiro.start");
	assert.ok(host.calls.some((call) => call.method === "models.list"));
	assert.match(host.text(), /Kiro local proxy is running/);
	assert.match(host.text(), /Active provider: kiro/);
	assert.match(host.text(), /model: auto/);
});

test("/auth kiro stop reports a managed stop", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.kiro.stop"] = { stopped: true, status: KIRO_STOPPED };
	await run("/auth kiro stop", host);
	assert.equal(host.calls[0]!.method, "auth.kiro.stop");
	assert.match(host.text(), /Kiro local proxy stopped/);
});

test("/auth kiro setup and login point at the official CLI", async () => {
	const setupHost = new AuthFakeHost();
	await run("/auth kiro setup", setupHost);
	assert.equal(setupHost.calls.length, 0);
	assert.match(setupHost.text(), /does not bundle or auto-install/);
	assert.match(setupHost.text(), /MACHINA_KIRO_GATEWAY_DIR/);
	assert.match(setupHost.text(), /MACHINA_KIRO_AUTOSTART=false/);

	const loginHost = new AuthFakeHost();
	await run("/auth kiro login", loginHost);
	assert.equal(loginHost.calls.length, 0);
	assert.match(loginHost.text(), /kiro-cli login/);
	assert.match(loginHost.text(), /\/auth kiro start/);
});

test("renderKiroStatus shows the actionable error and log path", () => {
	const rendered = renderKiroStatus({
		...KIRO_STOPPED,
		state: "error",
		detail: "Kiro gateway process exited with code 1.",
	});
	assert.match(rendered, /local proxy error/);
	assert.match(rendered, /exited with code 1/);
	assert.match(rendered, /Log: \/x\/\.machina\/logs\/kiro-gateway\.log/);
});

test("/auth kiro usage is one bounded RPC and renders credits", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.kiro.usage"] = { usage: KIRO_USAGE };
	host.responses["auth.kiro.fallback"] = {
		provider: "chatgpt",
		model: "gpt-6.1-sol",
		reason: null,
	};

	await run("/auth kiro usage", host);

	assert.deepEqual(
		host.calls.map((call) => call.method),
		["auth.kiro.usage", "auth.kiro.fallback"],
	);
	const text = host.text();
	assert.match(text, /37\.5 \/ 1,000 Credits used \(3\.75%\)/);
	assert.match(text, /Remaining: 962\.5 Credits/);
	assert.match(text, /KIRO STUDENT/);
	assert.match(text, /Fallback when exhausted: chatgpt \/ gpt-6\.1-sol/);
});

test("renderKiroUsage marks cached values honestly", () => {
	const rendered = renderKiroUsage({ ...KIRO_USAGE, stale: true });
	assert.match(rendered, /cached; the last upstream refresh failed/);
});

test("/auth menu exposes Kiro and routes to its actions", async () => {
	const host = new AuthFakeHost();
	host.responses["auth.chatgpt.status"] = { status: SIGNED_OUT };
	host.responses["auth.grok.status"] = { status: { signedIn: false, source: null } };
	host.responses["auth.copilot.status"] = { status: { signedIn: false, source: null } };
	host.responses["auth.kiro.status"] = { status: KIRO_STOPPED };
	let picks = 0;
	host.pick = async (_title: string, items: SelectItem[]) => {
		picks += 1;
		const value = picks === 1 ? "kiro" : "status";
		return items.find((item) => item.value === value) ?? items[0]!;
	};

	await run("/auth", host);

	assert.ok(picks >= 2);
	assert.match(host.text(), /Kiro account: local proxy stopped/);
	assert.match(host.text(), /\/auth kiro start/);
	assert.ok(host.calls.some((call) => call.method === "auth.kiro.status"));
});
