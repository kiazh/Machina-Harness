"""Non-secret setup starters, independent of installed or connected inventory.

Listing these entries never contacts a provider, installs a package, starts a
process, or saves configuration. The regular configure/preview/authorization
flows still apply after a human reviews and edits a starter.
"""

from __future__ import annotations

from typing import Any

from machina.connectors.store import ConnectorStore


def connector_catalog() -> list[dict[str, Any]]:
    """Return fresh editable starters; availability is not connection health."""
    entries = [
        {
            "id": "composio",
            "displayName": "Composio",
            "kind": "connector",
            "category": "Managed connectors",
            "provider": "Composio",
            "description": "Google Drive, Gmail, Calendar, GitHub and many other apps through Composio's managed OAuth MCP gateway.",
            "docsUrl": "https://docs.composio.dev/docs/composio-connect",
            "setup": {
                "type": "oauth",
                "instructions": [
                    "Composio handles provider OAuth and token refresh, so no Google Cloud "
                    "project or provider OAuth client is required for Drive, Gmail or Calendar.",
                    "Connect and authorize Composio in the browser. Then ask Machina to connect "
                    "the app you need; Composio returns its own one-time authorization link.",
                    "Composio is a third-party gateway and receives brokered access to connected "
                    "apps. Review its trust, scopes and retention policy before using sensitive data.",
                ],
            },
            "config": {
                "id": "composio",
                "display_name": "Composio",
                "transport": "streamable_http",
                "endpoint": "https://connect.composio.dev/mcp",
                "oauth": {"client_mode": "auto"},
            },
        },
        {
            # The gateway slug is account-specific. The UI asks for it at add
            # time; it must never be committed as a shared starter endpoint.
            "id": "arcade",
            "displayName": "Arcade",
            "kind": "connector",
            "category": "Managed connectors",
            "provider": "Arcade",
            "description": "Google Drive, Gmail and Calendar through your own Arcade MCP Gateway.",
            "docsUrl": "https://docs.arcade.dev/en/operate/governance/mcp-gateways",
            "setup": {
                "type": "gateway_url",
                "instructions": [
                    "Create a free Arcade account and an MCP Gateway. Add the Google "
                    "Drive, Gmail or Calendar tools you want the agent to use.",
                    "Copy the gateway URL from Arcade. It looks like "
                    "https://api.arcade.dev/mcp/gw_...",
                    "Paste it below; Machina saves it and starts the normal browser "
                    "OAuth flow. Arcade's free tier includes 2,000 tool calls per month.",
                    "Arcade is a third-party gateway. Review its trust, scopes and "
                    "retention policy before using sensitive data.",
                ],
                "prompt": "Paste your Arcade MCP gateway URL",
                "description": "Copy this from the Arcade MCP Gateway dashboard. Do not use the project API key here.",
                "example": "https://api.arcade.dev/mcp/gw_...",
            },
            "config": {
                "id": "arcade",
                "display_name": "Arcade",
                "transport": "streamable_http",
                "oauth": {"client_mode": "auto"},
            },
        },
        {
            "id": "github",
            "displayName": "GitHub",
            "kind": "connector",
            "category": "Development",
            "provider": "GitHub",
            "description": "Access repositories, issues, pull requests, Actions and code security through GitHub's official MCP server.",
            "docsUrl": "https://github.com/github/github-mcp-server",
            "setup": {
                "type": "secret",
                "instructions": [
                    "Create a GitHub personal access token with the repository access you "
                    "want the agent to have. Fine-grained tokens are recommended.",
                    "Paste it below; Machina stores it in .env as GITHUB_MCP_PAT and sends "
                    "it only to GitHub's remote MCP endpoint.",
                ],
                "prompt": "Paste a GitHub personal access token",
                "description": "The token is masked and stored in .env, never in the MCP configuration.",
                "example": "github_pat_... or ghp_...",
                "secretKey": "GITHUB_MCP_PAT",
            },
            "config": {
                "id": "github",
                "display_name": "GitHub",
                "transport": "streamable_http",
                "endpoint": "https://api.githubcopilot.com/mcp/",
                "header_refs": {"Authorization": "GITHUB_MCP_PAT"},
            },
        },
        {
            "id": "firecrawl",
            "displayName": "Firecrawl",
            "kind": "connector",
            "category": "Web research",
            "provider": "Firecrawl",
            "description": "Search, scrape, crawl, parse and research the web through Firecrawl's hosted MCP server.",
            "docsUrl": "https://docs.firecrawl.dev/mcp-server",
            "setup": {
                "type": "oauth",
                "instructions": [
                    "Connect Firecrawl in the browser. Firecrawl supports MCP OAuth with "
                    "dynamic client registration, so no API key or local bridge is required.",
                    "The hosted account endpoint exposes the full tool surface allowed by "
                    "your Firecrawl plan. Keyless access is available separately but is "
                    "limited to Search, Scrape, and Parse.",
                ],
            },
            "config": {
                "id": "firecrawl",
                "display_name": "Firecrawl",
                "transport": "streamable_http",
                "endpoint": "https://mcp.firecrawl.dev/v2/mcp-oauth",
                "oauth": {"client_mode": "auto"},
            },
        },
        {
            "id": "vercel",
            "displayName": "Vercel",
            "kind": "connector",
            "category": "Development",
            "provider": "Vercel",
            "description": "Search Vercel docs, manage projects, inspect deployments and query Web Analytics.",
            "docsUrl": "https://vercel.com/docs/agent-resources/vercel-mcp",
            "setup": {
                "type": "oauth",
                "instructions": [
                    "Connect Vercel in the browser and approve access to the projects and "
                    "analytics the agent may use.",
                    "Vercel supports standard MCP OAuth; no OAuth client setup is required.",
                ],
            },
            "config": {
                "id": "vercel",
                "display_name": "Vercel",
                "transport": "streamable_http",
                "endpoint": "https://mcp.vercel.com",
                "oauth": {"client_mode": "auto"},
            },
        },
    ]
    for entry in entries:
        if entry["kind"] == "connector" and entry["setup"]["type"] != "gateway_url":
            ConnectorStore.validate_input(entry["config"])
    return entries
