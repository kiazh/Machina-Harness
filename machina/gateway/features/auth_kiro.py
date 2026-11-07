"""``/auth kiro`` — lifecycle for the user-installed local Kiro gateway.

Kiro's account sign-in happens in the official CLI. This surface only starts,
stops, and reports the loopback proxy that reads that login, keeping the
community-interop warning and all process management outside the model path.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from machina.core.provider import kiro_gateway
from machina.gateway.errors import INVALID_PARAMS, OPERATION_FAILED, RpcError

if TYPE_CHECKING:
    from machina.gateway.server import Gateway


async def auth_kiro_status(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Public, bounded gateway status (no credential or database contents)."""
    return {"status": await asyncio.to_thread(kiro_gateway.status)}


def _wait_seconds(params: dict[str, Any]) -> float:
    raw = params.get("wait")
    if raw is None:
        return kiro_gateway.START_TIMEOUT_SECONDS
    if isinstance(raw, bool):
        raise RpcError(INVALID_PARAMS, "wait must be a number of seconds")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise RpcError(INVALID_PARAMS, "wait must be a number of seconds") from None
    if not 0.0 <= value <= 30.0:
        raise RpcError(INVALID_PARAMS, "wait must be in range 0..30 seconds")
    return value


async def auth_kiro_start(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Start the configured gateway and wait, bounded, for its listener."""
    wait = _wait_seconds(params)
    try:
        return await asyncio.to_thread(kiro_gateway.start, wait=wait)
    except kiro_gateway.KiroGatewayError as exc:
        raise RpcError(OPERATION_FAILED, str(exc)) from None


async def auth_kiro_stop(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Stop only a gateway Machina started; external processes stay untouched."""
    try:
        return await asyncio.to_thread(kiro_gateway.stop)
    except kiro_gateway.KiroGatewayError as exc:
        raise RpcError(OPERATION_FAILED, str(exc)) from None
