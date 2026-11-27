"""A2A never reports an approval-paused task as completed."""

from __future__ import annotations

import uuid

import pytest

from machina.api.routes import a2a


class _Db:
    connected = True

    def __init__(self, value):
        self.value = value

    async def fetchval(self, _sql, _session_id):
        return self.value


@pytest.mark.asyncio
async def test_pending_approval_is_input_required(monkeypatch):
    monkeypatch.setattr("machina.db.connection.db", _Db(1))

    assert await a2a._pending_approval(uuid.uuid4()) is True


@pytest.mark.asyncio
async def test_no_pending_approval(monkeypatch):
    monkeypatch.setattr("machina.db.connection.db", _Db(None))

    assert await a2a._pending_approval(uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_database_error_is_treated_as_unknown_not_success(monkeypatch):
    class _Broken(_Db):
        async def fetchval(self, _sql, _session_id):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr("machina.db.connection.db", _Broken(None))

    assert await a2a._pending_approval(uuid.uuid4()) is True
