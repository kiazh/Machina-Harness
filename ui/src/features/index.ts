// Re-exports all feature commands and maintains the same public API.

import type { SlashCommand } from "@earendil-works/pi-tui";
import { helpText, type ParsedCommand } from "../commands.ts";
import type { SessionResult } from "../protocol.ts";
import { codexCommands } from "./codex.ts";
import { sessionCommands } from "./sessions.ts";
import { memoryCommand } from "./memory.ts";
import { growthCommand } from "./growth.ts";
import { skillsCommand } from "./skills.ts";
import { agentsCommand, delegateCommand } from "./agents.ts";
import { authCommand } from "./auth.ts";
import { jobsCommand } from "./jobs.ts";
import { keysCommand } from "./keys.ts";
import { approvalsCommand, modeCommand } from "./mode.ts";
import { modelsCommand } from "./models.ts";
import { routingCommand } from "./routing.ts";
import { mcpCommand } from "./mcp.ts";
import { interopCommand } from "./interop.ts";
import { harnessesCommand } from "./harnesses.ts";
import { outputCommand } from "./output.ts";
import { settingsCommands } from "./settings.ts";
import { themeCommand } from "./theme.ts";
import { musicCommand } from "./music.ts";
import { contextCommands } from "./context.ts";
import { workspaceCommands } from "./workspace.ts";
import type { AskTextOptions, AskTextResult, Command, FamilySession, FeatureHost, NoticeKind } from "./types.ts";

export type { AskTextOptions, AskTextResult, Command, FamilySession, FeatureHost, NoticeKind };
export { formatLspStatusLine, workspaceCommands } from "./workspace.ts";
export type { WorkspaceHost } from "./types.ts";

const appCommands: Command[] = [
	{ name: "activity", description: "View queued steering, background commands, and agents", async run(_args, host) { await host.inspectActivity?.(); } },
	{
		name: "clear",
		description: "Clear the terminal and start a new chat",
		async run(_args, host) {
			host.clear();
			if (host.newSession) {
				host.newSession();
				return;
			}
			const agent = host.sessionAgent();
			const { session } = await host.request<SessionResult>(
				"session.create",
				agent ? { agent } : {},
			);
			host.switchTo(session, []);
		},
	},
	{
		name: "exit",
		description: "Quit Machina",
		async run(_args, host) {
			await host.exit();
		},
	},
];

const helpCommand: Command = {
	name: "help",
	description: "List every command with its description",
	async run(_args, host) {
		host.print(helpText(COMMANDS), "plain");
	},
};

const COMMANDS: Command[] = [
	helpCommand,
	...sessionCommands,
	...codexCommands,
	...contextCommands,
	memoryCommand,
	growthCommand,
	skillsCommand,
	agentsCommand,
	delegateCommand,
	authCommand,
	jobsCommand,
	keysCommand,
	modeCommand,
	approvalsCommand,
	modelsCommand,
	routingCommand,
	themeCommand,
	musicCommand,
	outputCommand,
	mcpCommand,
	interopCommand,
	harnessesCommand,
	...settingsCommands,
	...workspaceCommands,
	...appCommands,
];

/** All commands offered by editor autocomplete and the grouped help catalog. */
export const SLASH_COMMANDS: SlashCommand[] = COMMANDS;

/** Run a parsed slash command. Errors are reported through the host. */
export async function runCommand({ name, args }: ParsedCommand, host: FeatureHost): Promise<void> {
	const command = COMMANDS.find((c) => c.name === name);
	if (!command) {
		host.print(`Unknown command /${name}. Type /help for the list.`, "warning");
		return;
	}
	try {
		await command.run(args, host);
	} catch (error) {
		host.print(error instanceof Error ? error.message : String(error), "error");
	}
}
