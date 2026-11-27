import assert from "node:assert/strict";
import { test } from "node:test";
import { agentCommand } from "../src/features/agent.ts";
import type { FeatureHost } from "../src/features/types.ts";

function host(overrides: Record<string, (params: Record<string, unknown>) => unknown> = {}) {
	const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
	const printed: string[] = [];
	const switched: string[] = [];
	const value: FeatureHost = {
		request: async <T>(method: string, params: Record<string, unknown> = {}) => {
			calls.push({ method, params });
			const handler = overrides[method];
			if (handler) return handler(params) as T;
			throw new Error(`unexpected request ${method}`);
		},
		print: (text: string) => { printed.push(text); },
		switchTo: (session: { id: string }) => { switched.push(session.id); },
	} as unknown as FeatureHost;
	return { host: value, calls, printed, switched };
}

const status = {
	profile: {
		key: "default",
		name: "Buddy",
		persona: "",
		autonomyMode: "auto-safe",
		sharedMemory: true,
		paused: false,
	},
	sessionId: "session-1",
	pendingApprovals: 2,
	grants: [],
	coreMemoryCount: 3,
};

test("/agent status prints the resident profile", async () => {
	const h = host({ "resident.status": () => status });

	await agentCommand.run("", h.host);

	assert.match(h.printed.join("\n"), /Resident: Buddy/);
	assert.match(h.printed.join("\n"), /Autonomy: auto-safe/);
	assert.match(h.printed.join("\n"), /Session: session-1/);
	assert.match(h.printed.join("\n"), /Pending approvals: 2/);
});

test("/agent remember writes a pinned core fact", async () => {
	const h = host({
		"resident.memory.set": (params) => ({ entry: { key: params.key, value: params.value } }),
	});

	await agentCommand.run("remember name=Kiarad", h.host);

	assert.deepEqual(h.calls[0]?.params, {
		key: "name",
		value: "Kiarad",
		category: "fact",
		source: "user",
		pinned: true,
	});
	assert.match(h.printed.join("\n"), /Remembered: name/);
});

test("/agent autonomy updates the mode", async () => {
	const h = host({
		"resident.profile.set": (params) => ({ profile: { autonomyMode: params.autonomyMode } }),
	});

	await agentCommand.run("autonomy full", h.host);

	assert.deepEqual(h.calls[0]?.params, { autonomyMode: "full" });
	assert.match(h.printed.join("\n"), /Resident autonomy: full/);
});

test("/agent grant parses duration, operations, and budget", async () => {
	const h = host({
		"resident.grant.create": () => ({
			grant: { id: "grant-uuid", scope: { operations: ["send_email"] } },
		}),
	});

	await agentCommand.run("grant 60 send_email --max 5", h.host);

	assert.deepEqual(h.calls[0]?.params, {
		operations: ["send_email"],
		sessions: undefined,
		targets: undefined,
		maxActions: 5,
		hours: 1,
		reason: "created from /agent",
	});
	assert.match(h.printed.join("\n"), /Grant grant-u/);
});

test("/agent open resumes the resident session", async () => {
	const h = host({
		"resident.status": () => status,
		"session.resume": () => ({ session: { id: "session-1" }, history: [] }),
	});

	await agentCommand.run("open", h.host);

	assert.equal(h.switched[0], "session-1");
	assert.match(h.printed.join("\n"), /Opened the resident chat/);
});

test("/agent approve resolves with the session binding", async () => {
	const h = host({ "resident.approval.resolve": () => ({ approval: {} }) });

	await agentCommand.run("approve req-1 session-1", h.host);

	assert.deepEqual(h.calls[0]?.params, {
		requestId: "req-1",
		sessionId: "session-1",
		verdict: "approved",
	});
	assert.match(h.printed.join("\n"), /req-1 approved/);
});

test("/agent rejects an unknown option", async () => {
	const h = host();
	await assert.rejects(() => agentCommand.run("nonsense", h.host), /Unknown \/agent option/);
});
