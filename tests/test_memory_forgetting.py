"""Consolidation applies bounded forgetting.

The forgetting model and ``evict_weak_memories`` existed but had no caller,
so durable memories accumulated forever. Consolidation now invokes the
store's low-importance/cap eviction after a successful write cycle.
"""

from __future__ import annotations

import pytest

from machina.memory.consolidator import MemoryConsolidator


class _Store:
    def __init__(self):
        self.calls: list[dict] = []

    async def evict_weak_memories(self, **kwargs):
        self.calls.append(kwargs)
        return 3


@pytest.mark.asyncio
async def test_eviction_runs_with_the_configured_cap():
    store = _Store()
    consolidator = MemoryConsolidator(store=store)

    await consolidator._evict_weak_memories("harness")

    assert store.calls == [{"threshold": 0.05, "agent_id": "harness", "max_memories": 5000}]


@pytest.mark.asyncio
async def test_eviction_failure_never_breaks_consolidation(monkeypatch):
    class _Broken:
        async def evict_weak_memories(self, **_kwargs):
            raise RuntimeError("db unavailable")

    consolidator = MemoryConsolidator(store=_Broken())

    # Must not raise: forgetting is maintenance, not the write contract.
    await consolidator._evict_weak_memories("harness")


@pytest.mark.asyncio
async def test_eviction_can_be_disabled(monkeypatch):
    from machina.core import config as config_module

    store = _Store()
    consolidator = MemoryConsolidator(store=store)
    monkeypatch.setattr(
        config_module.config,
        "get",
        lambda key, default=None: False if key == "memory_forgetting_enabled" else default,
    )

    await consolidator._evict_weak_memories("harness")

    assert store.calls == []
