# Using MCP servers

MCP support is part of the default backend installation. Restart/reinstall after updating so the pinned SDK dependency and new TUI commands are available.

`/mcp` opens the same centered, searchable overlay family as `/keys`; it does not use the bottom input picker. Curated providers (**Composio**, **Arcade**, **Firecrawl**, **GitHub**, **Vercel**) appear as headers with their cached tools underneath, followed by an **External** section for manually added servers. Tool rows use short provider-relative labels (`List projects`, not `vercel__list_projects`) and a simple `ready` / `approval` / `blocked` status. Selecting a tool closes the menu and prefills the composer with an explicit `@provider` request, for example `@vercel List projects: `.

Opening the menu never launches a provider or re-lists its catalog over the network. A local cached catalog is rendered immediately; **Add external server** opens the local stdio or HTTP form.

1. Open `/mcp`, choose **Add external server**, and select local stdio or HTTP.
2. Give it a lowercase ID (up to 64 characters), display name and launch fields.
3. For stdio, enter the executable and arguments separately, for example `node` and `["/absolute/path/to/server.js"]`. The executable/server must already be installed; the harness does not download it. Choose its working directory, or use the configured workspace.
4. Optional advanced JSON supports `secret_refs`, `header_refs`, `tool_allowlist`, `trust` and `local_access`. Leave `trust` as `untrusted` until you have independently verified specific read-only tools. Private HTTP destinations require `local_access: true`; metadata destinations are always refused.
5. For a provider that is configured but not authorized, choose **Authorize**. That is the only connection action: it opens the provider flow and reconnects on success. There is no separate **Connect**, **Disconnect** or **Logout** button. Configured HTTP servers with usable cached authorization reconnect at gateway startup; stdio servers launch only through an explicit setup/connect flow.
6. Ask the agent to use the connected server, or select a tool row to prefill an `@provider` request. The agent pages `mcp_list`, reads a selected `mcp_describe`, and calls `mcp_call` with `{name: "server__tool", arguments: {...}}`. Pending tool approvals use the ordinary approval controls. A leading `@provider` mention in a normal prompt is a request to use that connector automatically.
7. Use **Remove server** to retire the connection and delete its saved configuration. There is no "forget access" / logout action in the menu; removing the server is the complete cleanup.

Provider secrets remain in the existing environment/mounted/Vault/AWS secret boundary. `secret_refs: ["MY_MCP_TOKEN"]` resolves that named secret at launch and passes it to the stdio server. Never put tokens in command arguments, endpoints or configuration JSON. Missing references refuse launch. Errors and server output redact known launch secret values.

## Managed connectors

The **Composio** starter connects Machina to `https://connect.composio.dev/mcp`, a managed MCP gateway. It uses ordinary MCP OAuth, so no Google Cloud project or provider OAuth client is required. Authorize Composio in the browser, then ask Machina to connect Google Drive, Gmail, Calendar, or another supported app; the first tool call returns a Composio authorization link for that provider.

Composio is a third party and receives brokered access to connected apps. Review its scopes, retention policy and trust model before using sensitive data. Its gateway exposes meta-tools rather than a fixed Google-only tool list. App tools discovered through Composio are rendered as familiar `Gmail · Fetch emails`, `GitHub · Create an issue`, and `Google Drive · List files` labels; descriptions are stripped of Markdown, links and HTML before display.

Arcade works the same way through a browser OAuth flow, but each account has a unique gateway URL. Select the **Arcade** starter: it explains the free setup, then prompts for `https://api.arcade.dev/mcp/<your-gateway-slug>` and saves that URL before starting OAuth. Arcade's shared Google app uses narrower scopes such as `drive.file`; connecting a broader scope set requires a custom Google OAuth provider in Arcade.

**Firecrawl** now uses its hosted account endpoint at `https://mcp.firecrawl.dev/v2/mcp-oauth`. It supports MCP OAuth with dynamic client registration, so the starter no longer requires a local Firecrawl bridge, a local API URL, or a pasted API key. The account endpoint exposes the full tool surface allowed by the Firecrawl plan. If you want the keyless trial instead, add `https://mcp.firecrawl.dev/v2/mcp` as an external server; it exposes only Search, Scrape, and Parse.

The **GitHub** starter asks for a masked personal access token. It is stored in `.env` as `GITHUB_MCP_PAT` and sent only to GitHub's remote MCP endpoint. **Vercel** uses Vercel's standard MCP OAuth flow. Other configured servers appear under **External** in the `/mcp` menu.

Connected connector IDs and their cached tool names are included in the model's runtime context, and `@provider` mentions are treated as routing instructions. For example, `@vercel list my projects` or `@composio use Gmail to write a draft` should cause `mcp_list` / `mcp_describe` / `mcp_call` without asking the user to connect again. Composio exposes meta-tools rather than a fixed Gmail/Drive catalog, so the agent uses its search/execute tools to reach the connected app. Ordinary exact approvals still apply to side-effecting calls.

## Authority and lifecycle

Human management RPC methods are `mcp.configure`, `mcp.remove`, `mcp.preview`, `mcp.connect`, `mcp.disconnect` and `mcp.tools`; existing `mcp.list` and `mcp.status` remain read-only. `mcp.tools` with `{cached: true}` reads the last discovered catalog without contacting the provider, which keeps the human menu instant. `mcp.connect` takes the current session and the digest returned by `mcp.preview`. The human confirmation is recorded/claimed through the existing broker. Configuration is not a model tool, and server callbacks cannot launch another connector or approve a tool.

Native calls bind the complete non-secret configuration/revision, resolved executable identity, schema, remote name and canonical arguments to the broker digest. The existing session/mode/backend and parent tool restrictions also apply. Explicit host configuration may grant narrowly allowlisted trusted reads; server annotations cannot. Altered configurations/schemas/arguments, missing connections, disabled servers and owner mismatches refuse dispatch.

Configurations are installation-local JSON in `~/.machina/mcp.json`, mode 0600, with advisory locks, atomic replacement and monotonic generations/revisions. This deliberately follows existing local configuration instead of adding the originally planned PostgreSQL connector table. Health and transports are worker-local. Inspection reloads configuration without launching; mutations retire stale transports. Workers on different homes have different inventories. Administrator manual changes must advance generation/revision; rollback of a loaded generation fails closed.

Authorized HTTP connections reconnect in a background task after gateway startup. This task never opens a browser, launches stdio, or asks for approval; if authorization is missing or expired, the provider stays disconnected and `/mcp` offers **Authorize**. A new worker cannot use another worker's connected process. SDK contexts are entered and retired by one owning task; callers may disconnect from other tasks. A shutdown timeout retains cleanup tracking and refuses replacement rather than pretending retirement succeeded.

Calls have a total deadline including concurrency wait. Waiting without dispatch is a known failure; a dispatched timeout/dropped transport is `outcome_unknown`, rendered explicitly in the TUI and retained in typed agent outcomes. No remote call is automatically retried. Connection retries remain bounded and transient-only. Cancellation never proves a remote effect was undone.

## Machina as an MCP server

Machina also acts as an MCP server: `python -m machina.mcp_server` serves the interop and subagent shim over stdio to hosts such as OpenCode, Codex and Claude Desktop. Register it with a Python that has Machina installed (a checkout virtualenv is easiest):

```bash
opencode mcp add machina -- /path/to/machina/.venv/bin/python -m machina.mcp_server
```

Project-scope registration writes the host's own config file (`opencode.json` for OpenCode), which this repository gitignores because the interpreter path is host-specific; other hosts use their equivalent stdio configuration. The shim exposes:

- `machina_status` — read-only health/count summary.
- `machina_chat` — one headless `machina chat` turn.
- `machina_session_create` — create an empty session.
- `machina_agents` — list the agent roles an external orchestrator can call.
- `machina_delegate` — run one child agent in a fresh durable Machina session.
- `machina_delegate_many` — run up to 20 child agents in bounded waves.

Chat is text-only; a turn that pauses for approval (CLI exit 3) returns `{"status": "paused_for_approval", ...}` with pending request ids instead of hanging; the caller resolves the request in Machina and resumes the session. Chat subprocesses run in the host's launch directory with a minimal environment allowlist, their own process group and a bounded kill-tree deadline; stdout is the reply and stderr is diagnostics-only, so child secret material never enters a tool result. Delegated children instead run in-process under Machina's configured workspace, provider, accounting and permission ceiling.

### OpenCode/Codex as orchestrator, Machina as subagent provider

This is the scalable interop direction: the external harness keeps its own planner/UI/session model, and calls Machina when it wants a durable specialist child with Machina permissions, context, and accounting.

1. Register the shim once: `machina harnesses enable opencode` (or `codex`).
2. From the external orchestrator, call `machina_agents` to discover roles.
3. Call `machina_delegate` for one child, or `machina_delegate_many` with up to 20 `{agent, task}` entries for a batch.
4. `max_concurrency` defaults to `max_delegate_concurrency` (4), so 20 tasks run in orderly waves rather than 20 simultaneous provider calls. The external orchestrator can choose a lower value or up to 20.
5. A child that needs approval returns `waiting_approval` with request ids; a human resolves it in Machina. Children inherit the MCP server's execution-mode ceiling and cannot approve their own actions.

The reverse direction (Machina as planner over OpenCode child sessions) is not exposed through MCP because OpenCode's subagent surface is its internal Task/session API and ACP/HTTP server, not an MCP server. `machina harnesses run opencode "<prompt>"` remains the one-shot bridge; a true reverse control plane would need an OpenCode server/ACP adapter rather than pretending the MCP registration is symmetric.

The `j9` gate journey proves the OpenCode → shim → Machina-session roundtrip end to end and SKIPs unless `opencode` is installed and the project has a registered `machina` server; the recorded round trip, database token check and unit + stdio-handshake coverage are in the [interop proof](verification/proofs/interop_2026-10-14/README.md).

## Scope and remaining work

Only the schema subset enforced by `machina/connectors/policy.py` is accepted: typed values, properties/required, arrays, enum/const, numeric/string/item bounds and supported combinators. Unsupported constraints or malformed schemas fail closed rather than being silently ignored.

Supported: stdio and Streamable HTTP, tools, interactive configuration/authorization/removal, durable local configuration, broker-bound native tool calls, existing exact HTTP approval continuation machinery, and OAuth browser authorization (Authorization Code + S256 PKCE) for HTTP servers with encrypted persistent credentials and refresh. The backend retains disconnect/forget primitives for lower-level callers, but the human menu exposes only **Authorize** and **Remove server**. See [MCP OAuth](MCP_OAUTH.md).

Not supported: HTTP credential-profile authentication, legacy SSE, remote resources/prompts, catalog subscriptions, server-initiated model/filesystem callbacks, automatic executable installation, shared-process connections or complete transport-level DNS-rebinding defenses. Stdio is host execution and is refused in sandbox mode. HTTP disables redirects and inherited proxies and rechecks destinations; normalized result caps do not bound every SDK receive allocation.

SDK fixture tests prove transport and harness pipeline behavior. No particular external MCP vendor or live-provider quality gain is implied. See [release evidence](verification/RELEASE_READINESS.md) and the historical [connector progress](archive/plans/MCP_CONNECTORS_PROGRESS.md).

OAuth is implemented per the [implementation handoff](archive/plans/MCP_OAUTH_IMPLEMENTATION_HANDOFF.md) with progress in [MCP OAuth progress](archive/plans/MCP_OAUTH_PROGRESS.md). Remote hosted callbacks, device-code grants, OIDC user login, all-vendor certification and total DNS-rebinding defense remain deferred.

## Remote-call consent

Untrusted remote tools require an exact action approval in ordinary modes.
A verified `trusted_read` tool allowlist can permit listed reads without
per-call approval. An explicit live `full` grant also authorizes untrusted
remote calls without further per-call approval for that session. Selecting
full mode alone does not issue authority. Remote server descriptions, OAuth
login, tool annotations, and saved connection configuration do not grant
execution consent. Changes to reviewed call material invalidate an exact
action approval; calls with uncertain outcomes are not automatically retried.
