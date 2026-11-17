"""Agent-facing MCP onboarding derives a safe id from a pasted link."""

import json

import pytest

from machina.connectors.management import (
    _normalize_connect,
    connect_spec,
    derive_connector_name,
)
from machina.connectors.models import ConfigError


def test_pasted_links_derive_stable_connector_ids():
    assert derive_connector_name("https://mcp.example.com/mcp") == "mcp-example-com"
    assert derive_connector_name("https://www.example.com/mcp/v1") == "example-com-v1"
    assert derive_connector_name("http://127.0.0.1:3000/mcp") == "127-0-0-1"
    assert derive_connector_name("https://Example.COM/") == "example-com"
    assert derive_connector_name("https://example.com/sse") == "example-com"


def test_command_fallback_uses_the_executable_name():
    assert derive_connector_name(command="/usr/local/bin/npx") == "npx"
    assert derive_connector_name(command="C:\\Program Files\\node.exe") == "node"
    assert derive_connector_name() == "mcp"


def test_connect_normalization_accepts_a_link_without_a_name():
    fields, config, executable = _normalize_connect(
        {"url": "https://mcp.example.com/mcp", "trust": "trusted_read"}
    )
    assert fields["id"] == "mcp-example-com"
    assert config.id == "mcp-example-com"
    assert config.display_name == "mcp-example-com"
    assert config.transport == "streamable_http"
    assert config.endpoint == "https://mcp.example.com/mcp"
    assert config.trust == "trusted_read"
    assert executable == ""


def test_connect_spec_binds_the_derived_id_for_approval():
    spec = connect_spec({"url": "https://mcp.example.com/mcp"})
    content = json.loads(spec["content"])
    assert content["id"] == "mcp-example-com"
    assert content["endpoint"] == "https://mcp.example.com/mcp"
    assert spec["network"] == ["https://mcp.example.com/mcp"]


def test_connect_normalization_still_requires_exactly_one_transport():
    with pytest.raises(ConfigError):
        _normalize_connect({"url": "https://example.com/mcp", "command": "node"})
    with pytest.raises(ConfigError):
        _normalize_connect({})
