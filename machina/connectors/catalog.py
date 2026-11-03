"""Non-secret setup starters, independent of installed or connected inventory.

Listing these entries never contacts a provider, installs a package, starts a
process, or saves configuration. The regular configure/preview/authorization
flows still apply after a human reviews and edits a starter.
"""

from __future__ import annotations

from typing import Any

from machina.connectors.store import ConnectorStore
from machina.interop import server_command

_GOOGLE_DOCS = "https://developers.google.com/workspace/guides/configure-mcp-servers"


def _google_entry(
    connector_id: str, display_name: str, service: str, description: str, scopes: list[str]
) -> dict[str, Any]:
    return {
        "id": connector_id,
        "displayName": display_name,
        "kind": "connector",
        "category": "Google Workspace",
        "provider": "Google",
        "description": description,
        "docsUrl": _GOOGLE_DOCS,
        "setup": {
            "type": "oauth",
            "instructions": [
                "Requires Google Workspace Developer Preview access and a Google Cloud "
                "project with the product API and its MCP service enabled.",
                "Create a Google OAuth client for an AI-powered agent. Configure its "
                "registered callback, then select preregistered OAuth and supply its "
                "client ID and secret reference before authorizing. Store the client "
                "secret in Secrets.",
                "Review the requested read scopes, authorize with Google, then preview "
                "and connect the server. Saving this starter does not sign in.",
            ],
        },
        "config": {
            "id": connector_id,
            "display_name": display_name,
            "transport": "streamable_http",
            "endpoint": f"https://{service}.googleapis.com/mcp/v1",
            "oauth": {
                "client_mode": "auto",
                "scope_allowlist": [f"https://www.googleapis.com/auth/{scope}" for scope in scopes],
            },
        },
    }


def connector_catalog() -> list[dict[str, Any]]:
    """Return fresh editable starters; availability is not connection health."""
    command, *argv = server_command()
    entries = [
        _google_entry(
            "gmail",
            "Gmail",
            "gmailmcp",
            "Search and read email through Google's official Gmail MCP server.",
            ["gmail.readonly"],
        ),
        _google_entry(
            "google-drive",
            "Google Drive",
            "drivemcp",
            "Find and read files through Google's official Drive MCP server.",
            ["drive.readonly"],
        ),
        _google_entry(
            "google-calendar",
            "Google Calendar",
            "calendarmcp",
            "Read calendars and events through Google's official Calendar MCP server.",
            ["calendar.calendarlist.readonly", "calendar.events.readonly"],
        ),
        {
            "id": "github",
            "displayName": "GitHub",
            "kind": "connector",
            "category": "Development",
            "provider": "GitHub",
            "description": "Access repositories, issues, and pull requests with GitHub's MCP server.",
            "docsUrl": "https://github.com/github/github-mcp-server",
            "setup": {
                "type": "oauth",
                "instructions": [
                    "OAuth requires a GitHub App or OAuth App registered for this MCP client. "
                    "Configure its client details in OAuth settings before authorizing.",
                    "Review the server, authorize, then preview and connect it.",
                ],
            },
            "config": {
                "id": "github",
                "display_name": "GitHub",
                "transport": "streamable_http",
                "endpoint": "https://api.githubcopilot.com/mcp/",
                "oauth": {"client_mode": "auto"},
            },
        },
        {
            "id": "firecrawl-selfhosted",
            "displayName": "Firecrawl (self-hosted)",
            "kind": "connector",
            "category": "Web research",
            "provider": "Firecrawl",
            "description": "Scrape and crawl through your own Firecrawl server and local MCP bridge.",
            "docsUrl": "https://github.com/firecrawl/firecrawl-mcp-server",
            "setup": {
                "type": "local_endpoint",
                "instructions": [
                    "Requires your self-hosted Firecrawl REST API and a separately running "
                    "official firecrawl-mcp bridge. This starter does not install or start either.",
                    "Start the bridge with HTTP_STREAMABLE_SERVER=true, HOST=127.0.0.1, "
                    "and FIRECRAWL_API_URL set to your self-hosted REST API URL. Set the "
                    "backend API URL explicitly so the bridge uses your own server.",
                    "Enter the bridge's MCP URL below (default http://127.0.0.1:3000/mcp). "
                    "This differs from the backend REST API URL. Review, save, then connect.",
                ],
            },
            "config": {
                "id": "firecrawl-selfhosted",
                "display_name": "Firecrawl (self-hosted)",
                "transport": "streamable_http",
                "endpoint": "http://127.0.0.1:3000/mcp",
                "local_access": True,
            },
        },
        {
            "id": "custom-mcp",
            "displayName": "Custom MCP server",
            "kind": "connector",
            "category": "Custom",
            "provider": "Your server",
            "description": "Connect another MCP server using its endpoint or local executable.",
            "docsUrl": "https://modelcontextprotocol.io/docs/learn/architecture",
            "setup": {
                "type": "custom",
                "instructions": [
                    "Replace the example endpoint with your server URL, or choose stdio "
                    "and enter an executable and separate arguments.",
                    "Configure any required credentials through secret references or OAuth. "
                    "Review the exact transport before connecting.",
                ],
            },
            "config": {
                "id": "custom-mcp",
                "display_name": "Custom MCP server",
                "transport": "streamable_http",
                "endpoint": "https://mcp.example.com/mcp",
            },
        },
        {
            "id": "machina",
            "displayName": "Machina MCP",
            "kind": "harness",
            "category": "Agent harnesses",
            "provider": "Machina",
            "description": "Make Machina's tools available to Codex, OpenCode, and other harnesses.",
            "docsUrl": "https://github.com/kiazh/Machina-Harness#mcp-catalog-first-tool-use",
            "setup": {
                "type": "harness",
                "instructions": [
                    "Open Agent harnesses to detect an installed harness and register "
                    "Machina's MCP server with it.",
                    "For another client, use the displayed executable and arguments in "
                    "that client's MCP configuration.",
                ],
            },
            "config": None,
            "runtime": {"command": command, "argv": argv},
        },
    ]
    for entry in entries:
        if entry["kind"] == "connector":
            ConnectorStore.validate_input(entry["config"])
    return entries
