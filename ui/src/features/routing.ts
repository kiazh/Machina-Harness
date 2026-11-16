// Routing command: choose the orchestration mode or explain one prompt's route.

import type { ConfigSetResult, ProvidersListResult, RoutingInfo } from "../protocol.ts";
import { splitSub } from "../commands.ts";
import { options, type Command, type FeatureHost } from "./types.ts";

type RoutingMode = RoutingInfo["mode"];
type RoutingChoice = "multi_auto" | "single";

const MODE_LABELS: Record<RoutingMode, string> = {
	multi_auto: "Multi Auto",
	multi_pinned: "Multi Auto (pinned)",
	single: "Single Model",
};

/** Switch the first-class orchestration mode and persist it. */
export async function applyRoutingMode(host: FeatureHost, mode: RoutingChoice): Promise<void> {
	const values: Array<[string, unknown]> =
		mode === "multi_auto"
			? [
					["orchestration_mode", "multi_model"],
					["model_selection", "auto"],
					["auto_parallel_delegation", true],
				]
			: [
					["orchestration_mode", "single_model"],
					["model_selection", "auto"],
				];
	for (const [key, value] of values) {
		const result = await host.request<ConfigSetResult>("config.set", { key, value, persist: true });
		host.onConfig(result);
	}
	if (mode === "multi_auto") {
		host.print(
			"Routing: Multi Auto - planner/supervisor/worker routes are chosen from connected models. The current model stays the session/planner anchor; explicit parallel requests are harness-planned.",
			"success",
		);
	} else {
		host.print("Routing: Single Model - every role uses the current session route.", "success");
	}
}

async function currentRoutingMode(host: FeatureHost): Promise<RoutingMode | undefined> {
	try {
		const listed = await host.request<ProvidersListResult>("providers.list");
		return listed.routing?.mode;
	} catch {
		return undefined;
	}
}

export async function pickRoutingMode(host: FeatureHost): Promise<void> {
	const current = await currentRoutingMode(host);
	const title = current ? `Routing mode: ${MODE_LABELS[current]}` : "Routing mode";
	const choice = await host.pick(title, [
		{
			value: "multi_auto",
			label: `Multi Auto${current === "multi_auto" ? " - current" : ""}`,
			description:
				"Planner, supervisor, and worker routes chosen from connected models. Explicit parallel requests are harness-planned.",
		},
		{
			value: "single",
			label: `Single Model${current === "single" ? " - current" : ""}`,
			description: "One session route for every role. Choose a concrete model with /models to change it.",
		},
	]);
	if (!choice) return;
	if (choice.value === "multi_auto" || choice.value === "single") {
		await applyRoutingMode(host, choice.value);
	}
}

export const routingCommand: Command = {
	name: "routing",
	description: "Choose Multi Auto or Single Model, or explain a prompt's route",
	argumentHint: "[mode|<prompt>]",
	getArgumentCompletions: options([["mode", "Choose Multi Auto or Single Model"]]),
	async run(args, host) {
		const [sub, rest] = splitSub(args);
		if (!sub || sub === "mode") {
			if (rest) throw new Error("Usage: /routing mode");
			await pickRoutingMode(host);
			return;
		}
		const prompt = args.trim();
		const session = host.session();
		const result = await host.request<{ explanation: string }>("model.route", {
			prompt,
			sessionId: session?.id,
		});
		host.print(result.explanation, "plain");
	},
};
