import assert from "node:assert/strict";
import test from "node:test";
import { cleanMcpDescription, cleanMcpToolName } from "../src/mcpNames.ts";

test("Composio toolkit slugs become familiar App · Action labels", () => {
	assert.equal(cleanMcpToolName("GMAIL_FETCH_EMAILS", "composio"), "Gmail · Fetch emails");
	assert.equal(cleanMcpToolName("GITHUB_CREATE_AN_ISSUE", "composio"), "GitHub · Create an issue");
	assert.equal(
		cleanMcpToolName("GOOGLE_DRIVE_LIST_FILES", "composio"),
		"Google Drive · List files",
	);
	assert.equal(cleanMcpToolName("MICROSOFT_TEAMS_SEND_MESSAGE", "composio"), "Microsoft Teams · Send message");
});

test("Composio meta tools stay short and readable", () => {
	assert.equal(cleanMcpToolName("COMPOSIO_GET_TOOL_SCHEMAS", "composio"), "Tool schemas");
	assert.equal(cleanMcpToolName("COMPOSIO_MANAGE_CONNECTIONS", "composio"), "Connections");
	assert.equal(cleanMcpToolName("COMPOSIO_MULTI_EXECUTE_TOOL", "composio"), "Run tools");
});

test("ordinary provider-relative tool names are not confused with toolkit slugs", () => {
	assert.equal(cleanMcpToolName("vercel__list_projects", "vercel"), "List projects");
	assert.equal(cleanMcpToolName("GoogleDrive_ListFiles", "arcade"), "Google Drive · List files");
	assert.equal(cleanMcpToolName("firecrawl_scrape", "firecrawl"), "Scrape");
});

test("tool descriptions lose markdown and links but keep the useful sentence", () => {
	assert.equal(
		cleanMcpDescription("**Fetch** [Gmail](https://example.com) `messages`.\n\nUse this to read mail."),
		"Fetch Gmail messages. Use this to read mail.",
	);
	assert.ok(cleanMcpDescription("x".repeat(200), 20).length <= 20);
});
