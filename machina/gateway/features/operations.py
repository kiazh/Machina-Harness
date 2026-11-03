"""Short RPC methods for gateway-owned long-running operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from machina.gateway.server import Gateway


async def operations_start(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    return await gw.operations.start(params)


async def operations_get(gw: Gateway, params: dict[str, Any]) -> dict[str, Any] | None:
    return gw.operations.get(params)


async def operations_cancel(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    return gw.operations.cancel(params)
