// Entry point: `node ui/src/main.ts [--model M] [--provider P] [--session ID] [--mode MODE]`.
// Normally started by `machina` (machina/cli/launcher.py), which sets MACHINA_PYTHON.

import { parseArgs } from "node:util";
import { spawn } from "node:child_process";
import * as path from "node:path";
import * as fs from "node:fs";
import * as crypto from "node:crypto";
import { describeError, uiLog, type LogLevel } from "./debug.ts";
import { GatewayClient } from "./gateway.ts";

const USAGE = `Usage: machina [--model MODEL] [--provider PROVIDER] [--session ID] [--mode MODE] [--no-clear]
               [--log PATH] [--log-level error|warn|info|debug|trace]

Interactive Machina terminal UI. Set MACHINA_PYTHON to the Python interpreter
that has Machina installed (the \`machina\` command does this for you).
The terminal is cleared on startup so the app owns the screen; pass
--no-clear to keep existing scrollback.

Logging is off unless --log is passed or MACHINA_UI_LOG / MACHINA_UI_DEBUG is
set. The default path is <MACHINA_HOME>/logs/ui.log (NDJSON, 5 MB rotation).`;

const { values } = parseArgs({
	args: process.argv.slice(2),
	options: {
		model: { type: "string" },
		provider: { type: "string" },
		session: { type: "string" },
		mode: { type: "string" },
		"no-clear": { type: "boolean" },
		log: { type: "string" },
		"log-level": { type: "string" },
		help: { type: "boolean", short: "h" },
	},
	strict: true,
});

if (values.help) {
	console.log(USAGE);
	process.exit(0);
}

if (values.log) {
	uiLog.configure({ path: values.log, level: values["log-level"] as LogLevel | undefined });
	if (!uiLog.enabled) console.error(`machina: could not open debug log at ${values.log}`);
} else {
	uiLog.configureFromEnv();
	if (uiLog.enabled && values["log-level"]) {
		uiLog.configure({ path: uiLog.target!, level: values["log-level"] as LogLevel });
	}
}
const mainLog = uiLog.scope("main");
mainLog.info("ui.start", {
	pid: process.pid,
	platform: process.platform,
	node: process.versions.node,
	cwd: process.cwd(),
	log: uiLog.target ?? null,
});

function detectPython(): string {
	if (process.env.MACHINA_PYTHON) return process.env.MACHINA_PYTHON;
	if (process.platform === "win32") {
		// Prefer the project venv over system python (which lacks machina).
		const venv = path.resolve(import.meta.dirname, "..", "..", ".venv", "Scripts", "python.exe");
		try {
			if (fs.statSync(venv).isFile()) return venv;
		} catch { /* fall through */ }
		return "python";
	}
	return "python3";
}
const python = detectPython();
const gatewayToken = crypto.randomBytes(32).toString("hex");
// Start the Python gateway before loading the UI modules. Python imports and
// the Node module graph then boot in parallel instead of serially, which cuts
// roughly half a second from the first interactive frame's readiness.
const client = new GatewayClient({ python, token: gatewayToken, env: { MACHINA_GATEWAY_TOKEN: gatewayToken } });
client.start();
const uiModules = await Promise.all([
	import("@earendil-works/pi-tui"),
	import("./design/terminal.ts"),
	import("./clipboard.ts"),
	import("./app.ts"),
]).catch(async (error: unknown) => {
	await client.stop();
	throw error;
});
const [{ TuiAltScreen }, { ThemedTerminal }, { tuiClipboardOptions }, { App }] = uiModules;
// Fullscreen alternate buffer, Codex style: the app owns the display and the
// composer docks to the bottom (see App's layout root). Restored on exit.
// Selection copy must hit the real clipboard (pbcopy/wl-copy/Set-Clipboard);
// the framework's OSC 52 fallback lies "Copied!" on terminals that ignore it.
const tui = new TuiAltScreen(new ThemedTerminal(), undefined, undefined, {
	...tuiClipboardOptions(),
	wheelScrollLines: 3,
});
const app = new App(tui, client, { model: values.model, provider: values.provider, sessionId: values.session, mode: values.mode });

// A dead gateway is not recoverable in place: the Python process owns the
// turn, its leases, and every RPC channel. Rather than leaving an offline
// screen that only Ctrl+C can escape, restore the terminal and relaunch the
// UI with bounded backoff. The durable session is what makes this safe — the
// interrupted turn is recovered from PostgreSQL, not from Node state.
const RELAUNCH_LIMIT = 2;
let relaunching = false;
const healthy = setTimeout(() => {
	// A long-lived UI earns a fresh budget for a later unrelated crash.
	process.env.MACHINA_UI_RELAUNCH = "0";
}, 60_000);
healthy.unref();
client.onUnexpectedExit = (code, stderrTail) => {
	const attempt = Number(process.env.MACHINA_UI_RELAUNCH ?? "0");
	mainLog.warn("gateway.unexpected_exit", { code, attempt, tail: stderrTail.slice(-3) });
	if (relaunching || attempt >= RELAUNCH_LIMIT) return;
	relaunching = true;
	setTimeout(() => {
		try {
			tui.stop();
		} catch {
			// the terminal may already be restored
		}
		try {
			// Re-enter through the Python launcher, not Node directly: it owns
			// the managed-database TUI lease, the gateway token, and the
			// Node/UI checks. Multiple leases may coexist, so the old launcher
			// releasing after this process exits cannot stop the container.
			const python = process.env.MACHINA_PYTHON ?? (process.platform === "win32" ? "python" : "python3");
			const options = {
				model: values.model,
				provider: values.provider,
				session: values.session,
				mode: values.mode,
			};
			const launcher = [
				"import json, sys",
				"from machina.ui_launcher import launch_ui",
				'opts = json.loads(sys.argv[1])',
				'raise SystemExit(launch_ui(model=opts.get("model"), provider=opts.get("provider"), session_id=opts.get("session"), mode=opts.get("mode")))',
			].join("\n");
			const child = spawn(python, ["-c", launcher, JSON.stringify(options)], {
				detached: true,
				stdio: "inherit",
				env: { ...process.env, MACHINA_UI_RELAUNCH: String(attempt + 1) },
			});
			child.unref();
			process.exit(75);
		} catch (error) {
			mainLog.error("gateway.relaunch_failed", { error: describeError(error) });
			process.exit(1);
		}
	}, 1_000);
};

// `machina stop` targets the UI child, not the Python launcher. Shut the UI
// and its gateway down in order; the launcher releases the database lease
// only after this process exits.
let shuttingDown = false;
const requestShutdown = () => {
	if (shuttingDown) return;
	shuttingDown = true;
	mainLog.info("ui.shutdown");
	void app.exit().finally(() => process.exit(0));
};
process.on("SIGTERM", requestShutdown);
process.on("SIGBREAK", requestShutdown);

// Never leave the terminal in raw mode, whatever happens.
const crash = (error: unknown) => {
	mainLog.error("ui.fatal", { error: describeError(error) });
	try {
		tui.stop();
	} catch {
		// terminal may already be restored
	}
	console.error(error instanceof Error ? (error.stack ?? error.message) : error);
	void client.stop().finally(() => process.exit(1));
};
process.on("uncaughtException", crash);
process.on("unhandledRejection", crash);

if (!values["no-clear"] && process.stdout.isTTY) {
	// Wipe scrollback + visible screen and home the cursor, so the app is
	// the only thing on screen (opencode-style takeover). The alt screen
	// below then takes over fully and restores the terminal on exit.
	process.stdout.write("\x1b[3J\x1b[2J\x1b[H");
}

await app.start();
await app.exited;
mainLog.info("ui.exit");
uiLog.close();
process.exit(0);
