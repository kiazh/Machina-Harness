"""Approval decisions recorded by another process wake the owning gateway.

The in-process future only covers a resolve handled by the same gateway. The
durable row is the cross-process channel; with no approval timeout the turn
must keep polling rather than park forever.
"""

from __future__ import annotations

import asyncio

import pytest

from machina.gateway import server as gateway_server
from machina.permissions import store as approval_store


@pytest.mark.asyncio
async def test_local_future_wins():
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()
    fut.set_result("approved")

    verdict = await gateway_server._await_approval_decision(fut, "req-1", poll_interval=0.01)

    assert verdict == "approved"


@pytest.mark.asyncio
async def test_durable_decision_from_another_process_is_observed(monkeypatch):
    calls = {"count": 0}

    async def _get(_request_id):
        calls["count"] += 1
        return {"status": "pending"} if calls["count"] == 1 else {"status": "approved"}

    monkeypatch.setattr(approval_store, "get_approval", _get)
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()

    verdict = await gateway_server._await_approval_decision(fut, "req-2", poll_interval=0.01)

    assert verdict == "approved"
    assert calls["count"] >= 2


@pytest.mark.asyncio
async def test_database_error_does_not_deny(monkeypatch):
    async def _broken(_request_id):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(approval_store, "get_approval", _broken)
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()
    waiter = asyncio.create_task(
        gateway_server._await_approval_decision(fut, "req-3", poll_interval=0.01)
    )
    await asyncio.sleep(0.03)
    fut.set_result("denied")

    assert await waiter == "denied"
