"""Read-only inspection of this harness runtime's configured MCP servers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from machina.connectors.models import ConnectionFailed
from machina.gateway.errors import NOT_FOUND, RpcError
from machina.gateway.features._common import _str

if TYPE_CHECKING:
    from machina.gateway.server import Gateway


async def mcp_list(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """List sanitized inventory and current state; never connect or discover."""
    return {"servers": await gw.runtime.get_connector_service().list()}


async def mcp_status(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Inspect one server from the same runtime inventory as mcp.list."""
    connector_id = _str(params, "id", max_len=64)
    try:
        server = await gw.runtime.get_connector_service().status(connector_id)
    except ConnectionFailed:
        raise RpcError(NOT_FOUND, f"MCP server {connector_id!r} not found") from None
    return {"server": server}


async def mcp_config(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Return editable non-secret fields without losing argv/cwd on an edit.

    The public summary intentionally omits these fields. Detailed inspection
    is bound to a conversation and its connector owner, like OAuth management.
    Environment and secret references contain names only; no secret store is read.
    """
    from dataclasses import asdict

    from machina.gateway.errors import UNAUTHORIZED

    connector_id = _str(params, "id", max_len=64)
    _str(params, "sessionId", max_len=64)
    session = await gw.get_session(params)
    service = gw.runtime.get_connector_service()
    await service.refresh()
    config = service.registry.get(connector_id)
    if config is None:
        raise RpcError(NOT_FOUND, "MCP server not found")
    if config.owner and config.owner != session.agent_id:
        raise RpcError(UNAUTHORIZED, "MCP server belongs to another agent")
    fields = asdict(config)
    fields.pop("owner", None)
    revision = fields.pop("revision", None)
    return {"config": fields, "revision": revision, "scope": "installation"}


async def mcp_configure(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Human transport administration; config cannot grant owner authority."""
    from machina.connectors.models import ConfigError
    from machina.gateway.errors import INVALID_PARAMS

    try:
        return {"server": await gw.runtime.get_connector_service().configure(params.get("config"))}
    except ConfigError as exc:
        raise RpcError(INVALID_PARAMS, str(exc)) from None


async def mcp_remove(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "removed": await gw.runtime.get_connector_service().remove(_str(params, "id", max_len=64))
    }


async def mcp_preview(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Preview an exact launch for human confirmation; never launches."""
    from machina.connectors.execution import prepare_connect

    connector_id = _str(params, "id", max_len=64)
    _str(params, "sessionId", max_len=64)
    session = await gw.get_session(params)
    return await prepare_connect(
        gw.runtime.get_connector_service(), connector_id, str(session.id), session.agent_id
    )


async def mcp_connect(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Launch only after a human confirms the exact preview digest."""
    from machina.connectors.execution import connect_authorized

    connector_id = _str(params, "id", max_len=64)
    _str(params, "sessionId", max_len=64)
    digest = _str(params, "digest", max_len=128)
    session = await gw.get_session(params)
    return {
        "server": await connect_authorized(
            gw.runtime.get_connector_service(),
            connector_id,
            str(session.id),
            session.agent_id,
            digest,
        )
    }


async def mcp_disconnect(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    service = gw.runtime.get_connector_service()
    connector_id = _str(params, "id", max_len=64)
    try:
        await service.disconnect(connector_id)
        return {"server": await service.status(connector_id)}
    except ConnectionFailed:
        raise RpcError(NOT_FOUND, f"MCP server {connector_id!r} not found") from None


async def mcp_tools(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    from machina.connectors.models import ConnectorError
    from machina.gateway.errors import OPERATION_FAILED

    try:
        return await gw.runtime.get_connector_service().tools_list(_str(params, "id", max_len=64))
    except ConnectorError:
        raise RpcError(OPERATION_FAILED, "MCP catalog unavailable; connect first") from None
