// One source of truth for how the flat slash-command registry is presented.
//
// Command names are intentionally stable: existing muscle memory, docs and
// scripts keep working. This catalog only decides grouping, search keywords,
// and the small set of commands shown by a bare `/`. It never changes whether
// a command exists or how it runs.

export interface CommandGroup {
	label: string;
	names: readonly string[];
}

/** Presentation order is the order a keyboard user scans. */
export const COMMAND_GROUPS: readonly CommandGroup[] = [
	{
		label: "Start",
		names: ["help", "new", "resume", "sessions", "clear", "exit"],
	},
	{
		label: "Sessions",
		names: ["search", "recall", "rename", "fork", "delete", "export", "goal"],
	},
	{
		label: "Work",
		names: ["review", "init", "delegate", "agents", "activity", "jobs"],
	},
	{
		label: "Workspace",
		names: ["workspace", "editor", "explorer", "lsp", "problems", "quickopen", "attach"],
	},
	{
		label: "Memory",
		names: ["memory", "growth", "persona", "skills", "context", "compress"],
	},
	{
		label: "Setup",
		names: [
			"models",
			"provider",
			"keys",
			"auth",
			"routing",
			"effort",
			"config",
			"theme",
			"profile",
			"profiles",
			"mode",
			"approvals",
		],
	},
	{
		label: "Integrations",
		names: ["mcp", "interop", "harnesses", "music"],
	},
	{
		label: "Info",
		names: ["status", "usage", "output"],
	},
] as const;

const CATEGORY_BY_COMMAND = new Map<string, string>(
	COMMAND_GROUPS.flatMap((group) => group.names.map((name) => [name, group.label] as const)),
);

/** Category used for a command that is not in the curated catalog. */
export const OTHER_COMMAND_CATEGORY = "Other";

/** Stable presentation label for one command. */
export function commandCategory(name: string): string {
	return CATEGORY_BY_COMMAND.get(name) ?? OTHER_COMMAND_CATEGORY;
}

/**
 * Bare `/` is a launch pad, not a catalog dump. These fit the editor's
 * eight-row autocomplete and cover the common entry points; typing filters the
 * full registry and `/help` exposes every command.
 */
export const QUICK_COMMAND_NAMES: readonly string[] = [
	"new",
	"resume",
	"models",
	"workspace",
	"skills",
	"review",
	"status",
	"help",
] as const;
