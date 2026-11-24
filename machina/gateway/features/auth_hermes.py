"""``/auth hermes`` — one-click setup and lifecycle for the Hermes proxy.

Hermes owns its OAuth login and its proxy process. This surface detects the
CLI, installs it from the official installer when missing, opens the sign-in
handoff, and starts/stops only the proxy Machina launched.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from machina.gateway.errors import INVALID_PARAMS, OPERATION_FAILED, RpcError

if TYPE_CHECKING:
    from machina.gateway.server import Gateway


async def _status() -> dict[str, Any]:
    from machina.core import hermes_proxy

    return await asyncio.to_thread(hermes_proxy.status)


async def auth_hermes_status(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Public, bounded status (never returns credentials)."""
    return {"status": await _status()}


async def auth_hermes_start(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Start the proxy when Hermes and an upstream login are ready."""
    from machina.core import hermes_proxy

    try:
        ok, message = await asyncio.to_thread(hermes_proxy.start, wait=True)
    except Exception as exc:
        raise RpcError(OPERATION_FAILED, str(exc) or type(exc).__name__) from None
    return {"started": ok, "message": message, "status": await _status()}


async def auth_hermes_stop(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Stop only the proxy Machina started; external proxies stay untouched."""
    from machina.core import hermes_proxy

    try:
        stopped, message = await asyncio.to_thread(hermes_proxy.stop)
    except Exception as exc:
        raise RpcError(OPERATION_FAILED, str(exc) or type(exc).__name__) from None
    return {"stopped": stopped, "message": message, "status": await _status()}


async def auth_hermes_setup(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """One-click Setup: install if missing, then hand off the OAuth consent.

    Returns immediately when an install must run in the background; the
    sign-in window opens automatically and the proxy starts when the
    upstream reports ready.
    """
    provider = str(params.get("provider") or "auto").strip().lower()
    if provider not in {"auto", "nous", "xai"}:
        raise RpcError(INVALID_PARAMS, "provider must be auto|nous|xai")
    from machina.core import hermes_proxy

    try:
        ok, message = await asyncio.to_thread(hermes_proxy.setup, provider, interactive=False)
    except Exception as exc:
        raise RpcError(OPERATION_FAILED, str(exc) or type(exc).__name__) from None
    return {"started": ok, "message": message, "status": await _status()}
