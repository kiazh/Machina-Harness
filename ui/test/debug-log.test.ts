import assert from "node:assert/strict";
import { test } from "node:test";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { redactLogValue, redactParamsForLog, uiLog } from "../src/debug.ts";

async function tempLog(): Promise<{ dir: string; path: string }> {
	const dir = await mkdtemp(join(tmpdir(), "machina-ui-log-"));
	return { dir, path: join(dir, "ui.log") };
}

test("logging is off by default and read-only when unconfigured", () => {
	uiLog.reset();
	assert.equal(uiLog.configureFromEnv({}), false);
	assert.equal(uiLog.target, undefined);
	uiLog.scope("app").info("must not throw or write");
});

test("records are NDJSON, secrets are redacted, and long fields are bounded", async () => {
	const { dir, path } = await tempLog();
	try {
		uiLog.reset();
		uiLog.configure({ path, level: "debug" });
		uiLog.scope("app").info("hello", {
			token: "super-secret",
			nested: { apiKey: "k", text: "x".repeat(2000) },
			list: Array.from({ length: 30 }, (_, index) => index),
		});
		uiLog.scope("app").trace("too quiet");
		uiLog.close();

		const lines = (await readFile(path, "utf8")).trim().split("\n").map((line) => JSON.parse(line));
		assert.equal(lines.length, 1, "one record at debug; trace is filtered");
		const record = lines[0]!;
		assert.equal(record.scope, "app");
		assert.equal(record.msg, "hello");
		assert.equal(record.level, "info");
		assert.equal(record.data.token, "[redacted]");
		assert.equal(record.data.nested.apiKey, "[redacted]");
		assert.ok(record.data.nested.text.length < 900, "long strings are clipped");
		assert.equal(record.data.list.length, 20, "arrays are bounded");
	} finally {
		uiLog.reset();
		await rm(dir, { recursive: true, force: true });
	}
});

test("environment configuration picks the default home log and honors level", async () => {
	const { dir } = await tempLog();
	try {
		uiLog.reset();
		assert.equal(uiLog.configureFromEnv({ MACHINA_UI_DEBUG: "1", MACHINA_HOME: dir }), true);
		assert.equal(uiLog.target, join(dir, "logs", "ui.log"));
		uiLog.scope("app").debug("captured");
		uiLog.close();
		assert.ok(existsSync(join(dir, "logs", "ui.log")));

		const warnPath = join(dir, "warn.log");
		uiLog.reset();
		assert.equal(uiLog.configureFromEnv({ MACHINA_UI_LOG: warnPath, MACHINA_UI_LOG_LEVEL: "warn" }), true);
		uiLog.scope("app").debug("filtered");
		uiLog.scope("app").warn("kept");
		uiLog.close();
		const records = (await readFile(warnPath, "utf8")).trim().split("\n").map((line) => JSON.parse(line));
		assert.deepEqual(records.map((record) => record.msg), ["kept"]);
	} finally {
		uiLog.reset();
		await rm(dir, { recursive: true, force: true });
	}
});

test("the log rotates once it reaches its size cap", async () => {
	const { dir, path } = await tempLog();
	try {
		uiLog.reset();
		uiLog.configure({ path, level: "debug", maxBytes: 1024 });
		const log = uiLog.scope("app");
		for (let index = 0; index < 8; index++) log.info(`record ${index}`, { text: "x".repeat(300) });
		uiLog.close();
		assert.ok(existsSync(path), "the current log exists");
		assert.ok(existsSync(`${path}.1`), "the previous log rotated");
	} finally {
		uiLog.reset();
		await rm(dir, { recursive: true, force: true });
	}
});

test("redactLogValue bounds depth without dropping primitive values", () => {
	const deep: Record<string, unknown> = {};
	let cursor = deep;
	for (let index = 0; index < 10; index++) {
		const next: Record<string, unknown> = {};
		cursor.next = next;
		cursor = next;
	}
	assert.equal(redactLogValue("plain"), "plain");
	assert.equal(redactLogValue(7), 7);
	assert.match(String(redactLogValue("Authorization: Bearer abc123")), /\[redacted\]/);
	assert.match(String(redactLogValue("token=abc123")), /\[redacted\]/);
	assert.equal(JSON.stringify(redactLogValue(deep)).includes("[depth]"), true);
});

test("private RPC methods redact their user content but keep context", () => {
	assert.deepEqual(
		redactParamsForLog("keys.set", { name: "ANTHROPIC_API_KEY", value: "sk-secret", sessionId: "s1" }),
		{ name: "ANTHROPIC_API_KEY", value: "[redacted]", sessionId: "s1" },
	);
	assert.deepEqual(
		redactParamsForLog("question.answer", { requestId: "r1", answer: "private reply" }),
		{ requestId: "r1", answer: "[redacted]" },
	);
	assert.deepEqual(
		redactParamsForLog("changes.diff", { snapshotId: "s1", path: "src/a.ts" }),
		{ snapshotId: "s1", path: "src/a.ts" },
	);
});
