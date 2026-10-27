"""Research workers may use host-authorized read-only connector tools."""

import sys

import pytest

from machina.connectors.execution import call_spec
from machina.connectors.models import ConnectorConfig, ConnectorToolError, DiscoveredTool
from machina.connectors.service import ConnectorService
from machina.core.agent_def import BUILTIN_AGENTS
from machina.tools.agents import current_agent_id


def test_researcher_can_discover_connector_tools_without_management():
    tools = set(BUILTIN_AGENTS["researcher"].tools)
    assert {"mcp_list", "mcp_describe", "mcp_call"} <= tools
    assert not {"mcp_connect", "mcp_disconnect"} & tools


def test_researcher_only_calls_host_allowlisted_trusted_read_tools():
    service = ConnectorService()
    service.registry.add(
        ConnectorConfig(
            id="fixture",
            command=sys.executable,
            trust="trusted_read",
            tool_allowlist=["fixture__scrape"],
        )
    )
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    service.registry.set_tools(
        "fixture",
        [
            DiscoveredTool("fixture", "", "scrape", schema=schema),
            DiscoveredTool("fixture", "", "delete", schema=schema),
        ],
    )
    token = current_agent_id.set("researcher")
    try:
        assert (
            call_spec(service, {"name": "fixture__scrape", "arguments": {}})["capabilities"] == []
        )
        with pytest.raises(ConnectorToolError, match="trusted.read"):
            call_spec(service, {"name": "fixture__delete", "arguments": {}})
        service.registry.update("fixture", trust="untrusted")
        service.registry.set_tools(
            "fixture",
            [
                DiscoveredTool("fixture", "", "scrape", schema=schema),
                DiscoveredTool("fixture", "", "delete", schema=schema),
            ],
        )
        with pytest.raises(ConnectorToolError, match="trusted.read"):
            call_spec(service, {"name": "fixture__scrape", "arguments": {}})
    finally:
        current_agent_id.reset(token)
    # A different principal retains the normal reviewed execution path.
    assert call_spec(service, {"name": "fixture__delete", "arguments": {}})["capabilities"] == [
        "mcp_external_review"
    ]


@pytest.mark.asyncio
async def test_researcher_dispatch_uses_explicit_identity_and_keeps_call_bound(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from machina.connectors.execution import execute_call
    from machina.connectors.models import CallOutcome, ConnectionState
    from machina.permissions.broker import _execution_context
    from machina.permissions.policy import build_request

    service = ConnectorService()
    service.registry.add(
        ConnectorConfig(
            id="fixture", command=sys.executable, trust="trusted_read", tool_allowlist=["scrape"]
        )
    )
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    service.registry.set_tools(
        "fixture",
        [
            DiscoveredTool("fixture", "", "scrape", schema=schema),
            DiscoveredTool("fixture", "", "delete", schema=schema),
        ],
    )
    connection = SimpleNamespace(
        state=ConnectionState.CONNECTED,
        call_tool=AsyncMock(return_value=CallOutcome(ok=True, text="rendered")),
    )
    monkeypatch.setattr(service.manager, "connection", lambda _: connection)
    args = {"name": "fixture__scrape", "arguments": {}}
    request = build_request(
        **call_spec(service, args, agent_id="researcher"),
        tool="mcp_call",
        mode="full",
        session_id="session",
    )
    token = _execution_context.set(
        {"session_id": "session", "mode": "full", "backend": "host", "digest": request.digest}
    )
    try:
        result = await execute_call(service, **args, session_id="session", agent_id="researcher")
        assert result.text == "rendered"
        connection.call_tool.assert_awaited_once_with("scrape", {})
        with pytest.raises(ConnectorToolError, match="trusted.read"):
            await execute_call(
                service,
                name="fixture__delete",
                arguments={},
                session_id="session",
                agent_id="researcher",
            )
        assert connection.call_tool.await_count == 1
    finally:
        _execution_context.reset(token)
