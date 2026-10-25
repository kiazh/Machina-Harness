// /auth — subscription-provider sign-in for ChatGPT, Grok, and GitHub Copilot.
//
// The gateway owns every browser/device flow and every token store. The UI
// receives public metadata only (status fields, device codes, terminal events).
// No credential value is printed or retained here.

import type {
	ChatGPTAuthStatus,
	ChatGPTImportResult,
	ChatGPTLoginResult,
	ChatGPTStatusResult,
	ConfigSetResult,
	ModelsListResult,
	SubscriptionLoginResult,
	SubscriptionStatus,
	SubscriptionStatusResult,
} from "../protocol.ts";
import { splitSub } from "../commands.ts";
import type { Command, FeatureHost } from "./types.ts";

type Provider = "chatgpt" | "grok" | "copilot";

const SUBCOMMANDS: Array<[string, string]> = [
	["status", "Show sign-in status for all providers"],
	["chatgpt", "OpenAI account: status, sign-in, import-codex, sign-out"],
	["grok", "Grok subscription: status, sign-in, sign-out"],
	["copilot", "GitHub Copilot: status, sign-in, import-gh, sign-out"],
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

async function statuses(host: FeatureHost): Promise<{
	chatgpt: ChatGPTAuthStatus | null;
	grok: SubscriptionStatus | null;
	copilot: SubscriptionStatus | null;
}> {
	const [chatgpt, grok, copilot] = await Promise.all([
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
	]);
	return { chatgpt, grok, copilot };
}

async function showStatus(host: FeatureHost, only?: Provider): Promise<void> {
	const result = await statuses(host);
	const lines: string[] = [];
	if (!only || only === "chatgpt") lines.push(result.chatgpt ? renderAuthStatus(result.chatgpt) : "ChatGPT subscription: status unavailable.");
	if (!only || only === "grok") lines.push(result.grok ? renderGrokStatus(result.grok) : "Grok subscription: status unavailable.");
	if (!only || only === "copilot") lines.push(result.copilot ? renderCopilotStatus(result.copilot) : "GitHub Copilot: status unavailable.");
	lines.push("");
	lines.push("Actions: /auth sign-in · /auth import-codex · /auth sign-out");
	lines.push("More: /auth chatgpt · /auth grok · /auth copilot");
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
		host.print(`Signed in. Run /provider ${provider} (optionally with a model) to activate it.`, "warning");
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
		`Signed in to OpenAI${account}.${expiry} Run /provider chatgpt (optionally with a model) to use the account.`,
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
	return `GitHub Copilot — ${state}`;
}

async function providerMenu(host: FeatureHost, provider: Provider): Promise<void> {
	const status = await providerStatus(host, provider);
	const signedIn = status?.signedIn === true;
	const items: Array<{ value: string; label: string; description?: string }> = [
		{
			value: signedIn ? "sign-out" : "sign-in",
			label: signedIn ? `Sign out of ${provider}` : `Sign in to ${provider}`,
			description: signedIn ? "Remove Machina's token store for this provider" : "Browser/device authorization",
		},
		{ value: "status", label: "Show status" },
	];
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
		{ value: "status", label: "Show all sign-in statuses" },
	];
	const choice = await host.pick("Subscriptions — sign in or out", items);
	if (!choice) return;
	if (choice.value === "status") {
		await showStatus(host);
		return;
	}
	await providerMenu(host, choice.value as Provider);
}

async function dispatch(host: FeatureHost, provider: Provider, sub: string): Promise<void> {
	const command = sub || "menu";
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
	description: "Sign in to ChatGPT, Grok, or GitHub Copilot subscriptions",
	argumentHint: "[chatgpt|grok|copilot] [status|sign-in|sign-out]",
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
					`Unknown /auth option "${sub}". Try: status, sign-in, import-codex, sign-out, or chatgpt/grok/copilot.`,
				);
		}
	},
};
