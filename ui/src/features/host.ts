// `/host`: set up and manage the persistent remote Machina shell.
import type { HostPlanResult, HostSetupStatus, HostStatusView } from "../protocol.ts";
import { sanitizeDisplay } from "../design/text.ts";
import { confirm, options, type Command, type FeatureHost } from "./types.ts";

type ClientKind = "macos" | "windows" | "linux" | "all";

const ACCESS = [
	{ value: "both", label: "Browser + SSH", description: "Recommended. Both paths reach the same tmux shell." },
	{ value: "browser", label: "Browser only", description: "Any device with a browser and Tailscale." },
	{ value: "ssh", label: "SSH only", description: "A terminal on the other device." },
];

const CLIENTS: Array<{ value: ClientKind; label: string; description: string }> = [
	{ value: "macos", label: "macOS", description: "MacBook instructions" },
	{ value: "windows", label: "Windows", description: "Windows laptop instructions" },
	{ value: "linux", label: "Linux", description: "Linux instructions" },
	{ value: "all", label: "Show all", description: "Every client platform" },
];

const MODES = [
	{ value: "ask", label: "Ask", description: "Approve mutating tools per action" },
	{ value: "workspace", label: "Workspace", description: "Approve workspace writes, review outside it" },
	{ value: "sandbox", label: "Sandbox", description: "Contained execution where supported" },
];

const HOSTED_SESSIONS = [
	{
		value: "current",
		label: "Host this Machina session",
		description: "Keep the conversation you are using. Inside tmux, the browser mirrors this terminal exactly.",
	},
	{
		value: "new",
		label: "Start a new Machina session",
		description: "The hosted shell starts with a fresh conversation.",
	},
];

function line(label: string, value: string | null | undefined): string {
	return `${label}: ${value && value.trim() ? sanitizeDisplay(value) : "not configured"}`;
}

async function loadStatus(host: FeatureHost): Promise<HostStatusView> {
	const { status } = await host.request<{ status: HostStatusView }>("host.status");
	return status;
}

function statusText(status: HostStatusView): string {
	const lines = [
		`Machina host: ${status.running ? "running" : "stopped"}`,
		line("Runtime", status.runtime === "wsl" ? `Windows / WSL (${status.distro})` : status.platform),
		line("Workspace", status.workspace),
		line("Mode", status.execution_mode),
		line("Browser", status.browser_url),
		line("SSH", status.ssh_command),
	];
	if (status.missing.length) lines.push(`Missing: ${status.missing.map(sanitizeDisplay).join(", ")}`);
	if (status.pending_setup) lines.push("Pending: a setup is waiting for a Windows restart.");
	return lines.join("\n");
}

async function showStatus(host: FeatureHost): Promise<HostStatusView> {
	const status = await loadStatus(host);
	host.print(statusText(status), status.running ? "plain" : "warning");
	return status;
}

async function showCredentials(host: FeatureHost): Promise<void> {
	const { username, password } = await host.request<{ username: string; password: string }>("host.credentials");
	host.print(`Browser username: ${sanitizeDisplay(username)}\nBrowser password: ${sanitizeDisplay(password)}`, "plain");
}

async function showClients(host: FeatureHost): Promise<void> {
	const choice = await host.pick("Client setup instructions", CLIENTS);
	if (!choice) return;
	const clients: ClientKind[] = choice.value === "all" ? ["macos", "windows", "linux"] : [choice.value as ClientKind];
	for (const client of clients) {
		const { text } = await host.request<{ text: string }>("host.instructions", { client });
		host.print(text, "plain");
	}
}

async function showAccess(host: FeatureHost): Promise<void> {
	const status = await loadStatus(host);
	host.print(statusText(status), "plain");
	if (status.ssh_command && host.copyText) {
		const copy = await host.pick("Access actions", [
			{ value: "copy", label: "Copy SSH command", description: "Put the terminal command on the clipboard" },
			{ value: "clients", label: "Client setup instructions", description: "Browser and SSH steps for another device" },
			{ value: "back", label: "Back" },
		]);
		if (copy?.value === "copy") {
			const copied = await host.copyText(status.ssh_command);
			host.print(copied ? "SSH command copied." : "Clipboard unavailable.", copied ? "success" : "warning");
		} else if (copy?.value === "clients") {
			await showClients(host);
		}
	}
}

async function runLifecycle(host: FeatureHost, method: "host.start" | "host.stop" | "host.restart"): Promise<void> {
	const { status } = await host.request<{ status: HostStatusView }>(method);
	host.print(statusText(status), status.running ? "success" : "warning");
}

async function startHost(host: FeatureHost, hosted: string): Promise<void> {
	const currentSession = host.session();
	if (hosted === "current" && !currentSession) {
		host.print("No active Machina session yet; starting a new one.", "warning");
		hosted = "new";
	}
	const machinaSession = hosted === "current" && currentSession ? currentSession.id : "";
	const label = hosted === "current" ? "this Machina session" : "a new Machina session";
	host.print(`Starting the host with ${label}…`, "info");
	try {
		const { status } = await host.request<{ status: HostStatusView }>("host.start", { machinaSession });
		host.print(statusText(status), status.running ? "success" : "warning");
	} catch (error) {
		host.print(error instanceof Error ? error.message : String(error), "error");
	}
}

async function startFlow(host: FeatureHost): Promise<void> {
	const choice = await host.pick("Start the host with", HOSTED_SESSIONS);
	if (!choice) return;
	await startHost(host, choice.value);
}

async function followSetup(host: FeatureHost, operationId: string, title: string): Promise<void> {
	let currentUrl = "";
	let cancelled = false;
	let final: HostSetupStatus | undefined;
	const progress = host.showOAuthProgress({
		title,
		status: "Starting host setup…",
		onOpen: () => {
			if (currentUrl) void host.request("host.openUrl", { url: currentUrl }).catch(() => undefined);
		},
		onCopy: () => {
			if (currentUrl && host.copyText) void host.copyText(currentUrl).catch(() => undefined);
		},
		onCancel: () => {
			cancelled = true;
			void host.request("host.setup.cancel", { operationId }).catch(() => undefined);
		},
		idleHint: "setup running · c/esc cancel",
	});
	try {
		while (!cancelled) {
			const state = await host.request<HostSetupStatus>("host.setup.status", { operationId });
			currentUrl = state.url || currentUrl;
			progress.update(state.message || state.step, state.url || undefined);
			if (state.done) {
				final = state;
				break;
			}
			await new Promise((resolve) => setTimeout(resolve, 500));
		}
	} catch (error) {
		host.print(error instanceof Error ? error.message : String(error), "error");
		return;
	} finally {
		progress.dispose();
	}
	if (cancelled || !final) return;
	if (final.state === "restart_required") {
		host.print(`${final.message}\n\nRun /host after the restart and choose "Resume setup".`, "warning");
		return;
	}
	if (final.state !== "done" || !final.result) {
		host.print(final.error || final.message || "Host setup failed.", "error");
		return;
	}
	host.print(statusText(final.result.status), "success");
	host.print(final.result.instructions, "plain");
}

async function setupFlow(host: FeatureHost, advanced = false): Promise<void> {
	const current = await loadStatus(host);
	const access = await host.pick("Remote host access", ACCESS);
	if (!access) return;
	const client = await host.pick("Where will you connect from?", CLIENTS.slice(0, 3));
	if (!client) return;
	const modes = advanced
		? [
			...MODES,
			{ value: "full", label: "Full", description: "No approval prompts. Only for a single trusted owner" },
		]
		: MODES;
	const mode = await host.pick("Execution mode for the remote shell", modes);
	if (!mode) return;
	const workspace = await host.askText({
		title: "Host workspace",
		description: "The directory the remote shell opens on this computer",
		initial: current.workspace || "~",
	});
	if (workspace === undefined) return;

	let port = current.port || 7681;
	let session = current.session || "machina";
	if (advanced) {
		const portAnswer = await host.askText({
			title: "Browser bridge port",
			description: "Loopback port for ttyd. The default is fine unless it is taken.",
			initial: String(port),
		});
		if (portAnswer === undefined) return;
		const parsedPort = Number.parseInt(portAnswer.trim(), 10);
		if (!Number.isInteger(parsedPort) || parsedPort < 1 || parsedPort > 65535) {
			host.print("Port must be a whole number between 1 and 65535.", "warning");
			return;
		}
		port = parsedPort;
		const sessionAnswer = await host.askText({
			title: "tmux session name",
			description: "If this session already exists, the host broadcasts it instead of creating another.",
			initial: session,
		});
		if (sessionAnswer === undefined) return;
		session = sessionAnswer.trim() || "machina";
		if (!/^[A-Za-z0-9_-]{1,64}$/.test(session)) {
			host.print("Use only letters, numbers, dashes, and underscores for the session name.", "warning");
			return;
		}
	}

	let publicKey = "";
	if (access.value !== "browser") {
		const keyAnswer = await host.askText({
			title: "Client SSH public key (optional)",
			description: "Paste the ssh-ed25519 line from your connecting device. Leave empty to add it later.",
			initial: "",
		});
		if (keyAnswer === undefined) return;
		publicKey = keyAnswer.trim();
	}
	const request: Record<string, unknown> = {
		workspace: workspace.trim(),
		mode: mode.value,
		access: access.value,
		client: client.value,
		publicKey: publicKey,
		port,
		session,
	};
	if (advanced && mode.value === "full") request.allowFull = true;
	host.print("Checking the host setup…", "info");
	const plan = await host.request<HostPlanResult>("host.plan", request);
	const planned = plan.steps.length
		? plan.steps.map((step) => `• ${sanitizeDisplay(step.label)}`)
		: ["• Verify the existing host setup"];
	const summaryLines = [
		"Set up this computer as a Machina host?",
		`Access: ${access.label}`,
		`Client: ${client.label}`,
		`Mode: ${mode.label}`,
		`Workspace: ${workspace.trim() || "~"}`,
	];
	if (advanced) summaryLines.push(`Port: ${port}`, `Session: ${session}`);
	const summary = [
		...summaryLines,
		"",
		...planned,
		"",
		"This may install WSL packages, start tmux and ttyd, configure Tailscale and OpenSSH, and create a logon entry. Windows may ask for administrator approval.",
	].join("\n");
	host.print(summary, "plain");
	if (!await confirm(host, "Set up this computer as a Machina host?", "Set up host")) return;

	let operationId: string;
	try {
		({ operationId } = await host.request<{ operationId: string }>("host.setup.start", request));
	} catch (error) {
		host.print(error instanceof Error ? error.message : String(error), "error");
		return;
	}
	await followSetup(host, operationId, "Setting up Machina host");
}

async function resumeSetup(host: FeatureHost): Promise<void> {
	const status = await loadStatus(host);
	if (!status.pending_setup) {
		host.print("No host setup is waiting to resume.", "warning");
		return;
	}
	const plan = await host.request<HostPlanResult>("host.plan");
	const reason = plan.pending?.reason || "A setup step needed a Windows restart.";
	host.print(`Resume the host setup that was waiting for a restart?\n\n${reason}`, "plain");
	if (!await confirm(host, "Resume the saved host setup?", "Resume setup")) return;
	try {
		const { operationId } = await host.request<{ operationId: string }>("host.setup.resume");
		await followSetup(host, operationId, "Resuming Machina host setup");
	} catch (error) {
		host.print(error instanceof Error ? error.message : String(error), "error");
	}
}

async function addKey(host: FeatureHost): Promise<void> {
	const key = await host.askText({
		title: "Client SSH public key",
		description: "Paste one ssh-ed25519 or ssh-rsa public key line from the client device.",
		initial: "",
	});
	if (key === undefined || !key.trim()) return;
	try {
		const { status } = await host.request<{ status: HostStatusView }>("host.addKey", {
			publicKey: key.trim(),
		});
		host.print(statusText(status), "success");
		host.print("Key installed. Try the SSH command from the client.", "plain");
	} catch (error) {
		host.print(error instanceof Error ? error.message : String(error), "error");
	}
}

async function openMenu(host: FeatureHost): Promise<void> {
	const status = await loadStatus(host);
	host.print(statusText(status), status.running ? "plain" : "warning");
	const resume = status.pending_setup
		? [{ value: "resume", label: "Resume setup", description: "Continue the setup that needed a Windows restart" }]
		: [];
	const setup = { value: "setup", label: "Set up this computer", description: "Choose access, then host this conversation or start a fresh one" };
	const readyToStart = status.configured && status.runtime_ready;
	const startChoices = readyToStart
		? [
			{ value: "start-current", label: "Host this Machina session", description: "Mirror this terminal in tmux, otherwise resume this conversation" },
			{ value: "start-new", label: "Start a new Machina session", description: "Give the hosted shell a fresh conversation" },
		]
		: [];
	const actions = status.running
		? [
			...resume,
			{ value: "access", label: "Access info", description: "Browser URL, SSH command, and client setup" },
			{ value: "credentials", label: "Show browser credentials", description: "Reveal the generated username and password" },
			{ value: "clients", label: "Client setup instructions", description: "What to run on the MacBook or another laptop" },
			{ value: "add-key", label: "Add a client SSH key", description: "Install another device's public key" },
			{ value: "switch-session", label: "Switch hosted session", description: "Restart with this conversation or a fresh one" },
			{ value: "restart", label: "Restart host", description: "Restart the saved hosted session and browser bridge" },
			{ value: "stop", label: "Stop host", description: "Stop the persistent shell and browser bridge" },
			{ value: "setup", label: "Repair / change setup", description: "Choose a new access path or runtime setting" },
			{ value: "advanced", label: "Advanced setup", description: "Choose the port, session name, or full mode" },
		]
		: [
			...resume,
			...startChoices,
			setup,
			{ value: "advanced", label: "Advanced setup", description: "Choose the port, session name, or full mode" },
			{ value: "clients", label: "Client setup instructions", description: "What to run on the connecting device" },
			{ value: "access", label: "Access info", description: "Show the configured browser and SSH paths" },
		];
	const choice = await host.pick("Machina host", actions);
	if (!choice) return;
	if (choice.value === "resume") await resumeSetup(host);
	else if (choice.value === "access") await showAccess(host);
	else if (choice.value === "credentials") await showCredentials(host);
	else if (choice.value === "clients") await showClients(host);
	else if (choice.value === "add-key") await addKey(host);
	else if (choice.value === "start-current") await startHost(host, "current");
	else if (choice.value === "start-new") await startHost(host, "new");
	else if (choice.value === "switch-session") await startFlow(host);
	else if (choice.value === "stop") await runLifecycle(host, "host.stop");
	else if (choice.value === "restart") await runLifecycle(host, "host.restart");
	else if (choice.value === "setup") await setupFlow(host);
	else if (choice.value === "advanced") await setupFlow(host, true);
}

export const hostCommand: Command = {
	name: "host",
	description: "Set up and manage remote access to this Machina host",
	argumentHint: "[status | setup | advanced | resume | add-key | access | clients | credentials | start [current|new] | stop | restart]",
	getArgumentCompletions: options([
		["status", "Show host state and access paths"],
		["setup", "Install or repair the host runtime"],
		["advanced", "Setup with custom port, session, or full mode"],
		["resume", "Continue a setup that needed a restart"],
		["add-key", "Install a client SSH public key"],
		["access", "Show browser and SSH access"],
		["clients", "Instructions for another device"],
		["credentials", "Show the browser username and password"],
		["start", "Start with this session or a new one"],
		["stop", "Stop the host"],
		["restart", "Restart the saved hosted session"],
	]),
	async run(args, host) {
		const words = args.trim().split(/\s+/).filter(Boolean);
		const sub = words[0] ?? "";
		if (sub === "status") await showStatus(host);
		else if (sub === "setup") await setupFlow(host);
		else if (sub === "advanced") await setupFlow(host, true);
		else if (sub === "resume") await resumeSetup(host);
		else if (sub === "add-key") await addKey(host);
		else if (sub === "access") await showAccess(host);
		else if (sub === "clients") await showClients(host);
		else if (sub === "credentials") await showCredentials(host);
		else if (sub === "start") {
			if (words[1] === "current" || words[1] === "new") await startHost(host, words[1]);
			else await startFlow(host);
		}
		else if (sub === "stop" || sub === "restart") await runLifecycle(host, `host.${sub}`);
		else if (sub) host.print("Usage: /host [status | setup | advanced | resume | add-key | access | clients | credentials | start [current|new] | stop | restart]", "warning");
		else await openMenu(host);
	},
};
