// Display-only names for MCP tools. Wire names stay namespaced
// (`<connector>__<remote>`); humans see a short provider-relative label and
// the `@connector` mention that selects that provider in a request.
//
// Pure functions only: no protocol writes, no execution, no secrets.

const ACRONYMS = new Set(["api", "url", "uri", "id", "mcp", "http", "https", "json", "sql", "ssh", "git", "pdf", "html", "csv", "xml"]);
const ACTION_WORDS = new Set([
	"get", "list", "fetch", "create", "send", "search", "update", "delete",
	"run", "execute", "wait", "manage", "submit", "proxy", "remote", "read",
	"write", "add", "remove", "find", "query", "parse", "scrape", "crawl",
]);

// Composio app/tool slugs are `<TOOLKIT>_<ACTION>` (for example
// `GMAIL_FETCH_EMAILS`). Keep the app's familiar casing instead of flattening
// every slug into a generic title-cased sentence.
const APP_LABELS: Record<string, string> = {
	GITHUB: "GitHub",
	GMAIL: "Gmail",
	GOOGLEDRIVE: "Google Drive",
	GOOGLE_DRIVE: "Google Drive",
	GOOGLECALENDAR: "Google Calendar",
	GOOGLE_CALENDAR: "Google Calendar",
	GOOGLESHEETS: "Google Sheets",
	GOOGLE_SHEETS: "Google Sheets",
	GOOGLEDOCS: "Google Docs",
	GOOGLE_DOCS: "Google Docs",
	GOOGLESLIDES: "Google Slides",
	GOOGLE_SLIDES: "Google Slides",
	SLACK: "Slack",
	NOTION: "Notion",
	LINEAR: "Linear",
	HUBSPOT: "HubSpot",
	OUTLOOK: "Outlook",
	MICROSOFT_TEAMS: "Microsoft Teams",
	MICROSOFT: "Microsoft",
	STRIPE: "Stripe",
	DISCORD: "Discord",
	TELEGRAM: "Telegram",
	TWITTER: "Twitter",
	LINKEDIN: "LinkedIn",
	SALESFORCE: "Salesforce",
	JIRA: "Jira",
	CONFLUENCE: "Confluence",
	TRELLO: "Trello",
	ASANA: "Asana",
	CLICKUP: "ClickUp",
	DROPBOX: "Dropbox",
	AIRTABLE: "Airtable",
	FIGMA: "Figma",
	INTERCOM: "Intercom",
	ZENDESK: "Zendesk",
	SHOPIFY: "Shopify",
	CALENDLY: "Calendly",
	ZOOM: "Zoom",
	YOUTUBE: "YouTube",
	REDDIT: "Reddit",
	TWITCH: "Twitch",
	SPOTIFY: "Spotify",
};

const KNOWN_TOOL_LABELS: Record<string, string> = {
	get_tool_schemas: "Tool schemas",
	manage_connections: "Connections",
	search_tools: "Search tools",
	multi_execute_tool: "Run tools",
	proxy_execute: "Send request",
	remote_bash_tool: "Remote shell",
	remote_workbench: "Workbench",
	submit_feedback: "Submit feedback",
	wait_for_connections: "Wait for connections",
	manage_skill: "Manage skill",
	search_skills: "Search skills",
};
const APP_LABEL_KEYS = Object.keys(APP_LABELS).sort((left, right) => right.length - left.length);
const PROVIDER_PREFIX_PATTERNS = new Map<string, RegExp>();

function providerPrefixPattern(provider: string): RegExp {
	let pattern = PROVIDER_PREFIX_PATTERNS.get(provider);
	if (!pattern) {
		pattern = new RegExp(`^${provider.replace(/[^A-Za-z0-9_-]/g, "")}[_-]+`, "i");
		PROVIDER_PREFIX_PATTERNS.set(provider, pattern);
	}
	return pattern;
}

function titleWord(word: string, first: boolean): string {
	const lower = word.toLowerCase();
	if (ACRONYMS.has(lower)) return lower.toUpperCase();
	if (!first) return lower;
	return lower.slice(0, 1).toUpperCase() + lower.slice(1);
}

/** Provider id from a namespaced tool, or the supplied fallback. */
export function mcpProviderFor(name: string, fallback = ""): string {
	const prefix = name.includes("__") ? name.split("__", 1)[0]! : "";
	return (prefix || fallback || "mcp").toLowerCase();
}

/**
 * Clean provider-relative tool label.
 *
 * `composio__COMPOSIO_GET_TOOL_SCHEMAS` -> `Tool schemas`
 * `vercel__list_projects` -> `List projects`
 * `GoogleDrive_ListFiles` -> `Google Drive · List files`
 */
export function cleanMcpToolName(raw: string, provider = ""): string {
	let name = (raw || "").trim();
	if (!name) return "Tool";
	if (provider && name.toLowerCase().startsWith(`${provider.toLowerCase()}__`)) {
		name = name.slice(provider.length + 2);
	}
	// Drop any remaining connector namespace, then a repeated provider prefix
	// from providers that include their own brand in the remote name.
	name = name.replace(/^[A-Za-z0-9-]+__/, "");
	if (provider) name = name.replace(providerPrefixPattern(provider), "");
	const key = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
	if (KNOWN_TOOL_LABELS[key]) return KNOWN_TOOL_LABELS[key];
	// Tool vendors that encode an app + operation in one identifier read much
	// better with the app separated: GoogleDrive_ListFiles.
	const appSplit = /^([A-Z][A-Za-z0-9]*?(?:Drive|Mail|Calendar|Docs|Sheets|Slides|GitHub|Slack|Notion|Linear|Stripe|Gmail))_([A-Za-z0-9_]+)$/.exec(name);
	if (appSplit) {
		const app = appSplit[1]!.replace(/([a-z0-9])([A-Z])/g, "$1 $2");
		return `${app} · ${actionLabel(appSplit[2]!)}`;
	}
	// Composio app tools are `{TOOLKIT}_{ACTION}`; use the known app's brand
	// casing and show the action as a readable suffix. Longest prefixes win so
	// MICROSOFT_TEAMS and GOOGLE_DRIVE are not mistaken for MICROSOFT/GOOGLE.
	const appKey = APP_LABEL_KEYS.find(candidate => name.startsWith(`${candidate}_`));
	if (appKey) {
		const app = APP_LABELS[appKey]!;
		const action = name.slice(appKey.length + 1);
		if (app.toLowerCase() !== provider.toLowerCase()) return `${app} · ${actionLabel(action)}`;
		return actionLabel(action);
	}
	// Unknown all-caps toolkit slugs still get the same `App · Action` shape.
	const capsSplit = /^([A-Z][A-Z0-9]*)_([A-Z0-9_]+)$/.exec(name);
	if (capsSplit && !ACTION_WORDS.has(capsSplit[1]!.toLowerCase())) {
		const app = titleWord(capsSplit[1]!, true);
		const action = actionLabel(capsSplit[2]!);
		if (app.toLowerCase() !== provider.toLowerCase()) return `${app} · ${action}`;
		return action;
	}
	return actionLabel(name);
}

/** Human words for an action segment (`FETCH_EMAILS` -> `Fetch emails`). */
function actionLabel(raw: string): string {
	const words = raw
		.replace(/([a-z0-9])([A-Z])/g, "$1 $2")
		.replace(/[._-]+/g, " ")
		.split(/\s+/)
		.filter(Boolean);
	if (!words.length) return "Tool";
	return words.map((word, index) => titleWord(word, index === 0)).join(" ");
}

/** Short human status; never expose policy enum spellings such as needs_approval. */
export function mcpToolStatus(verdict: string | null | undefined): string {
	const value = (verdict ?? "").toLowerCase();
	if (value === "deny" || value === "blocked" || value === "denied") return "blocked";
	if (value === "needs_approval" || value === "approval" || value === "ask") return "approval";
	return "ready";
}

/** One-line, bounded tool description for a picker row. */
export function cleanMcpDescription(text: string | null | undefined, limit = 96): string {
	const clean = (text ?? "")
		.replace(/```[\s\S]*?```/g, " ")
		.replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
		.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
		.replace(/<[^>]*>/g, " ")
		.replace(/[*_`#>|]+/g, " ")
		.replace(/\s+([.,;:!?])/g, "$1")
		.replace(/\s+/g, " ")
		.trim();
	if (!clean) return "";
	return clean.length <= limit ? clean : `${clean.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

/** The composer mention a user and the model can both recognize. */
export function mcpToolMention(provider: string, displayName: string): string {
	return `@${provider} ${displayName}: `;
}

/** Display label for a tool call card (`mcp_call` carries the namespaced name). */
export function toolCallDisplayName(name: string, args: Record<string, unknown> = {}): string {
	if (name === "mcp_call") {
		const target = typeof args.name === "string" ? args.name : "";
		if (target) {
			const provider = mcpProviderFor(target);
			return `@${provider} · ${cleanMcpToolName(target, provider)}`;
		}
		return "MCP tool call";
	}
	if (name === "mcp_list") return "MCP catalog";
	if (name === "mcp_describe") return "MCP tool details";
	if (name === "mcp_connect") return "MCP connect";
	if (name === "mcp_disconnect") return "MCP remove";
	return "";
}

