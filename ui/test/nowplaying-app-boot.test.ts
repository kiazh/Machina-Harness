// Music bar at base launch: boots the real App (scripted gateway) and proves
// the pinned now-playing bar is on screen from the first frame — showing what
// is playing, an honest line when nothing is, and nothing at all when the
// feature is disabled. This is the user-facing acceptance test.

import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, TuiAltScreen, type Terminal } from "@earendil-works/pi-tui";
import { App } from "../src/app.ts";
import type { GatewayClient } from "../src/gateway.ts";

function mockTerminal(columns: number, rows: number): Terminal {
	return {
		writes: [],
		columns,
		rows,
		kittyProtocolActive: false,
		start() {},
		stop() {},
		async drainInput() {},
		write() {},
		moveBy() {},
		hideCursor() {},
		showCursor() {},
		clearLine() {},
		clearFromCursor() {},
		clearScreen() {},
		setTitle() {},
		setProgress() {},
	} as unknown as Terminal;
}

function scriptedClient(
	config: Record<string, unknown>,
	nowPlaying: Record<string, unknown>,
): GatewayClient {
	const session = {
		id: "nowplaying-boot",
		title: "new",
		model: "m",
		provider: "p",
		status: "active",
		lastActivity: null,
	};
	return {
		start() {},
		async stop() {},
		stderr() {
			return [];
		},
		onEvent: undefined,
		onExit: undefined,
		async request(method: string) {
			switch (method) {
				case "initialize":
					return { version: "test", model: "m", provider: "p", cwd: "/tmp", branch: "main" };
				case "config.get":
					return { config, secrets: [] };
				case "mode.get":
					return { mode: "ask" };
				case "profile.get":
					return {};
				case "session.create":
					return { session };
				case "nowplaying.get":
					return nowPlaying;
				default:
					return {};
			}
		},
	} as unknown as GatewayClient;
}

async function boot(
	config: Record<string, unknown>,
	nowPlaying: Record<string, unknown>,
): Promise<{ app: App; screen: string[] }> {
	const tui = new TuiAltScreen(mockTerminal(100, 30));
	const app = new App(tui, scriptedClient(config, nowPlaying));
	await app.start();
	tui.renderNow(true);
	const screen = tui.getScreenLines().map((line) => stripTerminalSequences(line));
	return { app, screen };
}

test("base launch shows what is playing in the header strip", async () => {
	const { app, screen } = await boot(
		{ theme: "codex", nowplaying_enabled: true },
		{
			available: true,
			state: "playing",
			title: "Boot Track",
			artist: "Example Artist",
			album: null,
			source: "Google Chrome",
			elapsed: 12,
			duration: 210,
			error: null,
		},
	);
	try {
		const barRow = screen.findIndex((line) => line.includes("Boot Track"));
		assert.ok(barRow > 0, "the one-line bar is visible on the first frame below the command strip");
		assert.match(screen[barRow] ?? "", /Example Artist/, "the artist stays on the one-line bar");
		assert.ok(screen.findIndex((line) => line.includes("MACHINA")) < barRow, "the strip is the first row");
	} finally {
		await app.exit();
	}
});

test("base launch with nothing playing shows the honest line", async () => {
	const { app, screen } = await boot(
		{ theme: "codex", nowplaying_enabled: true },
		{ available: true, state: null, title: null, artist: null, error: null },
	);
	try {
		assert.ok(screen.some((line) => /Nothing playing/.test(line)), "the honest line renders in the strip");
	} finally {
		await app.exit();
	}
});

test("base launch with the bar disabled shows no bar rows", async () => {
	const { app, screen } = await boot(
		{ theme: "codex", nowplaying_enabled: false },
		{ available: true, state: "playing", title: "Boot Track", error: null },
	);
	try {
		assert.doesNotMatch(screen.join("\n"), /Boot Track|Nothing playing/);
	} finally {
		await app.exit();
	}
});

test("a permission error is shown honestly, never as fake playback", async () => {
	const { app, screen } = await boot(
		{ theme: "codex", nowplaying_enabled: true },
		{
			available: false,
			state: null,
			title: null,
			error: "macOS blocked automation for Spotify",
		},
	);
	try {
		assert.ok(screen.some((line) => /blocked automation for Spotify/.test(line)), "the permission error renders honestly");
	} finally {
		await app.exit();
	}
});
