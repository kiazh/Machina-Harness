import { splitSub } from "../commands.ts";
import { shortId } from "../format.ts";
import type { RsiStatus } from "../protocol.ts";
import { type Command, confirm, options, requireArgs, requireSession } from "./types.ts";

export interface GrowthResult {
	growth: { notes: Record<string, string>; interactions: number; feedback_count: number };
	episodes: Array<{ id: string; action: string; completed: boolean; reward: number | null }>;
	rsi?: RsiStatus;
}

/**
 * Render the growth report as Markdown. Kept separate from the command so the
 * presentation can be tested without a live gateway, and so every datum has a
 * stable, readable place instead of being concatenated into one paragraph.
 */
export function formatGrowthReport({ growth, episodes, rsi }: GrowthResult): string {
	const notes = Object.entries(growth.notes)
		.map(([key, value]) => `- **${key}:** ${value}`)
		.join("\n");
	const decisions = episodes
		.filter((episode) => episode.completed)
		.slice(0, 5)
		.map((episode) => {
			const action = episode.action === "retrieve" ? "used memory search" : "skipped memory search";
			const rating = episode.reward === null ? "unrated" : String(episode.reward);
			return `| \`${shortId(episode.id)}\` | ${action} | ${rating} |`;
		});
	const sections = [
		"## Personality growth",
		"",
		`**${growth.interactions}** completed conversations · **${growth.feedback_count}** ratings`,
	];
	if (rsi) {
		sections.push(
			"",
			"### Runtime adaptation",
			"",
			`- RSI: **${rsi.enabled ? "on" : "off"}** · revision **${rsi.revision}**`,
			`- **${rsi.automatic_observations}** operational observations · **${rsi.human_ratings}** human ratings`,
			rsi.trial
				? `- Persona trial: revision **${rsi.trial.revision}** · **${rsi.trial.ratings}/${rsi.trial.required}** ratings before evaluation`
				: "- Persona trial: none pending",
		);
	}
	sections.push(
		"",
		"### Learned notes",
		"",
		notes || "_No learned notes yet. Teach one with `/growth teach`._",
		"",
		"### Recent memory decisions",
		"",
	);
	if (decisions.length) {
		sections.push(
			"| Decision | Memory search | Rating |",
			"|---|---|---|",
			...decisions,
		);
	} else {
		sections.push("_No completed memory decisions yet._");
	}
	return sections.join("\n");
}

export const growthCommand: Command = {
	name: "growth",
	description: "Manage learned personality, feedback, and reversible improvement",
	argumentHint: "[show|teach|feedback|forget|rollback]",
	getArgumentCompletions: options([
		["show", "Learned preferences, style and lessons"],
		["teach", "<preference|style|lesson> <key> <note>"],
		["feedback", "<good|bad> [episode-id] — rate the approach"],
		["forget", "<kind:key> — remove a learned note"],
		["rollback", "[revision] — restore persona and memory policy"],
	]),
	async run(args, host) {
		const sessionId = requireSession(host).id;
		let [sub, rest] = splitSub(args);
		if (!sub) {
			const action = await host.pick("Personality growth", [
				{ value: "show", label: "View progress", description: "Notes, evidence, and persona revision" },
				{ value: "feedback", label: "Give feedback", description: "Rate the last completed approach" },
				{ value: "teach", label: "Teach a note", description: "Add a preference, style, or lesson" },
				{ value: "forget", label: "Forget a note", description: "Choose a saved note to remove" },
				{ value: "rollback", label: "Restore a revision", description: "Choose an earlier persona and memory policy" },
			]);
			if (!action) return;
			sub = action.value;
		}
		if (sub === "show") {
			const result = await host.request<GrowthResult>("growth.get", { sessionId });
			host.print(formatGrowthReport(result), "plain");
			return;
		}
		if (sub === "teach") {
			if (!rest) {
				const kind = await host.pick("What should I learn?", [
					{ value: "preference", label: "Preference" },
					{ value: "style", label: "Conversational style" },
					{ value: "lesson", label: "Verified lesson" },
				]);
				if (!kind) return;
				const key = await host.askText({ title: "Name this note", description: "Reuse this name to correct it later.", example: "verbosity", maxLength: 64 });
				if (key === undefined) return;
				const content = await host.askText({ title: "What should I remember?", description: "A durable preference or observed lesson. Avoid secrets and temporary task details.", maxLength: 500 });
				if (content === undefined) return;
				rest = `${kind.value} ${key.trim()} ${content.trim()}`;
			}
			const match = rest.match(/^(preference|style|lesson)\s+(\S+)\s+([\s\S]+)$/);
			if (!match) throw new Error("Usage: /growth teach <preference|style|lesson> <key> <note>");
			await host.request("growth.teach", { sessionId, kind: match[1], key: match[2], content: match[3] });
			host.print("Learned. This note will carry into future conversations.", "success");
			return;
		}
		if (sub === "forget") {
			if (!rest) {
				const { growth } = await host.request<GrowthResult>("growth.get", { sessionId });
				const items = Object.entries(growth.notes).map(([key, note]) => ({ value: key, label: key, description: note }));
				if (!items.length) { host.print("No saved notes to forget.", "info"); return; }
				const chosen = await host.pick("Forget which note?", items);
				if (!chosen) return;
				if (!await confirm(host, `Forget ${chosen.value}?`, "Forget note")) return;
				rest = chosen.value;
			}
			const { removed } = await host.request<{ removed: boolean }>("growth.forget", { sessionId, key: requireArgs(rest, "/growth forget <kind:key>") });
			host.print(removed ? "Learned note removed." : "No matching note.", removed ? "success" : "warning");
			return;
		}
		if (sub === "feedback") {
			if (!rest) {
				const rating = await host.pick("Was the last completed approach useful?", [
					{ value: "good", label: "Yes, useful" },
					{ value: "bad", label: "No, needs improvement" },
				]);
				if (!rating) return;
				rest = rating.value;
			}
			const [rating, suppliedId] = rest.split(/\s+/);
			if (rating !== "good" && rating !== "bad") throw new Error("Usage: /growth feedback <good|bad> [episode-id]");
			let episodeId: string | undefined = suppliedId;
			if (!episodeId) {
				const { episodes } = await host.request<GrowthResult>("growth.get", { sessionId });
				episodeId = episodes.find(e => e.completed && e.reward === null)?.id;
			}
			if (!episodeId) throw new Error("No completed, unrated memory decision in this session.");
			const { accepted } = await host.request<{ accepted: boolean }>("growth.feedback", { sessionId, episodeId, reward: rating === "good" ? 1 : -1 });
			host.print(accepted ? "Personality and memory approach learned from your rating." : "Decision unavailable or already rated.", accepted ? "success" : "warning");
			return;
		}
		if (sub === "rollback") {
			let revision: number;
			if (rest) {
                if (!/^(0|[1-9]\d*)$/.test(rest.trim()) || !Number.isSafeInteger(Number(rest))) throw new Error("Revision must be a nonnegative whole number.");
				revision = Number(rest);
			} else {
				const { rsi } = await host.request<GrowthResult>("growth.get", { sessionId });
                const choices = (rsi?.history ?? []).filter(item => item.revision >= 0 && item.revision !== rsi?.revision).map(item => ({ value: String(item.revision), label: `Revision ${item.revision}`, description: `${item.reason} · ${item.status}` }));
				if (!choices.length) { host.print("No earlier retained revisions to restore.", "info"); return; }
				const chosen = await host.pick("Restore which revision?", choices);
				if (!chosen) return;
				revision = Number(chosen.value);
			}
			if (!await confirm(host, `Restore revision ${revision}? This replaces learned notes and memory policy.`, "Restore revision")) return;
			const { restored } = await host.request<{ restored: boolean }>("growth.rollback", { sessionId, revision });
			host.print(restored ? `Restored revision ${revision}. History remains available.` : "Revision is no longer available. View progress and choose a retained revision.", restored ? "success" : "warning");
			return;
		}
		throw new Error("Usage: /growth [show|teach|feedback|forget|rollback]");
	},
};
