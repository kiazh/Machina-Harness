"""Built-in connector starters remain explicit, non-secret setup choices."""

import sys
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
    assert {entry["id"] for entry in entries} >= {
        "gmail",
        "google-drive",
        "google-calendar",
        "github",
        "firecrawl-selfhosted",
        "custom-mcp",
        "machina",
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
        config = ConnectorStore.validate_input(fields)
        assert config.id == entry["id"]
        assert config.trust == "untrusted"
        assert "owner" not in fields and "revision" not in fields
        assert not config.command and not config.argv  # No package-manager auto-install.
        assert not config.credential_profile and not config.secret_refs
        if config.oauth:
            assert not config.oauth.get("client_secret")
            assert not config.oauth.get("access_token")
            assert not config.oauth.get("refresh_token")


@pytest.mark.asyncio
async def test_firecrawl_starter_targets_an_editable_local_mcp_bridge():
    result = await METHODS["mcp.catalog"](object(), {})
    entry = next(item for item in result["entries"] if item["id"] == "firecrawl-selfhosted")
    config = ConnectorStore.validate_input(entry["config"])
    assert config.transport == "streamable_http"
    assert config.endpoint == "http://127.0.0.1:3000/mcp"
    assert config.local_access is True
    assert config.oauth is None
    assert entry["setup"]["type"] == "local_endpoint"
    # A different self-hosted bridge URL must still use the regular config validator.
    edited = {**entry["config"], "endpoint": "http://localhost:4300/mcp"}
    assert ConnectorStore.validate_input(edited).endpoint == "http://localhost:4300/mcp"


@pytest.mark.asyncio
async def test_google_starters_use_official_remote_endpoints_and_read_scopes():
    result = await METHODS["mcp.catalog"](object(), {})
    entries = {entry["id"]: entry for entry in result["entries"]}
    for connector_id, endpoint, scope in [
        ("gmail", "https://gmailmcp.googleapis.com/mcp/v1", "gmail.readonly"),
        ("google-drive", "https://drivemcp.googleapis.com/mcp/v1", "drive.readonly"),
        (
            "google-calendar",
            "https://calendarmcp.googleapis.com/mcp/v1",
            "calendar.events.readonly",
        ),
    ]:
        entry = entries[connector_id]
        config = ConnectorStore.validate_input(entry["config"])
        assert config.endpoint == endpoint
        assert config.local_access is False
        assert entry["setup"]["type"] == "oauth"
        assert f"https://www.googleapis.com/auth/{scope}" in config.oauth["scope_allowlist"]


@pytest.mark.asyncio
async def test_machina_is_an_outgoing_harness_registration_and_has_no_connector_config():
    result = await METHODS["mcp.catalog"](object(), {})
    entry = next(item for item in result["entries"] if item["id"] == "machina")
    assert entry["kind"] == "harness"
    assert entry["config"] is None
    assert entry["setup"]["type"] == "harness"
    assert entry["runtime"] == {"command": sys.executable, "argv": ["-m", "machina.mcp_server"]}


@pytest.mark.asyncio
async def test_editing_one_starter_does_not_mutate_later_catalog_requests():
    first = await METHODS["mcp.catalog"](object(), {})
    first["entries"][0]["config"]["oauth"]["scope_allowlist"].append("extra")
    second = await METHODS["mcp.catalog"](object(), {})
    assert "extra" not in second["entries"][0]["config"]["oauth"]["scope_allowlist"]
