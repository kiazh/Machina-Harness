// /agent — the resident buddy: status, inbox, memory, grants, autonomy.
//
// The resident is a normal Machina session with a durable profile, core
// memory, and scoped autonomy grants. This command is the terminal surface;
// the notch app uses the same resident.* RPCs.

import type {
	ResidentApprovalListResult,
	ResidentGrantListResult,
	ResidentMemoryResult,
	ResidentStatusResult,
	ResumeResult,
} from "../protocol.ts";
import { splitSub } from "../commands.ts";
import type { Command, FeatureHost } from "./types.ts";

const MODES = ["ask", "auto-safe", "full"] as const;

const SUBCOMMANDS: Array<[string, string]> = [
	["status", "Show the resident profile, session, queue, and grants"],
	["open", "Open the resident chat"],
	["inbox", "List pending approvals"],
	["approve", "Approve a request: /agent approve <requestId> <sessionId>"],
	["deny", "Deny a request: /agent deny <requestId> <sessionId>"],
	["memory", "Show core memory"],
	["remember", "Remember a fact: /agent remember <key>=<value>"],
	["forget", "Forget a core fact: /agent forget <key>"],
	["grants", "List autonomy grants"],
	["grant", "Create a grant: /agent grant <minutes> <ops> [--sessions ...] [--targets ...] [--max N]"],
	["revoke", "Revoke a grant: /agent revoke <grantId>"],
	["autonomy", "Set mode: /agent autonomy ask|auto-safe|full"],
	["name", "Set the resident name: /agent name Machina"],
	["kill", "Pause the resident and revoke every grant"],
];

function argWords(args: string): string[] {
	return args.trim().split(/\s+/).filter(Boolean);
}

function statusText(status: ResidentStatusResult): string {
	const profile = status.profile;
	return [
		`Resident: ${profile.name}${profile.paused ? " (paused)" : ""}`,
		`  Autonomy: ${profile.autonomyMode}`,
		`  Shared memory: ${profile.sharedMemory ? "on" : "off"}`,
		`  Session: ${status.sessionId}`,
		`  Core facts: ${status.coreMemoryCount}`,
		`  Pending approvals: ${status.pendingApprovals}`,
		`  Active grants: ${status.grants.length}`,
	].join("\n");
}

function grantText(grants: ResidentGrantListResult["grants"]): string {
	if (!grants.length) return "No active grants.";
	return grants
		.map((grant) => {
			const scope = grant.scope;
			const ops = (scope.operations ?? []).join(",") || "—";
			const sessions = (scope.sessions ?? ["*"]).join(",");
			const targets = (scope.targets ?? ["*"]).join(",");
			const expiry = grant.expiresAt ? new Date(grant.expiresAt).toISOString().replace(".000Z", "Z") : "no expiry";
			const budget = grant.maxActions === null ? "unlimited" : `${grant.actionsUsed}/${grant.maxActions}`;
			return `${grant.id.slice(0, 8)}  ${ops}  sessions=${sessions}  targets=${targets}  ${budget}  ${expiry}`;
		})
		.join("\n");
}

function approvalText(approvals: ResidentApprovalListResult["approvals"]): string {
	if (!approvals.length) return "No pending approvals.";
	return approvals
		.map((approval) => {
			const age = approval.created_at ? new Date(approval.created_at).toLocaleTimeString() : "";
			return `${approval.request_id}  ${approval.operation}  ${approval.target}  session=${approval.session_id}  ${age}`;
		})
		.join("\n");
}

async function openResident(host: FeatureHost): Promise<void> {
	const status = await host.request<ResidentStatusResult>("resident.status");
	const { session, history } = await host.request<ResumeResult>("session.resume", {
		sessionId: status.sessionId,
	});
	host.switchTo(session, history);
	host.print(`Opened the resident chat (${status.profile.name}).`, "plain");
}

async function resolveApproval(host: FeatureHost, args: string, verdict: "approved" | "denied"): Promise<void> {
	const [requestId, sessionId] = argWords(args);
	if (!requestId || !sessionId) {
		throw new Error("Usage: /agent approve|deny <requestId> <sessionId>");
	}
	await host.request("resident.approval.resolve", { requestId, sessionId, verdict });
	host.print(`Approval ${requestId} ${verdict}.`, verdict === "approved" ? "success" : "warning");
}

async function remember(host: FeatureHost, args: string): Promise<void> {
	const trimmed = args.trim();
	const eq = trimmed.indexOf("=");
	if (eq <= 0) throw new Error("Usage: /agent remember <key>=<value>");
	const key = trimmed.slice(0, eq).trim();
	const value = trimmed.slice(eq + 1).trim();
	await host.request("resident.memory.set", { key, value, category: "fact", source: "user", pinned: true });
	host.print(`Remembered: ${key}.`, "success");
}

async function grant(host: FeatureHost, args: string): Promise<void> {
	const words = argWords(args);
	const minutes = Number(words.shift() ?? "");
	const operations = (words.shift() ?? "").split(",").filter(Boolean);
	if (!Number.isFinite(minutes) || minutes <= 0 || !operations.length) {
		throw new Error("Usage: /agent grant <minutes> <operation,...> [--sessions ...] [--targets ...] [--max N]");
	}
	let sessions: string[] | undefined;
	let targets: string[] | undefined;
	let maxActions: number | undefined;
	for (let index = 0; index < words.length; index++) {
		const word = words[index];
		const next = words[index + 1];
		if (word === "--sessions" && next) { sessions = next.split(",").filter(Boolean); index += 1; }
		else if (word === "--targets" && next) { targets = next.split(",").filter(Boolean); index += 1; }
		else if (word === "--max" && next) { maxActions = Number(next); index += 1; }
		else throw new Error(`Unknown grant option "${word}".`);
	}
	const result = await host.request<{ grant: { id: string; scope: { operations?: string[] } } }>(
		"resident.grant.create",
		{ operations, sessions, targets, maxActions, hours: minutes / 60, reason: "created from /agent" },
	);
	host.print(
		`Grant ${result.grant.id.slice(0, 8)} active for ${minutes} min: ${(result.grant.scope.operations ?? []).join(", ")}.`,
		"success",
	);
}

export const agentCommand: Command = {
	name: "agent",
	description: "Resident buddy: status, inbox, memory, grants, autonomy",
	argumentHint: "[status|open|inbox|approve|deny|memory|remember|grants|grant|revoke|autonomy|kill]",
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
			case "status":
				host.print(statusText(await host.request<ResidentStatusResult>("resident.status")), "plain");
				return;
			case "open":
				await openResident(host);
				return;
			case "inbox": {
				const result = await host.request<ResidentApprovalListResult>("resident.approval.list", {
					...(rest.trim() ? { sessionId: rest.trim() } : {}),
				});
				host.print(approvalText(result.approvals), "plain");
				return;
			}
			case "approve":
				await resolveApproval(host, rest, "approved");
				return;
			case "deny":
				await resolveApproval(host, rest, "denied");
				return;
			case "memory": {
				const result = await host.request<ResidentMemoryResult>("resident.memory.get", {});
				if (!result.memory.length) {
					host.print("Core memory is empty. Use /agent remember <key>=<value>.", "plain");
					return;
				}
				host.print(
					result.memory
						.map((entry) => `${entry.pinned ? "*" : " "} ${entry.key}: ${entry.value}  (${entry.category})`)
						.join("\n"),
					"plain",
				);
				return;
			}
			case "remember":
				await remember(host, rest);
				return;
			case "forget": {
				const key = rest.trim();
				if (!key) throw new Error("Usage: /agent forget <key>");
				const result = await host.request<{ removed: boolean }>("resident.memory.forget", { key });
				host.print(result.removed ? `Forgot ${key}.` : `No core fact named ${key}.`, result.removed ? "success" : "warning");
				return;
			}
			case "grants": {
				const result = await host.request<ResidentGrantListResult>("resident.grant.list", {});
				host.print(grantText(result.grants), "plain");
				return;
			}
			case "grant":
				await grant(host, rest);
				return;
			case "revoke": {
				const grantId = rest.trim();
				if (!grantId) throw new Error("Usage: /agent revoke <grantId>");
				const result = await host.request<{ revoked: boolean }>("resident.grant.revoke", { grantId });
				host.print(result.revoked ? "Grant revoked." : "Grant not found or already revoked.", result.revoked ? "success" : "warning");
				return;
			}
			case "autonomy": {
				const mode = rest.trim().toLowerCase();
				if (!MODES.includes(mode as (typeof MODES)[number])) {
					throw new Error("Usage: /agent autonomy ask|auto-safe|full");
				}
				const result = await host.request<{ profile: { autonomyMode: string } }>("resident.profile.set", {
					autonomyMode: mode,
				});
				host.print(`Resident autonomy: ${result.profile.autonomyMode}.`, "success");
				return;
			}
			case "name": {
				const name = rest.trim();
				if (!name) throw new Error("Usage: /agent name <name>");
				const result = await host.request<{ profile: { name: string } }>("resident.profile.set", { name });
				host.print(`Resident name: ${result.profile.name}.`, "success");
				return;
			}
			case "kill": {
				const result = await host.request<{ revokedGrants: number; paused: boolean }>("resident.kill", {});
				host.print(`Resident paused; ${result.revokedGrants} grant(s) revoked.`, "warning");
				return;
			}
			default:
				throw new Error(
					`Unknown /agent option "${sub}". Try: status, open, inbox, approve, deny, memory, remember, grants, grant, revoke, autonomy, kill.`,
				);
		}
	},
};
