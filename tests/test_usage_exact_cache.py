"""A free exact-cache hit records zero accounted tokens, not the reservation."""

from __future__ import annotations

import uuid

import pytest

from machina.core import usage as usage_module


class _Db:
    connected = True

    def __init__(self):
        self.args = None

    async def execute(self, _sql, *args):
        self.args = args
        return "UPDATE 1"

    async def fetchval(self, _sql, _arg):
        return "openrouter"


@pytest.mark.asyncio
async def test_exact_cache_hit_accounts_zero(monkeypatch):
    fake = _Db()
    monkeypatch.setattr(usage_module, "db", fake)

    await usage_module.usage_store.finish(
        uuid.uuid4(),
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        model="free-model",
        cache_source="exact",
    )

    # accounted_tokens is the sixth positional parameter in the UPDATE.
    assert fake.args[4] == 0


@pytest.mark.asyncio
async def test_missing_usage_still_charges_the_reservation(monkeypatch):
    fake = _Db()
    monkeypatch.setattr(usage_module, "db", fake)

    await usage_module.usage_store.finish(uuid.uuid4(), {}, model="free-model")

    assert fake.args[4] is None
