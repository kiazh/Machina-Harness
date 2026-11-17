"""Hosted shell inspection and lifecycle for the `/host` command."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from machina.gateway.errors import INVALID_PARAMS, OPERATION_FAILED, RpcError
from machina.gateway.features._common import _str
from machina.host_setup import HostSetupError, SetupOptions

if TYPE_CHECKING:
    from machina.gateway.server import Gateway


def _setuper():
    from machina import host_setup

    return host_setup


async def host_status(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw, params
    return {"status": (await asyncio.to_thread(_setuper().status)).public()}


def _options(params: dict[str, Any]) -> SetupOptions:
    try:
        port = int(params.get("port") or 7681)
    except (TypeError, ValueError):
        raise RpcError(INVALID_PARAMS, "port must be an integer") from None
    if not 1 <= port <= 65535:
        raise RpcError(INVALID_PARAMS, "port must be between 1 and 65535")
    access = _str(params, "access", required=False, max_len=16) or "both"
    if access not in {"both", "browser", "ssh"}:
        raise RpcError(INVALID_PARAMS, "access must be both, browser, or ssh")
    return SetupOptions(
        workspace=_str(params, "workspace", required=False, max_len=4096),
        mode=_str(params, "mode", required=False, max_len=32) or "ask",
        access=access,
        client=_str(params, "client", required=False, max_len=32) or "macos",
        public_key=_str(params, "public_key", required=False, max_len=4096),
        port=port,
        session=_str(params, "session", required=False, max_len=64) or "machina",
        allow_full=params.get("allow_full") is True,
        machina_session=_str(params, "machina_session", required=False, max_len=64),
    )


async def host_plan(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw
    options = _options(params)
    return await asyncio.to_thread(_setuper().plan_setup, options)


async def host_setup_start(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw
    try:
        operation_id = await asyncio.to_thread(
            _setuper().start_setup, _options(params)
        )
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None
    return {"operationId": operation_id}


async def host_setup_status(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw
    operation_id = _str(params, "operationId", max_len=64)
    try:
        return await asyncio.to_thread(_setuper().setup_status, operation_id)
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None


async def host_setup_cancel(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw
    operation_id = _str(params, "operationId", max_len=64)
    try:
        return await asyncio.to_thread(_setuper().cancel_setup, operation_id)
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None


async def host_setup_resume(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw, params
    try:
        operation_id = await asyncio.to_thread(_setuper().resume_setup)
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None
    return {"operationId": operation_id}


async def host_add_key(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw
    public_key = _str(params, "publicKey", max_len=4096)
    try:
        status = await asyncio.to_thread(_setuper().add_key, public_key)
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None
    return {"status": status.public()}


async def host_open_url(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw
    url = _str(params, "url", max_len=4096)
    opened = await asyncio.to_thread(_setuper().open_url, url)
    return {"opened": opened}


async def host_setup(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw
    workspace = _str(params, "workspace", required=False, max_len=4096)
    mode = _str(params, "mode", required=False, max_len=32) or "ask"
    try:
        status = await asyncio.to_thread(_setuper().setup, workspace=workspace, mode=mode)
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None
    return {"status": status.public()}


async def host_start(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw, params
    try:
        status = await asyncio.to_thread(_setuper().start)
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None
    return {"status": status.public()}


async def host_stop(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw, params
    try:
        status = await asyncio.to_thread(_setuper().stop)
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None
    return {"status": status.public()}


async def host_restart(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw, params
    try:
        status = await asyncio.to_thread(_setuper().restart)
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None
    return {"status": status.public()}


async def host_credentials(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw, params
    try:
        return await asyncio.to_thread(_setuper().credentials)
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None


async def host_instructions(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    del gw
    client = _str(params, "client", max_len=32)
    access = _str(params, "access", required=False, max_len=16) or "both"
    try:
        text = await asyncio.to_thread(_setuper().client_instructions, client, access)
    except HostSetupError as error:
        raise RpcError(OPERATION_FAILED, str(error)) from None
    return {"text": text}
