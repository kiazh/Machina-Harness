from __future__ import annotations

from datetime import date, timedelta

import importlib

import pytest

from machina.gateway.errors import RpcError

report = importlib.import_module("machina.gateway.features.usage_report")


class _Gateway:
    def require_db(self) -> None:
        pass


def test_streaks_count_consecutive_days() -> None:
    today = date(2026, 11, 27)
    days = {today, today - timedelta(days=1), today - timedelta(days=4)}

    current, longest = report._streaks(days, today)

    assert current == 2
    assert longest == 2
    assert report._streaks(set(), today) == (0, 0)


def test_streaks_keep_yesterdays_run_when_today_is_idle() -> None:
    today = date(2026, 11, 27)
    days = {today - timedelta(days=1), today - timedelta(days=2)}

    assert report._streaks(days, today)[0] == 2


def test_daily_series_fills_zero_days() -> None:
    today = date(2026, 11, 27)
    rows = [{"day": today, "tokens": 42}, {"day": today - timedelta(days=2), "tokens": 7}]

    series = report._daily_series(rows, days=4, today=today)

    assert [row["date"] for row in series] == [
        "2026-11-24",
        "2026-11-25",
        "2026-11-26",
        "2026-11-27",
    ]
    assert [row["tokens"] for row in series] == [0, 7, 0, 42]


@pytest.mark.asyncio
async def test_usage_report_assembles_the_wire_payload(monkeypatch) -> None:
    today = date.today()

    async def active(_tz: str) -> list[date]:
        return [today, today - timedelta(days=1)]

    async def daily(_tz: str, days: int) -> list[dict]:
        return [{"day": today, "tokens": 500}, {"day": today - timedelta(days=1), "tokens": 250}]

    async def totals() -> dict[str, int]:
        return {
            "lifetimeTokens": 750,
            "requests": 12,
            "sessions": 3,
            "chats": 5,
            "peakTokens": 500,
            "longestChatSeconds": 3_600,
        }

    async def models() -> list[dict]:
        return [{"provider": "opencode-go", "model": "deepseek-v4.1-flash", "requests": 12, "tokens": 750}]

    def skills() -> dict:
        return {
            "total": 4,
            "enabled": 3,
            "neverUsed": 2,
            "explored": 2,
            "uses": 9,
            "views": 4,
            "top": [{"name": "review", "uses": 6, "views": 2, "sourceType": "user", "enabled": True}],
        }

    monkeypatch.setattr(report, "_active_days", active)
    monkeypatch.setattr(report, "_daily", daily)
    monkeypatch.setattr(report, "_totals", totals)
    monkeypatch.setattr(report, "_models", models)
    monkeypatch.setattr(report, "_skills_report", skills)
    monkeypatch.setattr(
        report, "_account", lambda: {"name": "Machina", "email": "user@example.test", "plan": "OpenCode Go"}
    )

    payload = await report.usage_report(_Gateway(), {"timezone": "UTC", "days": 7})

    assert payload["account"] == {
        "name": "Machina",
        "email": "user@example.test",
        "plan": "OpenCode Go",
    }

    assert payload["lifetimeTokens"] == 750
    assert payload["totalChats"] == 5
    assert payload["currentStreakDays"] == 2
    assert payload["longestChatSeconds"] == 3_600
    assert len(payload["daily"]) == 7
    assert payload["daily"][-1] == {"date": today.isoformat(), "tokens": 500}
    assert payload["models"][0]["model"] == "deepseek-v4.1-flash"
    assert payload["skills"]["uses"] == 9


@pytest.mark.asyncio
async def test_usage_report_rejects_an_unknown_timezone() -> None:
    with pytest.raises(RpcError) as error:
        await report.usage_report(_Gateway(), {"timezone": "Not/A_Zone"})

    assert error.value.code == -32602
