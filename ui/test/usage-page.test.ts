import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences, visibleWidth, type TUI } from "@earendil-works/pi-tui";
import { UsagePage } from "../src/components/UsagePage.ts";
import type { UsageReportResult } from "../src/protocol.ts";

function daily(days: number, today = new Date(2026, 10, 27)): Array<{ date: string; tokens: number }> {
	const out: Array<{ date: string; tokens: number }> = [];
	for (let offset = days - 1; offset >= 0; offset--) {
		const date = new Date(today);
		date.setDate(today.getDate() - offset);
		out.push({
			date: `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`,
			tokens: offset % 5 === 0 ? 100_000 : offset === 0 ? 240_000 : 0,
		});
	}
	return out;
}

function report(overrides: Partial<UsageReportResult> = {}): UsageReportResult {
	return {
		generatedAt: "2026-11-27T12:00:00-05:00",
		timezone: "America/New_York",
		days: 365,
		account: { name: "Machina", email: "user@example.test", plan: "OpenCode Go" },
		lifetimeTokens: 225_200_000,
		peakTokens: 13_500_000,
		longestChatSeconds: 3_600,
		currentStreakDays: 2,
		longestStreakDays: 9,
		totalChats: 237,
		totalRequests: 1_904,
		activeSessions: 41,
		daily: daily(60),
		models: [
			{ provider: "opencode-go", model: "deepseek-v4.1-flash", requests: 717, tokens: 18_000_000 },
			{ provider: "chatgpt", model: "gpt-6.1-sol", requests: 220, tokens: 4_000_000 },
		],
		skills: {
			total: 77,
			enabled: 73,
			neverUsed: 48,
			explored: 29,
			uses: 41_733,
			views: 1_102,
			top: [
				{ name: "ecc-search-first", uses: 16_597, views: 0, sourceType: "builtin-ecc", enabled: true },
				{ name: "testing-qa", uses: 13_359, views: 100, sourceType: "local", enabled: true },
			],
			items: [
				{ name: "ecc-search-first", uses: 16_597, views: 0, sourceType: "builtin-ecc", enabled: true },
				{ name: "testing-qa", uses: 13_359, views: 100, sourceType: "local", enabled: true },
				{ name: "review", uses: 2, views: 5, sourceType: "local", enabled: true },
				{ name: "disabled-skill", uses: 0, views: 0, sourceType: "local", enabled: false },
			],
		},
		...overrides,
	};
}

function page(rows = 40, width = 100) {
	let renders = 0;
	let closed = 0;
	let refreshed = 0;
	const tui = {
		terminal: { rows, columns: width },
		requestRender: () => { renders += 1; },
	} as unknown as TUI;
	const instance = new UsagePage({
		tui,
		onClose: () => { closed += 1; },
		onRefresh: () => { refreshed += 1; },
	});
	return {
		instance,
		renders: () => renders,
		closed: () => closed,
		refreshed: () => refreshed,
		text: (w = width) => instance.render(w).map(stripTerminalSequences),
	};
}

test("usage page renders stats, heatmap, models, and skills", () => {
	const p = page();
	p.instance.setReport(report());
	const text = p.text().join("\n");

	assert.match(text, /Usage · OpenCode Go · user@example\.test/);
	assert.match(text, /1 Usage/);
	assert.match(text, /2 Skills/);
	assert.match(text, /g Daily/);
	assert.match(text, /Machina/);
	assert.match(text, /225\.2M/);
	assert.match(text, /Lifetime tokens/);
	assert.match(text, /Peak tokens/);
	assert.match(text, /Longest chat/);
	assert.match(text, /Current streak/);
	assert.match(text, /Token activity/);
	assert.match(text, /Su /);
	assert.match(text, /Activity insights/);
	assert.match(text, /deepseek-v4\.1-flash/);
	assert.match(text, /@ecc-search-first/);
	assert.match(text, /Skills explored/);
	assert.match(text, /Skills explored\s+29/);
});

test("g cycles daily, weekly, and cumulative views", () => {
	const p = page();
	p.instance.setReport(report());

	p.instance.handleInput("g");
	const weekly = p.text().join("\n");
	assert.match(weekly, /g Weekly/);
	assert.match(weekly, /Each column = 1 week/);
	p.instance.handleInput("g");
	const cumulative = p.text().join("\n");
	assert.match(cumulative, /g Cumulative/);
	assert.match(cumulative, /Cumulative tokens/);
	p.instance.handleInput("g");
	assert.match(p.text().join("\n"), /g Daily/);
});

test("tab 2 lists every skill with uses and views", () => {
	const p = page();
	p.instance.setReport(report());
	p.instance.handleInput("2");
	const text = p.text().join("\n");

	assert.match(text, /77 installed/);
	assert.match(text, /73 enabled/);
	assert.match(text, /41\.7K runs/);
	assert.match(text, /Skill\s+Uses\s+Views\s+Source/);
	assert.match(text, /ecc-search-first\s+16\.6K/);
	assert.match(text, /disabled-skill \(off\)/);
});

test("the skills table scrolls to the last row", () => {
	const p = page(18);
	const items = Array.from({ length: 30 }, (_, index) => ({
		name: `skill-${index}`,
		uses: 30 - index,
		views: 0,
		sourceType: "local",
		enabled: true,
	}));
	p.instance.setReport(report({ skills: { total: 30, enabled: 30, neverUsed: 0, explored: 30, uses: 465, views: 0, top: items.slice(0, 10), items } }));
	p.instance.handleInput("2");
	assert.doesNotMatch(p.text().join("\n"), /skill-29/);

	p.instance.handleInput("\x1b[F"); // End
	assert.match(p.text().join("\n"), /skill-29/);
});

test("r refreshes and Esc closes", () => {
	const p = page();
	p.instance.setReport(report());
	p.instance.handleInput("r");
	assert.equal(p.refreshed(), 1);
	p.instance.handleInput("\x1b");
	assert.equal(p.closed(), 1);
});

test("a missing report shows progress; an error replaces it", () => {
	const p = page();
	assert.match(p.text().join("\n"), /Reading usage/);
	p.instance.setError("database offline");
	assert.match(p.text().join("\n"), /database offline/);
});

test("rendered lines never exceed the terminal width", () => {
	const p = page(24, 60);
	p.instance.setReport(report());
	for (const line of p.text(60)) {
		assert.ok(visibleWidth(line) <= 60, `line is ${visibleWidth(line)} cells: ${line}`);
	}
});
