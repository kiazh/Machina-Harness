// Human-operated MCP management. Inspection never connects or discovers tools.
import type { SelectItem } from "@earendil-works/pi-tui";
import type { MCPCatalogEntry, MCPConnectorConfig, MCPOAuthSettings, MCPServerView, MCPToolView } from "../protocol.ts";
import { sanitizeDisplay } from "../design/text.ts";
import { cleanMcpDescription, cleanMcpToolName, mcpToolMention, mcpToolStatus } from "../mcpNames.ts";
import { confirm, options, requireSession, type Command, type FeatureHost } from "./types.ts";
import { authorizeConnector } from "./mcp-oauth.ts";
const stateOf = (server: MCPServerView): string => server.enabled ? server.state : "disabled";
const nameOf = (server: MCPServerView): string => sanitizeDisplay(server.display_name || server.id);
const MANUAL_ACTION = "__add__";
const PROVIDER_ORDER = ["composio", "arcade", "github", "firecrawl", "vercel"] as const;
const PROVIDER_LABELS: Record<string, string> = {
 composio: "Composio",
 arcade: "Arcade",
 github: "GitHub",
 firecrawl: "Firecrawl",
 vercel: "Vercel",
};
const isMcpUrl = (value: string): boolean => /^https?:\/\//i.test(value);

/** Centered overlay on the real TUI (same family as /keys); plain hosts fall back. */
function pickMcp(host: FeatureHost, title: string, items: SelectItem[]): Promise<SelectItem | undefined> {
 return host.pickCentered ? host.pickCentered(title, items, { count: false }) : host.pick(title, items, { count: false });
}

/** Only the curated provider rows are shown; other configured servers are External. */
function providerStarters(catalog: MCPCatalogEntry[]): MCPCatalogEntry[] {
 return PROVIDER_ORDER
  .map(id => catalog.find(entry => entry.id === id && entry.kind === "connector" && entry.config))
  .filter((entry): entry is MCPCatalogEntry => entry !== undefined);
}

const errorText = (error: unknown): string => sanitizeDisplay(error instanceof Error ? error.message : String(error));
const authRequired = (error: unknown): boolean => /oauth|authoriz|401|403|auth_required/i.test(errorText(error));

interface ConnectPreview {
 digest: string;
 proposal?: { argv?: string[]; cwd?: string | null; network?: string[]; backend?: string; endpoint?: string; contentPreview?: string };
 description?: string;
}

/** Connect a saved server; one Enter is the consent, including OAuth. */
async function connectServer(
 host: FeatureHost,
 id: string,
 label: string,
 options: { docsUrl?: string; review?: boolean } = {},
): Promise<boolean> {
 const session = requireSession(host);
 const preview = await host.request<ConnectPreview>("mcp.preview", { id, sessionId: session.id });
 if (options.review) {
  const review = [
   `Connect ${label}?`,
   preview.proposal?.argv?.length ? `Command argv: ${JSON.stringify(preview.proposal.argv)}` : "",
   preview.proposal?.cwd ? `Working directory: ${preview.proposal.cwd}` : "",
   preview.proposal?.endpoint ? `Endpoint: ${preview.proposal.endpoint}` : "",
   preview.proposal?.network?.length ? `Network: ${JSON.stringify(preview.proposal.network)}` : "",
   preview.proposal?.backend ? `Backend: ${preview.proposal.backend}` : "",
   preview.proposal?.contentPreview ? `Configuration: ${preview.proposal.contentPreview}` : "",
   preview.description || "",
  ].filter(Boolean).map(line => sanitizeDisplay(line)).join("\n");
  if (!await confirm(host, review, "Connect")) return false;
 }
 const connect = async (digest: string): Promise<boolean> => {
  const result = await host.request<{ server: MCPServerView }>("mcp.connect", { id, sessionId: session.id, digest });
  host.print(describe(result.server), result.server.state === "connected" ? "success" : "warning");
  return result.server.state === "connected";
 };
 try {
  return await connect(preview.digest);
 } catch (error) {
  if (!authRequired(error)) {
   host.print(`Could not connect ${label}: ${errorText(error)}${options.docsUrl ? `\nDocs: ${options.docsUrl}` : ""}`, "error");
   return false;
  }
  if (!await authorizeConnector(host, id, { auto: true })) return false;
  const fresh = await host.request<ConnectPreview>("mcp.preview", { id, sessionId: session.id });
  try {
   return await connect(fresh.digest);
  } catch (retryError) {
   host.print(`Could not connect ${label}: ${errorText(retryError)}${options.docsUrl ? `\nDocs: ${options.docsUrl}` : ""}`, "error");
   return false;
  }
 }
}

async function addStarter(host: FeatureHost, entry: MCPCatalogEntry): Promise<void> {
 if (!entry.config) return;
 const config = { ...entry.config };
 if (entry.setup.type === "gateway_url") {
  host.print(
   [`${entry.displayName} setup:`, ...entry.setup.instructions].join("\n"),
   "plain",
  );
  const answer = await host.askText({
   title: entry.setup.prompt ?? "Paste the gateway URL",
   description: entry.setup.description,
   example: entry.setup.example,
  });
  if (answer === undefined || !answer.trim()) return;
  let endpoint: URL;
  try {
   endpoint = new URL(answer.trim());
  } catch {
   throw new Error("Use the HTTPS gateway URL copied from Arcade. Open /mcp to try again.");
  }
  if (endpoint.protocol !== "https:" || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
   throw new Error("Use an HTTPS gateway URL without credentials, query parameters or fragments. Open /mcp to try again.");
  }
  config.endpoint = endpoint.toString();
 }
 if (entry.setup.type === "secret") {
  host.print(
   [`${entry.displayName} setup:`, ...entry.setup.instructions].join("\n"),
   "plain",
  );
  if (!entry.setup.secretKey) throw new Error("This starter is missing its secret reference. Open /mcp to try again.");
  const secret = await host.askText({
   title: entry.setup.prompt ?? "Paste the access token",
   description: entry.setup.description,
   example: entry.setup.example,
   secret: true,
  });
  if (secret === undefined || !secret.trim()) return;
  try {
   await host.request("secrets.set", { key: entry.setup.secretKey, value: secret.trim(), persist: true });
  } catch {
   throw new Error("Could not save the access token to .env. Check secret storage and try /mcp again.");
  }
 }
 const { server } = await host.request<{ server: MCPServerView }>("mcp.configure", { config });
 await connectServer(host, server.id, nameOf(server), { docsUrl: entry.docsUrl });
}

interface CachedTools {
 tools: MCPToolView[];
 truncated: boolean;
}

async function cachedTools(host: FeatureHost, id: string): Promise<CachedTools> {
 try {
  // `cached` reads the last discovered catalog from the registry. It never
  // re-lists over the network, so opening /mcp does not wait on providers.
  return await host.request<CachedTools>("mcp.tools", { id, cached: true });
 } catch {
  return { tools: [], truncated: false };
 }
}

async function setProviderSecret(host: FeatureHost, entry: MCPCatalogEntry): Promise<boolean> {
 const secretKey = entry.setup.secretKey;
 if (!secretKey) {
  host.print(`${entry.displayName} does not expose a token setup step.`, "warning");
  return false;
 }
 host.print([`${entry.displayName} setup:`, ...entry.setup.instructions].join("\n"), "plain");
 const value = await host.askText({
  title: entry.setup.prompt ?? `Paste the ${entry.displayName} access token`,
  description: entry.setup.description,
  example: entry.setup.example,
  secret: true,
 });
 if (value === undefined || !value.trim()) return false;
 try {
  await host.request("secrets.set", { key: secretKey, value: value.trim(), persist: true });
  return true;
 } catch {
  throw new Error(`Could not save ${secretKey} to .env. Check secret storage and try /mcp again.`);
 }
}

function displayTool(tool: MCPToolView, provider: string): string {
 return cleanMcpToolName(tool.remote_name ?? tool.name, provider);
}

function toolRow(server: MCPServerView, tool: MCPToolView): SelectItem {
 const name = displayTool(tool, server.id);
 const description = cleanMcpDescription(tool.description);
 return {
  value: `tool:${server.id}:${tool.name}`,
  label: `${name}  ·  ${mcpToolStatus(tool.verdict)}`,
  description: `@${server.id}${description ? ` · ${description}` : ""}`,
 };
}

function selectTool(host: FeatureHost, server: MCPServerView, tool: MCPToolView): void {
 const name = displayTool(tool, server.id);
 const mention = mcpToolMention(server.id, name);
 if (host.prefillInput) {
  host.prefillInput(mention);
  host.print(`MCP tool selected: @${server.id} · ${name}\nFinish the request and press Enter to run it.`, "info");
  return;
 }
 host.print(`MCP tool: @${server.id} · ${name}${tool.description ? `\n${cleanMcpDescription(tool.description, 240)}` : ""}`, "plain");
}

async function authorizeAndConnect(host: FeatureHost, server: MCPServerView, docsUrl?: string): Promise<boolean> {
 const authorized = await authorizeConnector(host, server.id);
 if (!authorized) return false;
 return connectServer(host, server.id, nameOf(server), { docsUrl });
}

async function externalServerMenu(host: FeatureHost, server: MCPServerView): Promise<void> {
 const { tools, truncated } = await cachedTools(host, server.id);
 const lines = [describe(server)];
 if (!tools.length) lines.push("No tools are cached for this server.");
 host.print(lines.join("\n"), "plain");
 const rows: SelectItem[] = tools.map(tool => toolRow(server, tool));
 if (truncated) {
  rows.push({ value: "__more__", label: "More tools on server", description: "The server truncated its catalog at the discovery limit" });
 }
 if (server.transport === "streamable_http") rows.push({ value: "authorize", label: "Authorize", description: "Open provider authorization" });
 rows.push({ value: "remove", label: "Remove server", description: "Delete this external configuration" });
 rows.push({ value: "back", label: "Back", description: "Return to the MCP menu" });
 const action = await pickMcp(host, `${nameOf(server)} · tools`, rows);
 if (!action || action.value === "back") return;
 if (action.value === "authorize") {
  await authorizeAndConnect(host, server);
  return;
 }
 if (action.value === "remove" && await confirm(host, `Remove ${nameOf(server)}?`, "Remove server")) {
  await host.request("mcp.remove", { id: server.id });
  host.print(`${nameOf(server)} removed.`, "success");
  return;
 }
 if (action.value.startsWith("tool:")) {
  const tool = tools.find(candidate => action.value === `tool:${server.id}:${candidate.name}`);
  if (tool) selectTool(host, server, tool);
 }
}

function deriveServerId(endpoint: URL, servers: MCPServerView[]): string {
 const parts = [endpoint.hostname.replace(/^www\./i, "")];
 for (const segment of endpoint.pathname.split("/")) {
  const clean = segment.trim().toLowerCase();
  if (clean && !["mcp", "sse", "message", "messages"].includes(clean)) parts.push(clean);
 }
 let base = parts.join("-").replace(/[^a-z0-9_-]+/g, "-").replace(/-{2,}/g, "-").replace(/^[-_]+|[-_]+$/g, "").slice(0, 64);
 base = base.replace(/[-_]+$/g, "");
 if (!base) base = "mcp";
 if (!/^[a-z0-9]/.test(base)) base = `mcp-${base}`.slice(0, 64).replace(/[-_]+$/g, "");
 const used = new Set(servers.map(server => server.id));
 let id = base;
 for (let suffix = 2; used.has(id); suffix++) {
  const tail = `-${suffix}`;
  id = `${base.slice(0, 64 - tail.length)}${tail}`;
 }
 return id;
}

/** Paste an MCP link: derive a stable id, save, connect. */
async function addUrl(host: FeatureHost, raw: string, servers: MCPServerView[]): Promise<void> {
 const endpoint = new URL(raw.trim());
 if (!["https:", "http:"].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
  throw new Error("Use an HTTP(S) MCP link without credentials, query parameters or fragments.");
 }
 const id = deriveServerId(endpoint, servers);
 const loopback = /^(localhost|127(?:\.\d+){3}|\[?::1\]?)$/i.test(endpoint.hostname);
 const config: MCPConnectorConfig = {
  id,
  display_name: id,
  transport: "streamable_http",
  endpoint: endpoint.toString(),
  trust: "untrusted",
 };
 if (loopback) config.local_access = true;
 requireSession(host);
 const { server } = await host.request<{ server: MCPServerView }>("mcp.configure", { config });
 host.print(`Adding ${nameOf(server)}…`, "plain");
 await connectServer(host, server.id, nameOf(server));
}
function describe(server: MCPServerView): string {
 const lines = [`${nameOf(server)} · ${stateOf(server)}`, `ID: ${sanitizeDisplay(server.id)} · Transport: ${sanitizeDisplay(server.transport)}`];
 if (server.state === "connected") {
  if (server.server_name) lines.push(`Server: ${sanitizeDisplay(server.server_name)}${server.server_version ? ` ${sanitizeDisplay(server.server_version)}` : ""}`);
  if (server.protocol_version) lines.push(`Protocol: ${sanitizeDisplay(server.protocol_version)}`);
 }
 if (server.error) lines.push(`Error: ${sanitizeDisplay(server.error)}`);
 return lines.join("\n");
}
function stringList(value: unknown, field: string): string[] {
 if (!Array.isArray(value) || value.some(item => typeof item !== "string")) throw new Error(`${field} must be a JSON array of strings. Open /mcp to try again.`);
 return value;
}
function stringMap(value: unknown, field: string): Record<string, string> {
 if (!value || typeof value !== "object" || Array.isArray(value) || Object.values(value).some(item => typeof item !== "string")) {
  throw new Error(`${field} must be a JSON object of header names to secret names. Open /mcp to try again.`);
 }
 return value as Record<string, string>;
}
async function addServer(host: FeatureHost): Promise<void> {
 const transport = await pickMcp(host, "Add MCP server · transport", [
  { value: "stdio", label: "Local command", description: "Start a local MCP process when you connect" },
  { value: "streamable_http", label: "HTTP endpoint", description: "Connect to a remote MCP server" },
 ]);
 if (!transport) return;
 const id = await host.askText({ title: "Server ID", description: "A unique name for this connection", example: "docs" });
 if (id === undefined) return;
 if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(id.trim())) throw new Error("Server ID must start with a lowercase letter or number and use lowercase letters, numbers, underscores or dashes (maximum 64 characters). Open /mcp to try again.");
 const name = await host.askText({ title: "Display name", initial: id.trim() });
 if (name === undefined) return;
 const config: MCPConnectorConfig = { id: id.trim(), display_name: name.trim() || id.trim(), transport: transport.value as MCPConnectorConfig["transport"], trust: "untrusted" };
 if (config.transport === "stdio") {
  const command = await host.askText({ title: "Executable", description: "Executable path or command name; put arguments in the next field", example: "node" });
  if (command === undefined) return;
  if (!command.trim()) throw new Error("An executable is required. Open /mcp to try again.");
  config.command = command.trim();
  const argv = await host.askText({ title: "Arguments (JSON array)", initial: "[]", example: '["/path/to/server.js"]' });
  if (argv === undefined) return;
  config.argv = stringList(JSON.parse(argv || "[]"), "Arguments");
  const cwd = await host.askText({ title: "Working directory (optional)", description: "Leave empty to use the harness configured workspace directory" });
  if (cwd === undefined) return;
  if (cwd.trim()) config.cwd = cwd.trim();
 } else {
  const endpoint = await host.askText({ title: "MCP endpoint", example: "https://example.com/mcp", description: "Do not include passwords, tokens or credential query parameters" });
  if (endpoint === undefined) return;
  const url = new URL(endpoint.trim());
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error("Use an HTTP(S) endpoint without credentials, query parameters or fragments. Open /mcp to try again.");
  config.endpoint = endpoint.trim();
  const authentication = await pickMcp(host, "HTTP authentication", [
   { value: "none", label: "None", description: "Connect without credentials" },
   { value: "oauth", label: "OAuth", description: "Authorize in a browser with PKCE" },
  ]);
  if (!authentication) return;
  if (authentication.value === "oauth") {
   const oauth = await askOAuthSettings(host);
   if (!oauth) return;
   config.oauth = oauth;
  }
 }
 const advanced = await host.askText({ title: "Advanced options (JSON, optional)", initial: "{}", description: "Allowed: local_access, secret_refs/header_refs (names only), tool_allowlist, trust (untrusted or trusted_read). Never enter secret values.", example: '{"header_refs":{"Authorization":"GITHUB_MCP_PAT"},"tool_allowlist":["search"]}' });
 if (advanced === undefined) return;
 const extra: unknown = JSON.parse(advanced || "{}");
 if (!extra || typeof extra !== "object" || Array.isArray(extra)) throw new Error("Advanced options must be a JSON object. Open /mcp to try again.");
 for (const [key, value] of Object.entries(extra)) {
  if (key === "secret_refs" || key === "tool_allowlist") config[key] = stringList(value, key);
  else if (key === "header_refs") config.header_refs = stringMap(value, key);
  else if (key === "local_access" && typeof value === "boolean") config.local_access = value;
  else if (key === "trust" && (value === "untrusted" || value === "trusted_read")) config.trust = value;
  else throw new Error(`Unsupported advanced option: ${sanitizeDisplay(key)}. Use secret_refs/header_refs for secret names; never enter secret values.`);
 }
 const { server } = await host.request<{ server: MCPServerView }>("mcp.configure", { config });
 await connectServer(host, server.id, nameOf(server));
}
async function askOAuthSettings(host: FeatureHost, existing?: MCPOAuthSettings): Promise<MCPOAuthSettings | undefined> {
 const mode = await pickMcp(host, "OAuth client registration", [
  { value: "auto", label: "Automatic", description: "Use dynamic registration when the server supports it" },
  { value: "preregistered", label: "Pre-registered", description: "Use an existing client ID (required by Google, GitHub and some other providers)" },
  { value: "metadata_document", label: "Client metadata document", description: "Reference a published client metadata URL" },
 ]);
 if (!mode) return undefined;
 const settings: MCPOAuthSettings = {
  client_id: "", client_metadata_url: "", client_secret_ref: "",
  token_endpoint_auth_method: "none", issuer: "", scope_allowlist: [], callback_port: 0,
  ...existing,
  client_mode: mode.value,
 };
 if (existing?.client_mode !== mode.value) {
  settings.client_id = "";
  settings.client_metadata_url = "";
  settings.client_secret_ref = "";
  settings.token_endpoint_auth_method = "none";
 }
 if (mode.value === "preregistered") {
  const clientId = await host.askText({ title: "Client ID", initial: existing?.client_id, description: "Public client ID from the provider" });
  if (clientId === undefined) return undefined;
  settings.client_id = clientId.trim();
  // OAuth client secrets never belong in mcp.json. Collect the value through
  // the masked editor, persist it in .env, and keep only the reference name.
  const secret = await host.askText({
   title: "Client secret (optional, masked)",
   description: "Stored in .env as MCP_OAUTH_CLIENT_SECRET. Leave empty for a public client or to keep the current secret.",
   secret: true,
   allowEmpty: true,
  });
  if (secret === undefined) return undefined;
  if (secret.trim()) {
   try {
    await host.request("secrets.set", { key: "MCP_OAUTH_CLIENT_SECRET", value: secret.trim(), persist: true });
   } catch {
    throw new Error("Could not save the OAuth client secret to .env. Check secret storage and try /mcp again.");
   }
   settings.client_secret_ref = "MCP_OAUTH_CLIENT_SECRET";
   settings.token_endpoint_auth_method = "client_secret_basic";
  } else if (existing?.client_secret_ref) {
   settings.client_secret_ref = existing.client_secret_ref;
   settings.token_endpoint_auth_method = "client_secret_basic";
  } else {
   settings.client_secret_ref = "";
   settings.token_endpoint_auth_method = "none";
  }
  // Google and other pre-registered providers require an exact registered
  // redirect URI, so the loopback port must be stable instead of ephemeral.
  const portText = await host.askText({
   title: "Callback port (optional)",
   initial: String(existing?.callback_port ?? 0),
   description: "Use a fixed port registered with the provider as http://127.0.0.1:<port>/oauth/callback. 0 chooses a free port.",
  });
  if (portText === undefined) return undefined;
  const port = Number((portText || "0").trim());
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
   throw new Error("Callback port must be an integer from 0 to 65535. Open /mcp to try again.");
  }
  settings.callback_port = port;
 }
 if (mode.value === "metadata_document") {
  const documentUrl = await host.askText({ title: "Client metadata URL", initial: existing?.client_metadata_url, example: "https://client.example.com/metadata" });
  if (documentUrl === undefined) return undefined;
  settings.client_metadata_url = documentUrl.trim();
  settings.client_id = settings.client_metadata_url;
 }
 const issuer = await host.askText({ title: "Issuer (optional)", initial: existing?.issuer, description: "Leave empty to use the server-advertised issuer" });
 if (issuer === undefined) return undefined;
 settings.issuer = issuer.trim();
 const scopes = await host.askText({ title: "Allowed scopes (JSON array, optional)", initial: JSON.stringify(existing?.scope_allowlist ?? []), description: "Empty means every discovered scope needs explicit review" });
 if (scopes === undefined) return undefined;
 const parsed: unknown = JSON.parse(scopes || "[]");
 if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== "string")) throw new Error("Allowed scopes must be a JSON array of strings. Open /mcp to try again.");
 settings.scope_allowlist = parsed;
 return settings;
}
export const mcpCommand: Command = {
 name: "mcp",
 description: "Providers, cached tools, and external MCP servers",
 argumentHint: "[<url> | add [<url>] | list | status <id>]",
 getArgumentCompletions: options([["add", "Add and connect from a pasted MCP link"], ["list", "List configured harness servers"], ["status", "Inspect a server by ID"]]),
 async run(args, host) {
  const words = args.trim().split(/\s+/).filter(Boolean);
  if (words[0] === "status" && words.length === 2) {
   const { server } = await host.request<{ server: MCPServerView }>("mcp.status", { id: words[1] });
   host.print(describe(server), "plain");
   return;
  }
  if (words[0] === "list" && words.length === 1) {
   const { servers } = await host.request<{ servers: MCPServerView[] }>("mcp.list");
   if (servers.length) host.print(servers.map(server => `${nameOf(server)} · ${stateOf(server)} · ${sanitizeDisplay(server.id)}`).join("\n"), "plain");
   else host.print("No MCP servers registered in this harness. Run /mcp to add one.", "plain");
   return;
  }
  const wantsAdd = words[0] === "add";
  if (wantsAdd && words.length === 1) {
   await addServer(host);
   return;
  }
  const pasted = wantsAdd ? words[1] : words.length === 1 ? words[0] : undefined;
  if (pasted && words.length === (wantsAdd ? 2 : 1)) {
   if (!isMcpUrl(pasted)) {
    host.print("Paste an http(s) MCP endpoint, for example /mcp https://example.com/mcp", "warning");
    return;
   }
   const { servers } = await host.request<{ servers: MCPServerView[] }>("mcp.list");
   await addUrl(host, pasted, servers);
   return;
  }
  if (words.length) {
   host.print("Usage: /mcp · /mcp <url> · /mcp add [<url>] · /mcp list · /mcp status <id>", "warning");
   return;
  }
 for (;;) {
  const [{ servers }, catalogResult] = await Promise.all([
  host.request<{ servers: MCPServerView[] }>("mcp.list"),
  host.request<{ entries: MCPCatalogEntry[] }>("mcp.catalog").catch(() => ({ entries: [] as MCPCatalogEntry[] })),
 ]);
 const catalog = catalogResult.entries;
 const providers = providerStarters(catalog);
 const providerIds = new Set(providers.map(entry => entry.id));
 const external = servers.filter(server => !providerIds.has(server.id));
 // Cached catalogs are read in parallel from the local registry. Opening the
 // menu never launches or re-lists a provider, so it stays instant.
 const toolEntries = await Promise.all(
  providers
   .map(entry => servers.find(server => server.id === entry.id))
   .filter((server): server is MCPServerView => Boolean(server && server.state === "connected"))
   .map(async server => [server.id, await cachedTools(host, server.id)] as const),
 );
 const toolsById = new Map(toolEntries);
 if (!servers.length) {
  host.print(
   providers.length
    ? "No MCP servers registered yet. Choose a provider below or add an external server."
    : "No MCP servers registered yet. Add an external server or paste an MCP link.",
   "plain",
  );
 }
 const rows: Array<SelectItem & { section?: true; muted?: boolean }> = [];
 for (const entry of providers) {
  rows.push({ value: `__provider__:${entry.id}`, label: sanitizeDisplay(PROVIDER_LABELS[entry.id] ?? entry.displayName), section: true });
  const server = servers.find(candidate => candidate.id === entry.id);
  if (!server) {
   rows.push({ value: `setup:${entry.id}`, label: "Set up", description: sanitizeDisplay(entry.setup.instructions[0] ?? entry.description) });
   continue;
  }
  if (server.state !== "connected") {
   if (entry.setup.type === "oauth" || entry.setup.type === "gateway_url") {
    rows.push({ value: `auth:${entry.id}`, label: "Authorize", description: "Open provider authorization" });
   } else if (entry.setup.type === "secret") {
    rows.push({ value: `token:${entry.id}`, label: "Set access token", description: "Save a masked token for this provider" });
   } else {
    rows.push({ value: `__provider_status__:${entry.id}`, label: "Not connected", section: true, muted: true });
   }
  }
  const { tools, truncated } = toolsById.get(entry.id) ?? { tools: [], truncated: false };
  for (const tool of tools) rows.push(toolRow(server, tool));
  if (server.state === "connected" && !tools.length) {
   rows.push({ value: `__provider_empty__:${entry.id}`, label: "No tools discovered", section: true, muted: true });
  } else if (truncated) {
   rows.push({ value: `__provider_more__:${entry.id}`, label: "More tools on server", section: true, muted: true });
  }
  rows.push({ value: `remove:${entry.id}`, label: "Remove server", description: "Delete this configuration" });
 }
 rows.push({ value: "__section_external__", label: "External", section: true });
 rows.push(...external.map(server => ({
  value: `external:${server.id}`,
  label: `${nameOf(server)}  ·  ${stateOf(server)}`,
  description: `${sanitizeDisplay(server.transport)} · ${sanitizeDisplay(server.id)}`,
 })));
 rows.push({ value: MANUAL_ACTION, label: "Add external server", description: "HTTP endpoint or local command" });
 const selected = await pickMcp(host, "MCP servers", rows);
 if (!selected) return;
 if (selected.value.startsWith("tool:")) {
  const [, providerId, ...rest] = selected.value.split(":");
  const toolName = rest.join(":");
  const server = servers.find(candidate => candidate.id === providerId);
  const tool = toolsById.get(providerId ?? "")?.tools.find(candidate => candidate.name === toolName);
  if (server && tool) selectTool(host, server, tool);
  return;
 }
 if (selected.value === MANUAL_ACTION) {
  await addServer(host);
  continue;
 }
 if (selected.value.startsWith("setup:")) {
  const provider = providers.find(entry => `setup:${entry.id}` === selected.value);
  if (provider) await addStarter(host, provider);
  continue;
 }
 if (selected.value.startsWith("auth:")) {
  const provider = providers.find(entry => `auth:${entry.id}` === selected.value);
  const server = provider ? servers.find(candidate => candidate.id === provider.id) : undefined;
  if (provider && server) await authorizeAndConnect(host, server, provider.docsUrl);
  continue;
 }
 if (selected.value.startsWith("token:")) {
  const provider = providers.find(entry => `token:${entry.id}` === selected.value);
  if (provider && await setProviderSecret(host, provider)) {
   const server = servers.find(candidate => candidate.id === provider.id);
   if (server) await connectServer(host, server.id, nameOf(server), { docsUrl: provider.docsUrl });
  }
  continue;
 }
 if (selected.value.startsWith("remove:")) {
  const provider = providers.find(entry => `remove:${entry.id}` === selected.value);
  if (provider) {
   const server = servers.find(candidate => candidate.id === provider.id);
   if (server && await confirm(host, `Remove ${nameOf(server)}?`, "Remove server")) {
    await host.request("mcp.remove", { id: provider.id });
    host.print(`${nameOf(server)} removed.`, "success");
   }
  }
  continue;
 }
 if (selected.value.startsWith("external:")) {
  const server = external.find(candidate => `external:${candidate.id}` === selected.value);
  if (server) await externalServerMenu(host, server);
  continue;
 }
 }
 },
};
