import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, type Component, type TUI } from "@earendil-works/pi-tui";
import { settingsCommands } from "../src/features/settings.ts";
import type { FeatureHost } from "../src/features/types.ts";
import { UsageController } from "../src/app/usage.ts";
import { UsagePage } from "../src/components/UsagePage.ts";
import type { UsageReportResult, UsageResult } from "../src/protocol.ts";

function report(): UsageReportResult {
	return {
		generatedAt: "2026-11-27T12:00:00-05:00",
		timezone: "UTC",
		days: 365,
		account: null,
		lifetimeTokens: 225_200_000,
		peakTokens: 1_000,
		longestChatSeconds: 60,
		currentStreakDays: 1,
		longestStreakDays: 2,
		totalChats: 3,
		totalRequests: 4,
		activeSessions: 1,
		daily: [{ date: "2026-11-27", tokens: 10 }],
		models: [],
		skills: { total: 0, enabled: 0, neverUsed: 0, explored: 0, uses: 0, views: 0, top: [], items: [] },
	};
}

function usageResult(): UsageResult {
	const view = {
		requests: 1,
		accountedTokens: 10,
		chargedRequests: 1,
		chargedTokens: 10,
		knownPromptTokens: 8,
		knownCompletionTokens: 2,
		cacheReadTokens: 0,
		cacheWriteTokens: 0,
		cacheCapability: null,
		exactCacheHits: 0,
		unknownCalls: 0,
		estimatedCalls: 0,
		acceptedCompletions: 1,
		knownCostUsd: 0,
		unknownCostCalls: 0,
		costPerAcceptedCompletionUsd: null,
		requestLimit: null,
		tokenLimit: null,
		requestsRemaining: null,
		tokensRemaining: null,
	};
	return { sessionId: "s", agentId: "harness", cache: null, session: view, agent: view };
}

test("/usage opens the dashboard without a session", async () => {
	const command = settingsCommands.find((entry) => entry.name === "usage");
	assert.ok(command);
	let opened = 0;
	const host = {
		openUsage: () => { opened += 1; },
		session: () => undefined,
	} as unknown as FeatureHost;

	await command.run("", host);

	assert.equal(opened, 1);
});

test("/usage session keeps the textual per-chat breakdown", async () => {
	const command = settingsCommands.find((entry) => entry.name === "usage");
	assert.ok(command);
	const calls: string[] = [];
	const printed: string[] = [];
	const host = {
		openUsage: () => { throw new Error("the dashboard must not open"); },
		session: () => ({ id: "session-1" }),
		request: async (method: string) => {
			calls.push(method);
			return usageResult();
		},
		print: (text: string) => { printed.push(text); },
	} as unknown as FeatureHost;

	await command.run("session", host);

	assert.deepEqual(calls, ["usage.get"]);
	assert.match(printed.join("\n"), /Session requests/);
});

test("UsageController loads the report into the open page and refreshes", async () => {
	const calls: Array<Record<string, unknown> | undefined> = [];
	let component: Component | undefined;
	const tui = {
		terminal: { rows: 40, columns: 100 },
		requestRender: () => {},
	} as unknown as TUI;
	const controller = new UsageController({
		tui,
		request: async <T>(method: string, params?: Record<string, unknown>) => {
			assert.equal(method, "usage.report");
			calls.push(params);
			return report() as T;
		},
		openDialog: async <T>(create: (close: (value?: T) => void) => Component) => {
			component = create(() => {});
			return new Promise<T | undefined>(() => {});
		},
	});

	controller.open();
	await new Promise((resolve) => setImmediate(resolve));

	assert.ok(component instanceof UsagePage);
	assert.match(component.render(100).map(stripTerminalSequences).join("\n"), /225\.2M/);
	assert.equal(typeof calls[0]?.timezone, "string");

	component.handleInput("r");
	await new Promise((resolve) => setImmediate(resolve));
	assert.equal(calls.length, 2);
});
