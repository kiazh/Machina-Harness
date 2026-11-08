// /auth — account sign-in for ChatGPT, Grok, and GitHub Copilot, plus the
// lifecycle of the user-installed Kiro local gateway.
//
// The gateway owns every browser/device flow and every token store. The UI
// receives public metadata only (status fields, device codes, terminal events).
// No credential value is printed or retained here. Kiro sign-in itself stays
// in the official CLI; this surface only starts/stops its loopback proxy.

import type {
	ChatGPTAuthStatus,
	ChatGPTImportResult,
	ChatGPTLoginResult,
	ChatGPTStatusResult,
	ConfigSetResult,
	KiroFallback,
	KiroGatewayStatus,
	KiroStartResult,
	KiroStatusResult,
	KiroStopResult,
	KiroUsage,
	KiroUsageResult,
	ModelsListResult,
	SubscriptionLoginResult,
	SubscriptionStatus,
	SubscriptionStatusResult,
} from "../protocol.ts";
import { splitSub } from "../commands.ts";
import type { Command, FeatureHost } from "./types.ts";

type Provider = "chatgpt" | "grok" | "copilot" | "kiro";

const SUBCOMMANDS: Array<[string, string]> = [
	["status", "Show sign-in status for all providers"],
	["chatgpt", "OpenAI account: status, sign-in, import-codex, sign-out"],
	["grok", "Grok subscription: status, sign-in, sign-out"],
	["copilot", "GitHub Copilot: status, sign-in, import-gh, sign-out"],
	["kiro", "Kiro account: status, usage, start, stop, setup (local proxy)"],
	["sign-in", "Alias for /auth chatgpt sign-in"],
	["import-codex", "Alias for /auth chatgpt import-codex"],
	["sign-out", "Alias for /auth chatgpt sign-out"],
];

const LOGIN_SETTLE_MS = 310_000;
const POLL_INTERVAL_MS = 1_000;

const PROVIDER_DEFAULT_MODEL: Record<Provider, string> = {
	chatgpt: "gpt-6.1-sol",
	grok: "grok-4.7",
	copilot: "gpt-6.1-sol",
	kiro: "auto",
};

type AuthOutcome =
	| { kind: "completed"; accountId: string | null; expiry: number | null }
	| { kind: "failed"; reason: string }
	| { kind: "timeout" };

function sourceLabel(status: ChatGPTAuthStatus): string {
	if (status.source === "codex") return "Codex CLI credentials";
	if (status.source === "machina") return "Machina token store";
	return "unknown source";
}

export function formatAuthExpiry(value: number | null): string {
	if (typeof value !== "number") return "unknown";
	return new Date(value * 1000).toISOString().replace(".000Z", "Z");
}

export function renderAuthStatus(status: ChatGPTAuthStatus): string {
	if (!status.signedIn) {
		return "ChatGPT subscription: not signed in.";
	}
	const state = status.expired
		? `signed in via ${sourceLabel(status)} (expired — refreshes on next use)`
		: `signed in via ${sourceLabel(status)}`;
	return [
		`ChatGPT subscription: ${state}`,
		`  Account: ${status.accountId ?? "unknown"}`,
		`  Expires: ${formatAuthExpiry(status.expiresAt)}`,
		`  Last refresh: ${status.lastRefresh ?? "—"}`,
	].join("\n");
}

function renderGrokStatus(status: SubscriptionStatus): string {
	if (!status.signedIn) return "Grok subscription: not signed in.";
	const expiry = status.expiresAt
		? `expires ${formatAuthExpiry(status.expiresAt)}${status.expired ? " (refresh on use)" : ""}`
		: "expiry unknown";
	return `Grok subscription: signed in (${status.accountId ?? "xAI account"}, ${expiry}).`;
}

function renderCopilotStatus(status: SubscriptionStatus): string {
	if (!status.signedIn) return "GitHub Copilot: not signed in.";
	return `GitHub Copilot: signed in (${status.accountId ?? "GitHub account"}, ${status.tokenFamily ?? "token"}).`;
}

function kiroStateLabel(status: KiroGatewayStatus): string {
	if (status.reachable) {
		return status.managed ? "local proxy running (managed)" : "local proxy running (external)";
	}
	if (status.state === "starting") return "local proxy starting";
	if (status.state === "error") return "local proxy error";
	return "local proxy stopped";
}

export function renderKiroStatus(status: KiroGatewayStatus): string {
	const cli = status.cliInstalled ? "Kiro CLI installed" : "Kiro CLI not found";
	const login = status.loginDetected ? "login database detected" : "no login database detected";
	const lines = [`Kiro account: ${kiroStateLabel(status)}; ${cli}; ${login}.`];
	if (status.detail) lines.push(`  ${status.detail}`);
	lines.push(`  Endpoint: ${status.baseUrl}`);
	if (!status.launchConfigured && !status.reachable) {
		lines.push("  Launcher: not configured (set MACHINA_KIRO_GATEWAY_DIR).");
	}
	if (status.state === "error" && status.logPath) lines.push(`  Log: ${status.logPath}`);
	return lines.join("\n");
}

async function kiroStatus(host: FeatureHost): Promise<KiroGatewayStatus | null> {
	try {
		const result = await host.request<KiroStatusResult>("auth.kiro.status");
		return result.status;
	} catch {
		return null;
	}
}

async function statuses(host: FeatureHost): Promise<{
	chatgpt: ChatGPTAuthStatus | null;
	grok: SubscriptionStatus | null;
	copilot: SubscriptionStatus | null;
	kiro: KiroGatewayStatus | null;
}> {
	const [chatgpt, grok, copilot, kiro] = await Promise.all([
		host
			.request<ChatGPTStatusResult>("auth.chatgpt.status")
			.then((result) => result.status)
			.catch(() => null),
		host
			.request<SubscriptionStatusResult>("auth.grok.status")
			.then((result) => result.status)
			.catch(() => null),
		host
			.request<SubscriptionStatusResult>("auth.copilot.status")
			.then((result) => result.status)
			.catch(() => null),
		kiroStatus(host),
	]);
	return { chatgpt, grok, copilot, kiro };
}

async function showStatus(host: FeatureHost, only?: Provider): Promise<void> {
	const result = await statuses(host);
	const lines: string[] = [];
	if (!only || only === "chatgpt") lines.push(result.chatgpt ? renderAuthStatus(result.chatgpt) : "ChatGPT subscription: status unavailable.");
	if (!only || only === "grok") lines.push(result.grok ? renderGrokStatus(result.grok) : "Grok subscription: status unavailable.");
	if (!only || only === "copilot") lines.push(result.copilot ? renderCopilotStatus(result.copilot) : "GitHub Copilot: status unavailable.");
	if (!only || only === "kiro") lines.push(result.kiro ? renderKiroStatus(result.kiro) : "Kiro account: status unavailable.");
	lines.push("");
	lines.push("Actions: /auth sign-in · /auth import-codex · /auth sign-out");
	lines.push("More: /auth chatgpt · /auth grok · /auth copilot · /auth kiro");
	host.print(lines.join("\n"), "plain");
}

function failureText(provider: Provider, reason: string): string {
	if (reason === "timeout") {
		return provider === "chatgpt"
			? "Sign-in timed out before the browser callback arrived. Run /auth sign-in to try again."
			: `${provider} sign-in timed out. Run /auth ${provider} sign-in to try again.`;
	}
	if (reason === "access_denied") {
		return provider === "chatgpt" ? "The sign-in was denied in the browser." : `The ${provider} sign-in was denied.`;
	}
	return `${provider} sign-in failed (${reason}). Run /auth ${provider} sign-in to try again.`;
}

async function waitForOutcome(
	host: FeatureHost,
	provider: Provider,
	statusRequest: () => Promise<SubscriptionStatus | null>,
): Promise<AuthOutcome> {
	if (host.onGatewayEvent) {
		const subscribe = host.onGatewayEvent.bind(host);
		const completed = `auth.${provider}.completed`;
		const failed = `auth.${provider}.failed`;
		return await new Promise<AuthOutcome>((resolve) => {
			let unsubscribe: () => void = () => {};
			const timer = setTimeout(() => {
				unsubscribe();
				resolve({ kind: "timeout" });
			}, LOGIN_SETTLE_MS);
			unsubscribe = subscribe((event) => {
				if (event.type !== completed && event.type !== failed) return;
				clearTimeout(timer);
				unsubscribe();
				if (event.type === failed) {
					resolve({ kind: "failed", reason: (event as { reason: string }).reason });
					return;
				}
				const completedEvent = event as { account_id?: string | null; expiry?: number | null };
				resolve({ kind: "completed", accountId: completedEvent.account_id ?? null, expiry: completedEvent.expiry ?? null });
				return;
			});
		});
	}
	const deadline = Date.now() + LOGIN_SETTLE_MS;
	while (Date.now() < deadline) {
		await new Promise<void>((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
		const status = await statusRequest();
		if (status?.signedIn) return { kind: "completed", accountId: status.accountId ?? null, expiry: status.expiresAt ?? null };
	}
	return { kind: "timeout" };
}

/** Apply the newly authorized provider and a catalog-backed model so the
 *  next turn does not keep using an API-key provider/model combination. */
async function activateProvider(host: FeatureHost, provider: Provider): Promise<void> {
	let model = PROVIDER_DEFAULT_MODEL[provider];
	try {
		const catalog = await host.request<ModelsListResult>("models.list", { provider });
		const first = catalog.models.find((entry) => entry.id);
		if (first?.id) model = first.id;
	} catch {
		// The provider default is still a valid fallback.
	}
	try {
		const providerResult = await host.request<ConfigSetResult>("config.set", {
			key: "provider",
			value: provider,
			persist: true,
		});
		host.onConfig(providerResult);
		const modelResult = await host.request<ConfigSetResult>("config.set", {
			key: "model",
			value: model,
			persist: true,
		});
		host.onConfig(modelResult);
		const session = host.session();
		if (session) {
			await host.request("session.setModel", { sessionId: session.id, model, provider });
		}
		host.print(`Active provider: ${provider} · model: ${model}`, "success");
	} catch {
		host.print(`Signed in. Choose a ${provider} model with /models to use the account.`, "warning");
	}
}

async function signInChatGPT(host: FeatureHost): Promise<void> {
	const { url } = await host.request<ChatGPTLoginResult>("auth.chatgpt.login", {});
	host.print(`Open this in your browser to sign in with OpenAI:\n${url}\nWaiting for the sign-in to complete…`, "plain");
	const outcome = await waitForOutcome(host, "chatgpt", async () => {
		const result = await host.request<ChatGPTStatusResult>("auth.chatgpt.status");
		return {
			signedIn: result.status.signedIn,
			source: result.status.source,
			expired: result.status.expired,
			expiresAt: result.status.expiresAt,
			accountId: result.status.accountId,
		};
	});
	if (outcome.kind === "timeout") {
		host.print("The sign-in window closed without a callback. Run /auth chatgpt sign-in to try again.", "warning");
		return;
	}
	if (outcome.kind === "failed") {
		host.print(failureText("chatgpt", outcome.reason), "error");
		return;
	}
	const account = outcome.accountId ? ` (${outcome.accountId})` : "";
	const expiry =
		outcome.expiry === null ? "" : ` Access token expires ${formatAuthExpiry(outcome.expiry)}.`;
	host.print(
		`Signed in to OpenAI${account}.${expiry} Choose a ChatGPT model with /models to use the account.`,
		"success",
	);
	await activateProvider(host, "chatgpt");
}

async function signInGrok(host: FeatureHost): Promise<void> {
	const started = await host.request<SubscriptionLoginResult>("auth.grok.login", {});
	if (started.pending) {
		host.print("A Grok sign-in is already waiting for approval.", "plain");
		return;
	}
	host.print(
		`Open this in your browser to approve Grok:\n${started.url ?? ""}\nCode: ${started.userCode ?? ""}\nWaiting for approval…`,
		"plain",
	);
	const outcome = await waitForOutcome(host, "grok", async () => {
		const result = await host.request<SubscriptionStatusResult>("auth.grok.status");
		return result.status;
	});
	if (outcome.kind === "timeout") {
		host.print("Grok sign-in timed out. Run /auth grok sign-in to try again.", "warning");
		return;
	}
	if (outcome.kind === "failed") {
		host.print(failureText("grok", outcome.reason), "error");
		return;
	}
	host.print(`Signed in to Grok${outcome.accountId ? ` (${outcome.accountId})` : ""}.`, "success");
	await activateProvider(host, "grok");
}

async function signInCopilot(host: FeatureHost): Promise<void> {
	const started = await host.request<SubscriptionLoginResult>("auth.copilot.login", {});
	if (started.pending) {
		host.print("A Copilot sign-in is already waiting for approval.", "plain");
		return;
	}
	host.print(
		`Open ${started.url ?? "https://github.com/login/device"} and enter code: ${started.userCode ?? ""}\nWaiting for approval…`,
		"plain",
	);
	const outcome = await waitForOutcome(host, "copilot", async () => {
		const result = await host.request<SubscriptionStatusResult>("auth.copilot.status");
		return result.status;
	});
	if (outcome.kind === "timeout") {
		host.print("Copilot sign-in timed out. Run /auth copilot sign-in to try again.", "warning");
		return;
	}
	if (outcome.kind === "failed") {
		host.print(failureText("copilot", outcome.reason), "error");
		return;
	}
	host.print(`Signed in to GitHub Copilot${outcome.accountId ? ` (${outcome.accountId})` : ""}.`, "success");
	await activateProvider(host, "copilot");
}

async function importCodex(host: FeatureHost): Promise<void> {
	const result = await host.request<ChatGPTImportResult>("auth.chatgpt.importCodex", {});
	if (result.imported) {
		host.print(`Imported the Codex CLI's OpenAI credentials.\n${renderAuthStatus(result.status)}`, "success");
		await activateProvider(host, "chatgpt");
		return;
	}
	host.print("Nothing imported: no Codex CLI credentials at ~/.codex/auth.json, or Machina already has its own tokens.", "warning");
}

async function importCopilot(host: FeatureHost): Promise<void> {
	const result = await host.request<{ imported: boolean; status: SubscriptionStatus }>("auth.copilot.importGh", {});
	if (result.imported) {
		host.print(`Imported the GitHub CLI credential.\n${renderCopilotStatus(result.status)}`, "success");
		await activateProvider(host, "copilot");
		return;
	}
	host.print("Nothing imported: no gh CLI credential found, or Machina already has its own Copilot token.", "warning");
}

async function signOut(host: FeatureHost, provider: Provider): Promise<void> {
	const method = `auth.${provider}.logout`;
	const result = await host.request<{ removed: boolean; status?: SubscriptionStatus }>(method, {});
	if (provider === "chatgpt") {
		if (result.removed) {
			host.print(
				"Signed out; Machina's OpenAI token store was removed. Codex CLI credentials (~/.codex/auth.json) were left untouched.",
				"success",
			);
		} else {
			host.print("Already signed out — Machina has no OpenAI token store to remove.", "plain");
		}
		if (result.status?.signedIn && result.status.source === "codex") {
			host.print(
				"Codex CLI credentials remain and are imported automatically on the next chatgpt call.",
				"plain",
			);
		}
		return;
	}
	if (result.removed) host.print(`Signed out of ${provider}.`, "success");
	else host.print(`Already signed out of ${provider}.`, "plain");
}

async function providerStatus(host: FeatureHost, provider: Provider): Promise<SubscriptionStatus | null> {
	if (provider === "kiro") return null;
	if (provider === "chatgpt") {
		try {
			const result = await host.request<ChatGPTStatusResult>("auth.chatgpt.status");
			return {
				signedIn: result.status.signedIn,
				source: result.status.source,
				expired: result.status.expired,
				expiresAt: result.status.expiresAt,
				accountId: result.status.accountId,
				lastRefresh: result.status.lastRefresh,
			};
		} catch {
			return null;
		}
	}
	try {
		const result = await host.request<SubscriptionStatusResult>(`auth.${provider}.status`);
		return result.status;
	} catch {
		return null;
	}
}

function providerLabel(provider: Provider, status: SubscriptionStatus | null): string {
	const state = status?.signedIn ? `signed in${status.accountId ? ` as ${status.accountId}` : ""}` : "signed out";
	if (provider === "chatgpt") return `ChatGPT subscription — ${state}`;
	if (provider === "grok") return `Grok subscription — ${state}`;
	if (provider === "kiro") return "Kiro account — local proxy";
	return `GitHub Copilot — ${state}`;
}

function kiroProviderLabel(status: KiroGatewayStatus | null): string {
	if (!status) return "Kiro account — status unavailable";
	if (status.reachable) return `Kiro account — local proxy running${status.managed ? " (managed)" : ""}`;
	if (status.state === "starting") return "Kiro account — local proxy starting";
	if (status.state === "error") return "Kiro account — local proxy error";
	if (status.loginDetected) return "Kiro account — login database detected, proxy stopped";
	if (status.cliInstalled) return "Kiro account — CLI installed, not signed in";
	return "Kiro account — not configured";
}

function errorText(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

async function showKiroStatus(host: FeatureHost): Promise<void> {
	const status = await kiroStatus(host);
	if (!status) {
		host.print("Kiro account: gateway status unavailable.", "warning");
		return;
	}
	host.print(
		[
			renderKiroStatus(status),
			"",
			"Actions: /auth kiro start · /auth kiro stop · /auth kiro usage · /auth kiro setup",
			"Community interop only; see docs/KIRO_PROXY.md and review Kiro's terms.",
		].join("\n"),
		"plain",
	);
}

export function renderKiroUsage(usage: KiroUsage): string {
	const amount = (value: number) =>
		value.toLocaleString("en-US", { maximumFractionDigits: 2 });
	const unit = usage.unit || "Credits";
	const reset = usage.nextResetAt
		? new Date(usage.nextResetAt).toLocaleDateString("en-US", {
				year: "numeric",
				month: "short",
				day: "numeric",
			})
		: "unknown";
	const lines = [
		`Kiro usage: ${amount(usage.used)} / ${amount(usage.limit)} ${unit} used (${usage.percentUsed.toFixed(2)}%)`,
		`Remaining: ${amount(usage.remaining)} ${unit} · resets ${reset}`,
		`Plan: ${usage.subscription}${usage.plan ? ` (${usage.plan})` : ""}`,
	];
	if (usage.stale) lines.push("Values are cached; the last upstream refresh failed.");
	return lines.join("\n");
}

async function showKiroUsage(host: FeatureHost): Promise<void> {
	host.print("Reading Kiro usage…", "plain");
	let result: KiroUsageResult;
	try {
		result = await host.request<KiroUsageResult>("auth.kiro.usage", {});
	} catch (error) {
		host.print(`Kiro usage unavailable: ${errorText(error)}`, "error");
		return;
	}
	host.print(renderKiroUsage(result.usage), "plain");
	try {
		const fallback = await host.request<KiroFallback>("auth.kiro.fallback", {});
		host.print(
			fallback.provider && fallback.model
				? `Fallback when exhausted: ${fallback.provider} / ${fallback.model}`
				: fallback.reason ?? "No Kiro fallback provider is available.",
			fallback.provider ? "plain" : "warning",
		);
	} catch (error) {
		host.print(`Kiro fallback unavailable: ${errorText(error)}`, "warning");
	}
	if (result.usage.remaining <= 0) {
		host.print(
			"Kiro credits are exhausted. Machina will switch this chat to the fallback on the next failed Kiro turn.",
			"warning",
		);
	}
}

async function startKiro(host: FeatureHost): Promise<void> {
	host.print("Starting the local Kiro gateway…", "plain");
	let result: KiroStartResult;
	try {
		result = await host.request<KiroStartResult>("auth.kiro.start", {});
	} catch (error) {
		host.print(`Kiro gateway did not start: ${errorText(error)}`, "error");
		return;
	}
	if (result.ready) {
		host.print(
			result.started ? "Kiro local proxy is running." : "Kiro local proxy was already running.",
			"success",
		);
		await activateProvider(host, "kiro");
		return;
	}
	host.print(
		`Kiro gateway start did not become ready.\n${renderKiroStatus(result.status)}`,
		"warning",
	);
}

async function stopKiro(host: FeatureHost): Promise<void> {
	let result: KiroStopResult;
	try {
		result = await host.request<KiroStopResult>("auth.kiro.stop", {});
	} catch (error) {
		host.print(`Kiro gateway did not stop: ${errorText(error)}`, "error");
		return;
	}
	if (result.stopped) {
		host.print("Kiro local proxy stopped.", "success");
		return;
	}
	host.print(
		`Kiro local proxy was not stopped (only a process Machina started can be stopped).\n${renderKiroStatus(result.status)}`,
		"warning",
	);
}

function showKiroSetup(host: FeatureHost): void {
	host.print(
		[
			"Kiro uses a community local gateway; Machina does not bundle or auto-install it.",
			"1. Install the official Kiro CLI and sign in: kiro-cli login",
			"2. Clone jwadow/kiro-gateway and install its dependencies.",
			"3. Keep it at ~/kiro-gateway, or set MACHINA_KIRO_GATEWAY_DIR to the checkout.",
			"   Builder ID accounts: Machina injects PROFILE_ARN from Kiro IDE's profile.json when present.",
			"4. Start it here: /auth kiro start",
			"5. Pick a model with /models (the full 22-alias catalog is built in).",
			"Auto-start is on by default when a CLI login and checkout are detected; disable it with MACHINA_KIRO_AUTOSTART=false.",
			"Docs: docs/KIRO_PROXY.md",
			"Community interop only: Kiro's terms prohibit third-party harnesses and AWS does not support this path.",
		].join("\n"),
		"plain",
	);
}

async function kiroMenu(host: FeatureHost): Promise<void> {
	const status = await kiroStatus(host);
	const running = status?.reachable === true;
	const items: Array<{ value: string; label: string; description?: string }> = [];
	if (!running) {
		items.push({
			value: "start",
			label: "Start the Kiro local proxy",
			description: status?.launchConfigured
				? "Launch the configured gateway and use Kiro"
				: "A configured gateway checkout is required first",
		});
	}
	if (running && status?.managed) {
		items.push({ value: "stop", label: "Stop the Kiro local proxy", description: "Stop the child Machina started" });
	}
	items.push({ value: "setup", label: "Show gateway setup instructions" });
	items.push({ value: "usage", label: "Show Kiro usage and remaining credits" });
	items.push({ value: "status", label: "Show status" });
	const choice = await host.pick(`${kiroProviderLabel(status)} — choose an action`, items);
	if (!choice) return;
	await dispatch(host, "kiro", String(choice.value));
}

async function providerMenu(host: FeatureHost, provider: Provider): Promise<void> {
	if (provider === "kiro") {
		await kiroMenu(host);
		return;
	}
	const status = await providerStatus(host, provider);
	const signedIn = status?.signedIn === true;
	const items: Array<{ value: string; label: string; description?: string }> = [
		{
			value: "sign-in",
			label: signedIn ? `Sign in again to ${provider}` : `Sign in to ${provider}`,
			description: signedIn ? "Re-run authorization and replace the stored token" : "Browser/device authorization",
		},
	];
	if (signedIn) {
		items.push({ value: "sign-out", label: `Sign out of ${provider}`, description: "Remove Machina's token store for this provider" });
	}
	items.push({ value: "status", label: "Show status" });
	if (provider === "chatgpt") items.push({ value: "import-codex", label: "Import Codex CLI credentials" });
	if (provider === "copilot") items.push({ value: "import-gh", label: "Import gh CLI credentials" });
	const choice = await host.pick(`${providerLabel(provider, status)} — choose an action`, items);
	if (!choice) return;
	await dispatch(host, provider, String(choice.value));
}

async function openAuthMenu(host: FeatureHost): Promise<void> {
	const result = await statuses(host);
	const items = [
		{ value: "chatgpt", label: providerLabel("chatgpt", result.chatgpt), description: "OpenAI account (ChatGPT subscription)" },
		{ value: "grok", label: providerLabel("grok", result.grok), description: "xAI Grok subscription" },
		{ value: "copilot", label: providerLabel("copilot", result.copilot), description: "GitHub Copilot seat" },
		{ value: "kiro", label: kiroProviderLabel(result.kiro), description: "Kiro account through a local OpenAI-compatible proxy (community interop)" },
		{ value: "status", label: "Show all sign-in statuses" },
	];
	const choice = await host.pick("Accounts — sign in, or manage the Kiro local proxy", items);
	if (!choice) return;
	if (choice.value === "status") {
		await showStatus(host);
		return;
	}
	await providerMenu(host, choice.value as Provider);
}

async function dispatch(host: FeatureHost, provider: Provider, sub: string): Promise<void> {
	const command = sub || "menu";
	if (provider === "kiro") {
		if (command === "menu") {
			await kiroMenu(host);
			return;
		}
		if (command === "status") {
			await showKiroStatus(host);
			return;
		}
		if (command === "usage") {
			await showKiroUsage(host);
			return;
		}
		if (command === "start") {
			await startKiro(host);
			return;
		}
		if (command === "stop") {
			await stopKiro(host);
			return;
		}
		if (command === "setup") {
			showKiroSetup(host);
			return;
		}
		if (command === "sign-in" || command === "login") {
			host.print(
				"Kiro sign-in is handled by the official CLI, not Machina. Run `kiro-cli login`, then /auth kiro start. See /auth kiro setup.",
				"plain",
			);
			return;
		}
		throw new Error(
			`Unknown /auth kiro option "${command}". Try: status, start, stop, setup.`,
		);
	}
	if (command === "menu") {
		await providerMenu(host, provider);
		return;
	}
	if (command === "status") {
		await showStatus(host, provider);
		return;
	}
	if (command === "sign-in" || command === "login") {
		if (provider === "chatgpt") await signInChatGPT(host);
		else if (provider === "grok") await signInGrok(host);
		else await signInCopilot(host);
		return;
	}
	if (provider === "chatgpt" && command === "import-codex") {
		await importCodex(host);
		return;
	}
	if (provider === "copilot" && command === "import-gh") {
		await importCopilot(host);
		return;
	}
	if (command === "sign-out" || command === "logout") {
		await signOut(host, provider);
		return;
	}
	throw new Error(`Unknown /auth ${provider} option "${command}". Try: status, sign-in, sign-out${provider === "chatgpt" ? ", import-codex" : provider === "copilot" ? ", import-gh" : ""}.`);
}

export const authCommand: Command = {
	name: "auth",
	description: "Sign in to ChatGPT, Grok, or Copilot — or manage the Kiro local proxy",
	argumentHint: "[chatgpt|grok|copilot|kiro] [status|sign-in|start|stop]",
	getArgumentCompletions: (prefix: string) => {
		const typed = prefix.trimStart().toLowerCase();
		if (/\s/.test(typed)) return null;
		const items = SUBCOMMANDS.filter(([value]) => value.startsWith(typed)).map(
			([value, description]) => ({ value, label: value, description }),
		);
		return items.length ? items : null;
	},
	async run(args, host) {
		const [sub, rest] = splitSub(args);
		switch (sub) {
			case "":
				await openAuthMenu(host);
				return;
			case "status":
				await showStatus(host);
				return;
			case "chatgpt":
			case "openai":
				await dispatch(host, "chatgpt", rest);
				return;
			case "grok":
				await dispatch(host, "grok", rest);
				return;
			case "copilot":
				await dispatch(host, "copilot", rest);
				return;
			case "kiro":
				await dispatch(host, "kiro", rest);
				return;
			case "sign-in":
			case "login":
				await signInChatGPT(host);
				return;
			case "import-codex":
				await importCodex(host);
				return;
			case "sign-out":
			case "logout":
				await signOut(host, "chatgpt");
				return;
			default:
				throw new Error(
					`Unknown /auth option "${sub}". Try: status, sign-in, import-codex, sign-out, or chatgpt/grok/copilot/kiro.`,
				);
		}
	},
};
