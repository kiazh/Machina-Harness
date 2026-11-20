import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, type SelectItem, type TUI } from "@earendil-works/pi-tui";
import { parseCommand } from "../src/commands.ts";
import { type FeatureHost, type NoticeKind, runCommand } from "../src/features/index.ts";
import type { AskTextOptions, OAuthProgressHandle, OAuthProgressOptions } from "../src/features/types.ts";
import type { SessionInfo } from "../src/protocol.ts";
import { OAuthProgress, sanitizeStatus } from "../src/components/OAuthProgress.ts";
import { authorizeConnector } from "../src/features/mcp-oauth.ts";

const SESSION: SessionInfo = {
	id: "11111111-2222-3333-4444-555555555555",
	title: "current",
	model: "m",
	provider: "openrouter",
	status: "active",
	lastActivity: null,
};

class OAuthFakeHost implements FeatureHost {
	calls: Array<{ method: string; params: Record<string, unknown> }> = [];
	printed: Array<{ text: string; kind: NoticeKind }> = [];
	picks: Array<string | undefined> = [];
	textQueue: Array<string | undefined> = [];
	asked: AskTextOptions[] = [];
	current: SessionInfo | undefined = SESSION;
	responses: Record<string, unknown | ((params: Record<string, unknown>) => unknown)> = {};
	progressOptions: OAuthProgressOptions[] = [];
	progressUpdates: Array<{ status: string; loginUrl?: string }> = [];
	progressDisposals = 0;
	copied: string[] = [];
	async copyText(text: string) {
		this.copied.push(text);
		return true;
	}

	async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
		this.calls.push({ method, params });
		const response = this.responses[method];
		if (response === undefined) throw new Error(`unexpected request ${method}`);
		if (typeof response === "function") {
			const fn = response as (p: Record<string, unknown>) => unknown;
			const value = fn(params);
			if (value instanceof Promise) return (await value) as T;
			return value as T;
		}
		return response as T;
	}
	print(text: string, kind: NoticeKind = "info") {
		this.printed.push({ text, kind });
	}
	printMarkdown(_text: string) {}
	session() {
		return this.current;
	}
	async submitTurn(_text: string) {}
	switchTo(_session: SessionInfo, _history: never[]) {}
	updateSession(_session: SessionInfo) {}
	async pick(_title: string, items: SelectItem[]) {
		const value = this.picks.shift();
		return items.find((i) => i.value === value);
	}
	async pickMany(_title: string, _items: SelectItem[]) {
		return undefined;
	}
	async pickTheme() {
		return undefined;
	}
	async askText(options: AskTextOptions): Promise<string | undefined> {
		this.asked.push(options);
		return this.textQueue.shift();
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
	restyle() {}
	async exit() {}
	showOAuthProgress(options: OAuthProgressOptions): OAuthProgressHandle {
		this.progressOptions.push(options);
		return {
			update: (status: string, loginUrl?: string) => {
				this.progressUpdates.push({ status, loginUrl });
			},
			dispose: () => {
				this.progressDisposals++;
			},
		};
	}
	last() {
		return this.printed.at(-1)!;
	}
}

const run = (text: string, host: OAuthFakeHost) => runCommand(parseCommand(text)!, host);

const HTTP_SERVER = {
	id: "docs", display_name: "Docs", transport: "streamable_http",
	state: "disconnected", enabled: true, oauth: { client_mode: "auto" },
};

function authorizeHost(): OAuthFakeHost {
	const host = new OAuthFakeHost();
	host.responses["mcp.list"] = { servers: [HTTP_SERVER] };
	host.responses["mcp.status"] = { server: HTTP_SERVER };
	host.responses["mcp.oauth.status"] = {
		auth: { state: "signed_out", generation: 0 },
		operation: { operationId: "op-1", connectorId: "docs", state: "awaiting_browser", expiresAt: new Date().toISOString() },
	};
	host.responses["mcp.oauth.preview"] = {
		previewId: "preview-1", digest: "digest-1",
		review: {
			connectorId: "docs", revision: 1, endpoint: "https://mcp.example.com/mcp",
			resource: "https://mcp.example.com/mcp", issuer: "https://auth.example.com",
			requestedScopes: ["read"], registrationMode: "dcr",
			endpoints: { authorization: "https://auth.example.com/authorize", token: "https://auth.example.com/token" },
			redirectUri: "http://127.0.0.1:9999/oauth/callback/x",
		},
	};
	host.responses["mcp.oauth.start"] = {
		operation: { operationId: "op-1", connectorId: "docs", state: "preparing", expiresAt: new Date().toISOString() },
	};
	host.responses["mcp.oauth.url"] = { authorizationUrl: "https://auth.example.com/authorize?client_id=x" };
	host.responses["mcp.oauth.open"] = { opened: true };
	host.responses["mcp.oauth.cancel"] = { cancelled: true };
	return host;
}

test("/mcp HTTP add offers OAuth and saves non-secret settings", async () => {
	const host = authorizeHost();
	host.responses["mcp.list"] = { servers: [] };
	host.picks.push("__add__", "streamable_http", "oauth", "auto");
	host.textQueue.push("docs", "Docs", "https://mcp.example.com/mcp", "", "[]", "{}");
	host.responses["mcp.configure"] = { server: { ...HTTP_SERVER, id: "docs" } };
	await run("/mcp", host);
	const configure = host.calls.find((call) => call.method === "mcp.configure");
	assert.ok(configure);
	const config = (configure.params as { config: Record<string, unknown> }).config;
	assert.equal(config.transport, "streamable_http");
	assert.deepEqual(config.oauth, {
		client_mode: "auto", client_id: "", client_metadata_url: "", client_secret_ref: "",
		token_endpoint_auth_method: "none", issuer: "", scope_allowlist: [], callback_port: 0,
	});
	assert.equal((config.oauth as { client_secret_ref: string }).client_secret_ref, "");
});

test("/mcp pre-registered OAuth stores a masked secret and fixed callback port", async () => {
	const host = authorizeHost();
	host.responses["mcp.list"] = { servers: [] };
	host.picks.push("__add__", "streamable_http", "oauth", "preregistered");
	host.textQueue.push(
		"drive",
		"Google Drive",
		"https://drivemcp.googleapis.com/mcp/v1",
		"client-id.apps.googleusercontent.com",
		"SENTINEL-client-secret",
		"8765",
		"",
		'["https://www.googleapis.com/auth/drive.readonly"]',
		"{}",
	);
	host.responses["secrets.set"] = (params: Record<string, unknown>) => ({
		key: params.key, set: true, persisted: true, envFile: "/tmp/.env",
	});
	host.responses["mcp.configure"] = { server: { ...HTTP_SERVER, id: "drive" } };
	host.responses["mcp.preview"] = { digest: "drive-digest", proposal: { endpoint: "https://drivemcp.googleapis.com/mcp/v1" } };
	host.responses["mcp.connect"] = { server: { ...HTTP_SERVER, id: "drive", state: "connected" } };
	await run("/mcp", host);

	const secretSet = host.calls.find((call) => call.method === "secrets.set");
	assert.deepEqual(secretSet?.params, {
		key: "MCP_OAUTH_CLIENT_SECRET", value: "SENTINEL-client-secret", persist: true,
	});
	const configure = host.calls.find((call) => call.method === "mcp.configure");
	const oauth = (configure!.params as { config: { oauth: Record<string, unknown> } }).config.oauth;
	assert.equal(oauth.client_mode, "preregistered");
	assert.equal(oauth.client_id, "client-id.apps.googleusercontent.com");
	assert.equal(oauth.client_secret_ref, "MCP_OAUTH_CLIENT_SECRET");
	assert.equal(oauth.token_endpoint_auth_method, "client_secret_basic");
	assert.equal(oauth.callback_port, 8765);
	assert.ok(host.asked.some((options) => options.secret === true), "secret uses the masked editor");
	assert.ok(!host.printed.some((notice) => notice.text.includes("SENTINEL")), "secret is never echoed");
});

test("/mcp Arcade starter explains setup and accepts the user's own gateway URL", async () => {
	const host = authorizeHost();
	host.responses["mcp.list"] = { servers: [] };
	host.responses["mcp.catalog"] = { entries: [{
		id: "arcade", displayName: "Arcade", kind: "connector", category: "Managed connectors",
		provider: "Arcade", description: "Google Workspace through a personal Arcade gateway.",
		docsUrl: "https://docs.arcade.dev/en/operate/governance/mcp-gateways",
		setup: {
			type: "gateway_url",
			instructions: ["Create a free Arcade gateway.", "Paste its URL below."],
			prompt: "Paste your Arcade MCP gateway URL",
			example: "https://api.arcade.dev/mcp/gw_...",
		},
		config: { id: "arcade", display_name: "Arcade", transport: "streamable_http", oauth: { client_mode: "auto" } },
	}] };
	host.picks.push("setup:arcade");
	host.textQueue.push("https://api.arcade.dev/mcp/gw_test");
	host.responses["mcp.configure"] = { server: { ...HTTP_SERVER, id: "arcade", display_name: "Arcade" } };
	host.responses["mcp.preview"] = { digest: "arcade-digest", proposal: { endpoint: "https://api.arcade.dev/mcp/gw_test" } };
	host.responses["mcp.connect"] = { server: { ...HTTP_SERVER, id: "arcade", display_name: "Arcade", state: "connected" } };
	await run("/mcp", host);

	const configure = host.calls.find((call) => call.method === "mcp.configure");
	const config = (configure!.params as { config: Record<string, unknown> }).config;
	assert.equal(config.id, "arcade");
	assert.equal(config.endpoint, "https://api.arcade.dev/mcp/gw_test");
	assert.equal((config.oauth as Record<string, unknown>).client_mode, "auto");
	assert.ok(host.printed.some((notice) => notice.text.includes("Create a free Arcade gateway")), "setup instructions are shown");
});

test("/mcp GitHub starter stores a masked PAT header reference", async () => {
	const host = authorizeHost();
	host.responses["mcp.list"] = { servers: [] };
	host.responses["mcp.catalog"] = { entries: [{
		id: "github", displayName: "GitHub", kind: "connector", category: "Development",
		provider: "GitHub", description: "GitHub tools", docsUrl: "https://github.com/github/github-mcp-server",
		setup: {
			type: "secret", instructions: ["Create a personal access token."],
			prompt: "Paste a GitHub personal access token", secretKey: "GITHUB_MCP_PAT",
		},
		config: {
			id: "github", display_name: "GitHub", transport: "streamable_http",
			endpoint: "https://api.githubcopilot.com/mcp/", header_refs: { Authorization: "GITHUB_MCP_PAT" },
		},
	}] };
	host.picks.push("setup:github");
	host.textQueue.push("github_pat_SENTINEL");
	host.responses["secrets.set"] = (params: Record<string, unknown>) => ({ key: params.key, set: true, persisted: true, envFile: "/tmp/.env" });
	host.responses["mcp.configure"] = { server: { ...HTTP_SERVER, id: "github", display_name: "GitHub" } };
	host.responses["mcp.preview"] = { digest: "github-digest", proposal: { endpoint: "https://api.githubcopilot.com/mcp/" } };
	host.responses["mcp.connect"] = { server: { ...HTTP_SERVER, id: "github", display_name: "GitHub", state: "connected" } };
	await run("/mcp", host);

	const secretSet = host.calls.find((call) => call.method === "secrets.set");
	assert.deepEqual(secretSet?.params, { key: "GITHUB_MCP_PAT", value: "github_pat_SENTINEL", persist: true });
	const configure = host.calls.find((call) => call.method === "mcp.configure");
	const config = (configure!.params as { config: Record<string, unknown> }).config;
	assert.deepEqual(config.header_refs, { Authorization: "GITHUB_MCP_PAT" });
	assert.ok(host.asked.some((options) => options.secret === true), "PAT uses the masked editor");
	assert.ok(!host.printed.some((notice) => notice.text.includes("SENTINEL")), "PAT is never echoed");
});

test("/mcp stdio add shows no OAuth controls", async () => {
	const host = authorizeHost();
	host.responses["mcp.list"] = { servers: [] };
	host.picks.push("__add__", "stdio");
	host.textQueue.push("local", "Local", "node", "[]", "", "{}");
	host.responses["mcp.configure"] = { server: { id: "local" } };
	await run("/mcp", host);
	const configure = host.calls.find((call) => call.method === "mcp.configure");
	const config = (configure!.params as { config: Record<string, unknown> }).config;
	assert.equal(config.oauth, undefined);
});

test("authorize stops with setup guidance when dynamic registration is unavailable", async () => {
	const host = authorizeHost();
	host.responses["mcp.oauth.preview"] = {
		previewId: "preview-1",
		digest: "digest-1",
		review: {
			connectorId: "docs", revision: 1, endpoint: "https://mcp.example.com/mcp",
			resource: "https://mcp.example.com/mcp", issuer: "https://auth.example.com",
			requestedScopes: ["read"], registrationMode: "preregistered-required",
			endpoints: { authorization: "https://auth.example.com/authorize", token: "https://auth.example.com/token" },
			redirectUri: "http://127.0.0.1:9999/oauth/callback",
		},
	};
	assert.equal(await authorizeConnector(host, "docs"), false);
	assert.deepEqual(host.calls.map((call) => call.method), ["mcp.oauth.preview", "mcp.oauth.cancel"]);
	assert.match(host.last().text, /requires a pre-registered OAuth client/);
	assert.match(host.last().text, /Edit OAuth settings/);
});

test("authorize confirms, starts, polls, and reports without leaking the URL", async () => {
	const host = authorizeHost();
	let urlFetched = false;
	const awaiting = () => ({
		auth: { state: "signed_out", generation: 0 },
		operation: { operationId: "op-1", connectorId: "docs", state: "awaiting_browser", expiresAt: new Date().toISOString() },
	});
	const complete = () => ({
		auth: { state: "authorized", generation: 1, issuer: "https://auth.example.com" },
		operation: { operationId: "op-1", connectorId: "docs", state: "complete", expiresAt: new Date().toISOString() },
	});
	host.responses["mcp.oauth.status"] = () => (urlFetched ? complete() : awaiting());
	host.responses["mcp.oauth.url"] = () => {
		urlFetched = true;
		return { authorizationUrl: "https://auth.example.com/authorize?client_id=x" };
	};
	host.responses["mcp.preview"] = { digest: "connect-digest" };
	host.responses["mcp.connect"] = { server: { ...HTTP_SERVER, state: "connected" } };
	host.picks.push("external:docs", "authorize", "yes");
	await run("/mcp", host);
	const methods = host.calls.map((call) => call.method);
	assert.ok(methods.includes("mcp.oauth.preview"));
	assert.ok(methods.includes("mcp.oauth.start"));
	assert.ok(methods.includes("mcp.oauth.status"));
	assert.ok(methods.includes("mcp.connect"));
	assert.equal(host.last().kind, "success");
	assert.ok(host.printed.some((entry) => entry.kind === "success" && /authorized/.test(entry.text)));
	assert.match(host.last().text, /connected/);
	// The authorization URL appears only in the transient overlay, never printed.
	assert.ok(!host.printed.some((entry) => entry.text.includes("auth.example.com/authorize?")));
	assert.ok(host.progressUpdates.some((update) => (update.loginUrl ?? "").includes("auth.example.com/authorize?")));
	assert.ok(host.progressDisposals >= 1);
});

test("preview cancel sends no start/open/exchange", async () => {
	const host = authorizeHost();
	host.picks.push("external:docs", "authorize", "no");
	await run("/mcp", host);
	const methods = host.calls.map((call) => call.method);
	assert.ok(methods.includes("mcp.oauth.preview"));
	assert.ok(methods.includes("mcp.oauth.cancel"));
	assert.ok(!methods.includes("mcp.oauth.start"));
	assert.ok(!methods.includes("mcp.oauth.open"));
});

test("escape during authorization cancels and clears timers", async () => {
	const host = authorizeHost();
	host.responses["mcp.oauth.status"] = () => new Promise((_resolve, reject) => setTimeout(() => reject(new Error("gone")), 20));
	host.picks.push("external:docs", "authorize", "yes");
	const finished = run("/mcp", host);
	while (host.progressOptions.length === 0) await new Promise((resolve) => setTimeout(resolve, 5));
	host.progressOptions[0]!.onCancel();
	await finished;
	const methods = host.calls.map((call) => call.method);
	assert.ok(methods.includes("mcp.oauth.cancel"));
	assert.ok(host.progressDisposals >= 1);
});

test("external server actions do not expose Forget access", async () => {
	const host = authorizeHost();
	host.picks.push("external:docs", "back");
	await run("/mcp", host);
	assert.equal(host.calls.some((call) => call.method === "mcp.oauth.forget"), false);
});

test("OAuthProgress sanitizes provider text and stays usable when narrow", async () => {
	assert.equal(sanitizeStatus("Title\x1b[31m"), "Title");
	// F33: the real source carries ESC/control bytes (the pasted literal
	// spaces were an export artifact); ordinary spaces must survive all three
	// passes, and OSC titles must be stripped without eating text around them.
	assert.equal(sanitizeStatus("Preparing authorization…"), "Preparing authorization…");
	assert.equal(sanitizeStatus("Authorizing docs"), "Authorizing docs");
	assert.equal(sanitizeStatus("keep  two  spaces"), "keep  two  spaces");
	assert.equal(sanitizeStatus("a\x1b]0;window title\x07b"), "ab");
	// C1 CSI (0x9b) is a control even without ESC.
	assert.equal(sanitizeStatus("a\x9bb"), "a");
	assert.equal(sanitizeStatus("a\x9b31mb"), "ab");
	assert.equal(sanitizeStatus("a\x9b?25lb"), "ab");
	assert.equal(sanitizeStatus("a\x9b1 qz"), "az");
	const progress = new OAuthProgress("Title\x1b[31m", "Status\x00with\x1bcontrols", {
		onOpen: () => {},
		onCancel: () => {},
	}, "https://auth.example.com/authorize?x=1");
	// eslint-disable-next-line no-control-regex
	const strip = (row: string): string => row.replace(/\x1b\[[0-9;]*[A-Za-z]/g, "");
	const rows = progress.render(20).map(strip);
	assert.ok(rows.every((row) => !row.includes("[31m") && !row.includes("\x00")));
	assert.ok(progress.render(80).map(strip).some((row) => row.includes("cancel")));

	let cancelled = false;
	const cancellable = new OAuthProgress("t", "s", { onOpen: () => {}, onCancel: () => {
		cancelled = true;
	} });
	cancellable.handleInput("\x1b");
	assert.equal(cancelled, true);
});

test("OAuthProgress shows the entire login link at a wide width", () => {
	const url = `https://auth.example.com/authorize?state=${"a".repeat(400)}&code_challenge=final`;
	const progress = new OAuthProgress("Authorization", "Waiting", {
		onOpen: () => {}, onCancel: () => {},
	}, url);
	const rows = progress.render(1000).map(stripTerminalSequences);
	assert.ok(rows.join("").includes(url), "full URL must be available");
	assert.ok(rows.every((row) => row.length <= 1000));
});

test("OAuthProgress keeps recovery controls visible and scrolls the complete link in a short terminal", () => {
	const url = `https://auth.example.com/authorize?state=${"a".repeat(400)}&code_challenge=final`;
	const progress = new OAuthProgress("Authorization", "Browser unavailable", {
		onOpen: () => {}, onCopy: () => {}, onCancel: () => {},
	}, url, { terminal: { rows: 12, columns: 40 } } as unknown as TUI);
	const start = progress.render(40).map(stripTerminalSequences);
	assert.equal(start.length, 12, "must fill the full-screen overlay height");
	assert.match(start.join("\n"), /y copy/);
	assert.match(start.join("\n"), /cancel/);
	assert.match(start.join("\n"), /https:\/\/auth.example.com/);
	progress.handleInput("\x1b[F"); // End
	const end = progress.render(40).map(stripTerminalSequences);
	assert.match(end.join(""), /code_challenge=final/);
	assert.match(end.join("\n"), /y copy/);
	assert.ok(end.every((row) => row.length <= 40));
});

test("OAuthProgress copy shortcut invokes copying without cancelling", () => {
	let copied = 0;
	let cancelled = 0;
	const progress = new OAuthProgress("Authorization", "Waiting", {
		onOpen: () => {}, onCancel: () => cancelled++, onCopy: () => copied++,
	}, "https://auth.example.com/authorize?state=x");
	progress.handleInput("y");
	assert.equal(copied, 1);
	assert.equal(cancelled, 0);
	assert.match(progress.render(100).join("\n"), /y copy/);
});

test("authorization review reveals every discovered authority endpoint", async () => {
	const host = authorizeHost();
	const preview = host.responses["mcp.oauth.preview"] as { review: { endpoints: Record<string, string> } };
	preview.review.endpoints.registration = "https://registration.example.com/register";
	preview.review.endpoints.revocation = "https://revocation.example.com/revoke";
	host.picks.push("no");
	await authorizeConnector(host, "docs");
	const review = host.printed.map((entry) => entry.text).join("\n");
	for (const url of Object.values(preview.review.endpoints)) assert.ok(review.includes(url));
});

test("cancelled in-flight status cannot report authorization success", async () => {
	const host = authorizeHost();
	let deliver!: (value: unknown) => void;
	host.responses["mcp.oauth.status"] = () => new Promise((resolve) => { deliver = resolve; });
	host.picks.push("yes");
	const finished = authorizeConnector(host, "docs");
	while (!deliver) await new Promise((resolve) => setTimeout(resolve, 1));
	host.progressOptions[0]!.onCancel();
	deliver({ auth: { state: "authorized", generation: 1 }, operation: { state: "complete" } });
	await finished;
	assert.ok(!host.printed.some((entry) => entry.kind === "success"));
	assert.equal(host.progressUpdates.length, 0);
});

test("permanent status errors terminate without another poll", async () => {
	for (const code of [1002, 1005, -32600, -32601, -32602]) {
		const host = authorizeHost();
		let polls = 0;
		host.responses["mcp.oauth.status"] = () => {
			if (++polls === 1) throw Object.assign(new Error("operation not found"), { code });
			return { auth: { state: "signed_out", generation: 0 }, operation: { state: "cancelled" } };
		};
		host.picks.push("yes");
		await authorizeConnector(host, "docs");
		assert.equal(polls, 1, `permanent RPC error ${code} must stop polling`);
		assert.equal(host.last().kind, "error");
		assert.match(host.last().text, /start again|review again|unavailable|permission|request/i);
	}
});

test("browser failure preserves full URL recovery, copy, and retry in the overlay", async (t) => {
	for (const failure of ["false", "error"]) {
		const host = authorizeHost();
		const url = `https://auth.example.com/authorize?state=${"x".repeat(500)}`;
		host.responses["mcp.oauth.url"] = { authorizationUrl: url };
		let opens = 0;
		host.responses["mcp.oauth.open"] = () => {
			if (++opens === 1) {
				if (failure === "error") throw new Error("browser failed");
				return { opened: false };
			}
			return { opened: true };
		};
		host.picks.push("yes");
		const finished = authorizeConnector(host, "docs");
		t.after(async () => { host.progressOptions[0]?.onCancel(); await finished; });
		while (!host.progressUpdates.some((update) => update.loginUrl === url)) {
			await new Promise((resolve) => setTimeout(resolve, 1));
		}
		host.progressOptions[0]!.onOpen();
		await new Promise((resolve) => setTimeout(resolve, 1));
		const recovery = host.progressUpdates.at(-1)!;
		assert.match(recovery.status, /could not|failed|unavailable/i);
		assert.equal(recovery.loginUrl, url);
		host.progressOptions[0]!.onCopy?.();
		await new Promise((resolve) => setTimeout(resolve, 1));
		assert.deepEqual(host.copied, [url]);
		host.progressOptions[0]!.onOpen();
		await new Promise((resolve) => setTimeout(resolve, 1));
		assert.equal(opens, 2);
		assert.match(host.progressUpdates.at(-1)!.status, /opened|waiting/i);
		host.progressOptions[0]!.onCancel();
		await finished;
		assert.ok(!host.printed.some((entry) => entry.text.includes(url)));
	}
});

test("real gateway wrapper exposes the oauth method boundary", { timeout: 60_000 }, async (t) => {
	const python = process.env.MACHINA_TEST_PYTHON;
	if (!python) {
		t.skip("MACHINA_TEST_PYTHON not set");
		return;
	}
	const { mkdtemp } = await import("node:fs/promises");
	const { tmpdir } = await import("node:os");
	const { join } = await import("node:path");
	const { GatewayClient } = await import("../src/gateway.ts");
	const home = await mkdtemp(join(tmpdir(), "machina-oauth-wrapper-"));
	const token = `oauth-wrapper-${Math.random().toString(36).slice(2)}`;
	const client = new GatewayClient({
		python,
		token,
		env: {
			HOME: home,
			MACHINA_ENV_FILE: join(home, "empty.env"),
			MACHINA_SECRET_BACKEND: "",
			MACHINA_TEST_DATABASE_URL: "",
			MACHINA_GATEWAY_NO_SCHEDULER: "1",
			MACHINA_GATEWAY_TOKEN: token,
		},
	});
	client.start();
	try {
		const list = await client.request<{ servers: unknown[] }>("mcp.list", {}, 30_000);
		assert.deepEqual(list, { servers: [] });
		// Unknown params are rejected before any session, network, or browser work.
		await assert.rejects(
			client.request("mcp.oauth.preview", { id: "x", sessionId: "y", callback: "evil" }, 30_000),
			(error: unknown) => (error as { code?: number }).code === -32602,
		);
		await assert.rejects(
			client.request("mcp.oauth.status", { id: "x", sessionId: "y", operationId: 42 }, 30_000),
			(error: unknown) => (error as { code?: number }).code === -32602,
		);
		await assert.rejects(
			client.request("mcp.call", {}, 30_000),
			(error: unknown) => (error as { code?: number }).code === -32601,
		);
		await client.request("shutdown", {}, 5_000).catch(() => undefined);
	} finally {
		await client.stop();
	}
});
