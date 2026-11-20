"""Built-in connector starters remain explicit, non-secret setup choices."""

from urllib.parse import urlparse

import pytest

from machina.connectors.store import ConnectorStore
from machina.gateway.features import METHODS


@pytest.mark.asyncio
async def test_catalog_is_available_without_initializing_a_runtime_or_session():
    # A catalog request must never launch, discover, persist, or request credentials.
    assert "mcp.catalog" in METHODS
    result = await METHODS["mcp.catalog"](object(), {})
    entries = result["entries"]
    assert {entry["id"] for entry in entries} == {
        "composio",
        "arcade",
        "github",
        "firecrawl",
        "vercel",
    }
    assert len({entry["id"] for entry in entries}) == len(entries)
    for entry in entries:
        assert entry["displayName"]
        assert entry["description"]
        assert entry["provider"]
        assert entry["category"]
        assert urlparse(entry["docsUrl"]).scheme == "https"
        assert entry["setup"]["instructions"]
        assert "connected" not in entry and "configured" not in entry


@pytest.mark.asyncio
async def test_connector_starters_validate_without_tokens_or_installation_authority():
    result = await METHODS["mcp.catalog"](object(), {})
    for entry in result["entries"]:
        if entry["kind"] != "connector":
            continue
        fields = entry["config"]
        if entry["setup"]["type"] == "gateway_url":
            # The gateway is account-specific: the UI asks for it before saving.
            assert fields["id"] == entry["id"]
            assert "endpoint" not in fields
            continue
        config = ConnectorStore.validate_input(fields)
        assert config.id == entry["id"]
        assert config.trust == "untrusted"
        assert "owner" not in fields and "revision" not in fields
        assert not config.command and not config.argv  # No package-manager auto-install.
        assert not config.credential_profile and not config.secret_refs
        assert all(
            value and not value.startswith(("ghp_", "github_pat_"))
            for value in config.header_refs.values()
        )
        if config.oauth:
            assert not config.oauth.get("client_secret")
            assert not config.oauth.get("access_token")
            assert not config.oauth.get("refresh_token")


@pytest.mark.asyncio
async def test_firecrawl_starter_uses_the_hosted_oauth_endpoint():
    result = await METHODS["mcp.catalog"](object(), {})
    entry = next(item for item in result["entries"] if item["id"] == "firecrawl")
    config = ConnectorStore.validate_input(entry["config"])
    assert config.transport == "streamable_http"
    assert config.endpoint == "https://mcp.firecrawl.dev/v2/mcp-oauth"
    assert config.oauth["client_mode"] == "auto"
    assert config.local_access is False
    assert entry["setup"]["type"] == "oauth"
    assert any("dynamic client registration" in line for line in entry["setup"]["instructions"])


@pytest.mark.asyncio
async def test_arcade_starter_asks_for_the_users_own_gateway_url():
    result = await METHODS["mcp.catalog"](object(), {})
    entry = next(item for item in result["entries"] if item["id"] == "arcade")
    assert entry["setup"]["type"] == "gateway_url"
    assert "gateway URL" in entry["setup"]["prompt"]
    assert entry["setup"]["example"].startswith("https://api.arcade.dev/mcp/")
    assert entry["config"]["id"] == "arcade"
    assert "endpoint" not in entry["config"]
    assert any("2,000" in line for line in entry["setup"]["instructions"])


@pytest.mark.asyncio
async def test_composio_starter_uses_the_managed_mcp_oauth_endpoint():
    result = await METHODS["mcp.catalog"](object(), {})
    entry = next(item for item in result["entries"] if item["id"] == "composio")
    config = ConnectorStore.validate_input(entry["config"])
    assert config.endpoint == "https://connect.composio.dev/mcp"
    assert config.transport == "streamable_http"
    assert config.oauth["client_mode"] == "auto"
    assert config.oauth.get("client_secret") is None
    assert entry["setup"]["type"] == "oauth"
    assert any("third-party" in line.lower() for line in entry["setup"]["instructions"])


@pytest.mark.asyncio
async def test_github_starter_uses_a_pat_header_reference_and_masked_setup():
    result = await METHODS["mcp.catalog"](object(), {})
    entry = next(item for item in result["entries"] if item["id"] == "github")
    config = ConnectorStore.validate_input(entry["config"])
    assert config.endpoint == "https://api.githubcopilot.com/mcp/"
    assert config.oauth is None
    assert config.header_refs == {"Authorization": "GITHUB_MCP_PAT"}
    assert entry["setup"]["type"] == "secret"
    assert entry["setup"]["secretKey"] == "GITHUB_MCP_PAT"
    assert "personal access token" in entry["setup"]["prompt"].lower()


@pytest.mark.asyncio
async def test_vercel_starter_uses_the_official_oauth_endpoint():
    result = await METHODS["mcp.catalog"](object(), {})
    entry = next(item for item in result["entries"] if item["id"] == "vercel")
    config = ConnectorStore.validate_input(entry["config"])
    assert config.endpoint == "https://mcp.vercel.com"
    assert config.oauth["client_mode"] == "auto"
    assert entry["setup"]["type"] == "oauth"


@pytest.mark.asyncio
async def test_editing_one_starter_does_not_mutate_later_catalog_requests():
    first = await METHODS["mcp.catalog"](object(), {})
    first["entries"][0]["config"]["display_name"] = "changed"
    second = await METHODS["mcp.catalog"](object(), {})
    assert second["entries"][0]["config"]["display_name"] != "changed"
