"""Typed ``Gateway.call`` seam shared by the HTTP API."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from machina.api.routes import rpc as rpc_routes
from machina.core.runtime import RuntimeServices
from machina.gateway import server as gateway_server
from machina.gateway.errors import (
    DATABASE_UNAVAILABLE,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    NOT_FOUND,
    TURN_IN_PROGRESS,
    RpcError,
)
from machina.gateway.server import Gateway


@pytest_asyncio.fixture
async def gateway() -> Gateway:
    frames: list[dict[str, Any]] = []
    gw = Gateway(
        frames.append,
        owns_db=True,
        runtime=RuntimeServices(),
        inherit_env_token=False,
    )
    gw.db_ready = True
    gw.test_frames = frames
    try:
        yield gw
    finally:
        gw.db_ready = False
        await gw.close(timeout=0.2)


@pytest.mark.asyncio
async def test_call_returns_real_result_without_synthesizing_a_frame(gateway: Gateway) -> None:
    async def echo(params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params["value"]}

    async def empty(params: dict[str, Any]) -> dict[str, Any]:
        return {"params": params}

    gateway.add_method("test.echo", echo)
    gateway.add_method("test.empty", empty)

    assert await gateway.call("test.echo", {"value": 7}) == {"echo": 7}
    assert await gateway.call("test.empty") == {"params": {}}
    assert gateway.test_frames == []


@pytest.mark.asyncio
async def test_call_propagates_typed_rpc_error(gateway: Gateway) -> None:
    async def busy(_params: dict[str, Any]) -> dict[str, Any]:
        raise RpcError(TURN_IN_PROGRESS, "a turn is already running for this session")

    gateway.add_method("test.busy", busy)

    with pytest.raises(RpcError) as caught:
        await gateway.call("test.busy", {})

    assert caught.value.code == TURN_IN_PROGRESS
    assert caught.value.message == "a turn is already running for this session"
    assert gateway.test_frames == []


@pytest.mark.asyncio
async def test_call_converts_unexpected_errors_to_sanitized_internal_error(
    gateway: Gateway,
) -> None:
    async def broken(_params: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("provider payload must stay in the log")

    gateway.add_method("test.broken", broken)

    with pytest.raises(RpcError) as caught:
        await gateway.call("test.broken", {})

    assert caught.value.code == INTERNAL_ERROR
    assert caught.value.message == "internal error"
    assert isinstance(caught.value.__cause__, ValueError)
    assert gateway.test_frames == []


@pytest.mark.asyncio
async def test_call_propagates_cancellation(gateway: Gateway) -> None:
    started = asyncio.Event()

    async def slow(_params: dict[str, Any]) -> dict[str, Any]:
        started.set()
        await asyncio.sleep(10)
        return {}

    gateway.add_method("test.slow", slow)
    task = asyncio.create_task(gateway.call("test.slow"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_call_rejects_unknown_method_and_non_object_params(gateway: Gateway) -> None:
    with pytest.raises(RpcError) as missing:
        await gateway.call("test.missing")
    assert missing.value.code == METHOD_NOT_FOUND

    with pytest.raises(RpcError) as invalid:
        await gateway.call("test.echo", ["not", "an", "object"])  # type: ignore[arg-type]
    assert invalid.value.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_db_readiness_uses_public_accessor() -> None:
    frames: list[dict[str, Any]] = []
    gw = Gateway(
        frames.append,
        owns_db=True,
        runtime=RuntimeServices(),
        inherit_env_token=False,
    )

    async def needs_db(_params: dict[str, Any]) -> dict[str, Any]:
        gw.require_db()
        return {"ready": True}

    gw.add_method("test.db", needs_db)
    assert gw.db_ready is False

    try:
        with pytest.raises(RpcError) as unavailable:
            await gw.call("test.db")
        assert unavailable.value.code == DATABASE_UNAVAILABLE

        gw.db_ready = True
        assert gw.db_ready is True
        assert await gw.call("test.db") == {"ready": True}
    finally:
        gw.db_ready = False
        await gw.close(timeout=0.2)


@pytest.mark.asyncio
async def test_explicit_readiness_wins_over_borrowed_pool_refresh(monkeypatch) -> None:
    class _BorrowedDb:
        connected = False

    monkeypatch.setattr(gateway_server, "db", _BorrowedDb())
    gw = Gateway(
        lambda _frame: None,
        owns_db=False,
        runtime=RuntimeServices(),
        inherit_env_token=False,
    )

    async def needs_db(_params: dict[str, Any]) -> dict[str, Any]:
        gw.require_db()
        return {"ready": True}

    gw.add_method("test.db", needs_db)
    gw.db_ready = True
    try:
        # The explicit public write is not a private-state poke; a caller that
        # owns lifecycle readiness can mark the gateway ready without a pool.
        assert await gw.call("test.db") == {"ready": True}
    finally:
        await gw.close(timeout=0.2)


@pytest.mark.asyncio
async def test_embedded_gateway_refreshes_borrowed_db_readiness(monkeypatch) -> None:
    class _BorrowedDb:
        connected = False

    borrowed = _BorrowedDb()
    monkeypatch.setattr(gateway_server, "db", borrowed)
    gw = Gateway(
        lambda _frame: None,
        owns_db=False,
        runtime=RuntimeServices(),
        inherit_env_token=False,
    )

    async def needs_db(_params: dict[str, Any]) -> dict[str, Any]:
        gw.require_db()
        return {"ready": True}

    gw.add_method("test.db", needs_db)
    try:
        with pytest.raises(RpcError) as unavailable:
            await gw.call("test.db")
        assert unavailable.value.code == DATABASE_UNAVAILABLE

        borrowed.connected = True
        assert await gw.call("test.db") == {"ready": True}
        assert gw.db_ready is True
    finally:
        await gw.close(timeout=0.2)


class _RouteGateway:
    """Typed fake: the route must call this seam, never handle_message."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        call_params = params or {}
        self.calls.append((method, call_params))
        if method == "test.missing":
            raise RpcError(NOT_FOUND, "missing")
        return {"method": method, "params": call_params}


@pytest.fixture
def route_client(monkeypatch) -> tuple[TestClient, _RouteGateway]:
    monkeypatch.setattr("machina.api.auth.configured_key", lambda: "test-key")
    app = FastAPI()
    app.router.routes.extend(rpc_routes.router.routes)
    fake = _RouteGateway()
    app.state.gateway = fake
    return TestClient(app), fake


def test_rpc_route_dispatches_through_typed_call(route_client) -> None:
    client, gateway = route_client
    response = client.post(
        "/rpc",
        json={"method": "test.echo", "params": {"value": 3}},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    assert response.json() == {"result": {"method": "test.echo", "params": {"value": 3}}}
    assert gateway.calls == [("test.echo", {"value": 3})]


def test_rpc_route_preserves_typed_error_mapping(route_client) -> None:
    client, gateway = route_client
    response = client.post(
        "/rpc",
        json={"method": "test.missing", "params": {}},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "missing"}
    assert gateway.calls == [("test.missing", {})]
