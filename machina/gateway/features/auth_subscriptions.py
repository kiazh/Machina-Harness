"""Subscription-provider sign-in control plane for the terminal UI.

The ChatGPT flow lives in :mod:`auth_chatgpt`; this module exposes the same
credential-free status/login/logout surface for Grok (xAI device flow) and
GitHub Copilot (GitHub device flow). Tokens never cross the gateway wire.

A login RPC returns the public device metadata immediately. The approval is
polled in one background task and settles with exactly one terminal event:
``auth.<provider>.completed`` or ``auth.<provider>.failed``.
"""

from __future__ import annotations

import asyncio
import webbrowser
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from machina.gateway.errors import OPERATION_FAILED, RpcError
from machina.gateway.features._common import _bool
from machina.security import copilot_auth, grok_auth

if TYPE_CHECKING:
    from machina.gateway.server import Gateway

DEVICE_TIMEOUT_SECONDS = 300.0
_PENDING: dict[str, asyncio.Task[None]] = {}


def _emit(gw: Gateway, event_type: str, **fields: Any) -> None:
    frame = {
        "jsonrpc": "2.0",
        "method": "event",
        "params": gw.event_payload(event_type, "", "", **fields),
    }
    with suppress(Exception):
        gw._write(frame)


def _grok_status() -> dict[str, Any]:
    report = grok_auth.status()
    return {
        "signedIn": bool(report.get("signed_in")),
        "source": report.get("source"),
        "expired": bool(report.get("expired")),
        "expiresAt": report.get("expires_at"),
        "accountId": report.get("account_id"),
        "lastRefresh": report.get("last_refresh"),
    }


def _copilot_status() -> dict[str, Any]:
    report = copilot_auth.status()
    return {
        "signedIn": bool(report.get("signed_in")),
        "source": report.get("source"),
        "tokenFamily": report.get("token_family"),
        "accountId": report.get("account"),
        "lastRefresh": report.get("last_refresh"),
    }


async def auth_grok_status(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    return {"status": await asyncio.to_thread(_grok_status)}


async def auth_copilot_status(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    return {"status": await asyncio.to_thread(_copilot_status)}


async def _run_grok_login(gw: Gateway, device: dict[str, Any], no_browser: bool) -> None:
    url = str(device.get("verification_uri_complete") or device.get("verification_uri") or "")
    if url and not no_browser:
        with suppress(Exception):
            await asyncio.to_thread(webbrowser.open, url)
    try:
        tokens = await asyncio.to_thread(
            grok_auth.poll_device_login, device, timeout=DEVICE_TIMEOUT_SECONDS
        )
        expiry = tokens.get("expires_at")
        _emit(
            gw,
            "auth.grok.completed",
            account_id=tokens.get("account_id"),
            expiry=(
                float(expiry)
                if isinstance(expiry, (int, float)) and not isinstance(expiry, bool)
                else None
            ),
        )
    except grok_auth.GrokAuthError as error:
        _emit(gw, "auth.grok.failed", reason=str(error.code or "grok_auth_failed"))
    except Exception as error:  # bounded and sanitized
        _emit(gw, "auth.grok.failed", reason=type(error).__name__)
    finally:
        _PENDING.pop("grok", None)


async def auth_grok_login(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    pending = _PENDING.get("grok")
    if pending is not None and not pending.done():
        return {"pending": True}
    try:
        device = await asyncio.to_thread(grok_auth.start_device_login)
    except grok_auth.GrokAuthError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None
    except Exception as error:
        raise RpcError(OPERATION_FAILED, f"Grok sign-in failed ({type(error).__name__})") from None
    no_browser = _bool(params, "no_browser")
    url = str(device.get("verification_uri_complete") or device.get("verification_uri") or "")
    _PENDING["grok"] = asyncio.create_task(
        _run_grok_login(gw, device, no_browser), name="grok-login-flow"
    )
    return {
        "url": url,
        "userCode": str(device.get("user_code") or ""),
        "verificationUri": str(device.get("verification_uri") or ""),
        "expiresIn": float(device.get("expires_in") or DEVICE_TIMEOUT_SECONDS),
    }


async def auth_grok_logout(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    pending = _PENDING.pop("grok", None)
    if pending is not None:
        pending.cancel()
    removed = await asyncio.to_thread(grok_auth.logout)
    return {"removed": removed, "status": await asyncio.to_thread(_grok_status)}


async def _run_copilot_login(
    gw: Gateway, device_code: str, interval: float, generation: int, no_browser: bool
) -> None:
    try:
        token = await asyncio.to_thread(
            copilot_auth.device_flow_poll,
            device_code,
            interval=interval,
            timeout=DEVICE_TIMEOUT_SECONDS,
        )
        account = await asyncio.to_thread(copilot_auth.fetch_account, token)
        tokens = await asyncio.to_thread(
            copilot_auth.publish_device_token, token, account=account, generation=generation
        )
        _emit(
            gw,
            "auth.copilot.completed",
            account_id=tokens.get("account"),
            token_family=tokens.get("token_family"),
        )
    except copilot_auth.CopilotAuthError as error:
        _emit(gw, "auth.copilot.failed", reason=str(error.code or "copilot_auth_failed"))
    except Exception as error:
        _emit(gw, "auth.copilot.failed", reason=type(error).__name__)
    finally:
        _PENDING.pop("copilot", None)


async def auth_copilot_login(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    pending = _PENDING.get("copilot")
    if pending is not None and not pending.done():
        return {"pending": True}
    generation = await asyncio.to_thread(copilot_auth.credential_generation)
    try:
        payload = await asyncio.to_thread(copilot_auth.device_flow_start)
    except copilot_auth.CopilotAuthError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None
    except Exception as error:
        raise RpcError(
            OPERATION_FAILED, f"Copilot sign-in failed ({type(error).__name__})"
        ) from None
    no_browser = _bool(params, "no_browser")
    verification_uri = str(payload.get("verification_uri") or "https://github.com/login/device")
    if not no_browser:
        with suppress(Exception):
            await asyncio.to_thread(webbrowser.open, verification_uri)
    _PENDING["copilot"] = asyncio.create_task(
        _run_copilot_login(
            gw,
            str(payload.get("device_code") or ""),
            float(payload.get("interval") or copilot_auth.DEVICE_POLL_INTERVAL),
            generation,
            no_browser,
        ),
        name="copilot-login-flow",
    )
    return {
        "url": verification_uri,
        "userCode": str(payload.get("user_code") or ""),
        "verificationUri": verification_uri,
        "expiresIn": float(payload.get("expires_in") or DEVICE_TIMEOUT_SECONDS),
    }


async def auth_copilot_import_gh(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    token = await asyncio.to_thread(copilot_auth.import_from_gh_cli, fetch_login=True)
    return {"imported": token is not None, "status": await asyncio.to_thread(_copilot_status)}


async def auth_copilot_logout(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    pending = _PENDING.pop("copilot", None)
    if pending is not None:
        pending.cancel()
    removed = await asyncio.to_thread(copilot_auth.logout)
    return {"removed": removed, "status": await asyncio.to_thread(_copilot_status)}
