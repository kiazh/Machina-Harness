"""Operational retention prunes only age-bounded tables."""

from __future__ import annotations

import pytest

from machina.core import retention


class _Db:
    connected = True

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    async def execute(self, sql: str, days: int):
        assert "DELETE FROM" in sql
        self.calls.append((sql.split()[2], days))
        return "DELETE 2"


class _Settings:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.mark.asyncio
async def test_prunes_each_configured_table(monkeypatch):
    fake = _Db()
    monkeypatch.setattr(retention, "db", fake)

    removed = await retention.prune_expired(
        config=_Settings(
            {
                "audit_retention_days": 30,
                "usage_retention_days": 90,
                "artifact_retention_days": 7,
            }
        )
    )

    assert removed == {"audit_events": 2, "llm_usage": 2, "tool_artifacts": 2}
    assert fake.calls == [
        ("audit_events", 30),
        ("llm_usage", 90),
        ("tool_artifacts", 7),
    ]


@pytest.mark.asyncio
async def test_zero_disables_the_pruner(monkeypatch):
    fake = _Db()
    monkeypatch.setattr(retention, "db", fake)

    removed = await retention.prune_expired(
        config=_Settings({"audit_retention_days": 0, "usage_retention_days": 0})
    )

    assert removed == {}
    assert fake.calls == []


@pytest.mark.asyncio
async def test_one_failure_does_not_block_others(monkeypatch):
    class _Flaky(_Db):
        async def execute(self, sql, days):
            if "audit_events" in sql:
                raise RuntimeError("locked")
            return await super().execute(sql, days)

    fake = _Flaky()
    monkeypatch.setattr(retention, "db", fake)

    removed = await retention.prune_expired(
        config=_Settings(
            {
                "audit_retention_days": 30,
                "usage_retention_days": 90,
            }
        )
    )

    assert "audit_events" not in removed
    assert removed["llm_usage"] == 2
