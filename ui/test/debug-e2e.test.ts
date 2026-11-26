// The debug log must capture a real UI flow, not just unit calls.
import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { uiLog } from "../src/debug.ts";

interface Record_ {
	scope: string;
	msg: string;
	level: string;
	data?: Record<string, unknown>;
}

test("a screen, an RPC and an approval produce searchable log records", async () => {
	const dir = await mkdtemp(join(tmpdir(), "machina-ui-e2e-log-"));
	const path = join(dir, "ui.log");
	uiLog.reset();
	uiLog.configure({ path, level: "trace" });
	try {
		const { referenceFixture } = await import("./support/reference.ts");
		const fixture = await referenceFixture(100, 30);
		try {
			fixture.app.openStatus();
			await new Promise(resolve => setImmediate(resolve));
			fixture.tui.renderNow(true);
			fixture.emit({ ...fixture.approval("logged-approval"), target: "install.sh" });
			fixture.tui.renderNow(true);
		} finally {
			await fixture.app.exit();
		}
		uiLog.close();
		const records = (await readFile(path, "utf8"))
			.trim()
			.split("\n")
			.filter(Boolean)
			.map((line) => JSON.parse(line) as Record_);
		const messages = new Set(records.map((record) => record.msg));
		for (const expected of ["start", "gateway.ready", "ready", "screen.open", "approval.prompt", "approval.dock"]) {
			assert.ok(messages.has(expected), `missing "${expected}" in ${[...messages].join(", ")}`);
		}
		const rpc = records.find((record) => record.scope === "app" && record.msg === "rpc.ok" && record.data?.method === "mode.get");
		assert.ok(rpc, "the mode RPC is logged with its method");
		assert.equal(typeof rpc.data?.ms, "number");
	} finally {
		uiLog.reset();
		await rm(dir, { recursive: true, force: true });
	}
});
