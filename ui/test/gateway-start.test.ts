// Regression: main.ts starts the gateway early to overlap boot; App.start
// calls start() again. A second spawn would orphan the first Python process.
import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { uiLog } from "../src/debug.ts";
import { GatewayClient } from "../src/gateway.ts";

test("GatewayClient.start is idempotent", { timeout: 15_000 }, async () => {
	const dir = await mkdtemp(join(tmpdir(), "machina-gateway-start-"));
	const path = join(dir, "ui.log");
	uiLog.reset();
	uiLog.configure({ path, level: "debug" });
	const client = new GatewayClient({ python: process.execPath, cwd: process.cwd() });
	try {
		client.start();
		client.start();
		await new Promise(resolve => setTimeout(resolve, 300));
		await client.stop().catch(() => {});
	} finally {
		uiLog.close();
	}
	try {
		const records = (await readFile(path, "utf8"))
			.trim()
			.split("\n")
			.filter(Boolean)
			.map((line) => JSON.parse(line) as { msg: string });
		assert.equal(records.filter((record) => record.msg === "spawn").length, 1, "one gateway process per client");
	} finally {
		uiLog.reset();
		await rm(dir, { recursive: true, force: true });
	}
});
