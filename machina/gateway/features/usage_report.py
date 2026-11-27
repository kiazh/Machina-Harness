"""Aggregated usage for the /usage dashboard.

Read-only: the report joins durable LLM accounting with local skill
telemetry. Daily buckets use the caller's IANA timezone so the heatmap
matches the user's calendar; token totals stay provider-agnostic.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from machina.core.usage import db
from machina.gateway.errors import INVALID_PARAMS, RpcError
from machina.gateway.features._common import _int, _str

MAX_MODELS = 8
MAX_SKILLS = 10


def _parse_timezone(raw: Any) -> ZoneInfo:
    name = _str({"timezone": raw}, "timezone", required=False, max_len=64) or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise RpcError(INVALID_PARAMS, f"unknown timezone: {name}") from None


def _streaks(days: set[date], today: date) -> tuple[int, int]:
    """(current, longest) consecutive active-day runs."""
    if not days:
        return 0, 0
    ordered = sorted(days)
    longest = run = 1
    for previous, current in zip(ordered, ordered[1:]):
        run = run + 1 if current - previous == timedelta(days=1) else 1
        longest = max(longest, run)
    anchor = today if today in days else today - timedelta(days=1)
    if anchor not in days:
        return 0, longest
    current_streak = 0
    cursor = anchor
    while cursor in days:
        current_streak += 1
        cursor -= timedelta(days=1)
    return current_streak, longest


def _daily_series(rows: list[dict[str, Any]], *, days: int, today: date) -> list[dict[str, Any]]:
    by_day = {
        row["day"]: int(row.get("tokens") or 0)
        for row in rows
        if isinstance(row.get("day"), date)
    }
    start = today - timedelta(days=days - 1)
    return [
        {"date": (start + timedelta(days=offset)).isoformat(), "tokens": by_day.get(start + timedelta(days=offset), 0)}
        for offset in range(days)
    ]


def _skills_report() -> dict[str, Any]:
    empty = {
        "total": 0,
        "enabled": 0,
        "neverUsed": 0,
        "explored": 0,
        "uses": 0,
        "views": 0,
        "top": [],
        "items": [],
    }
    try:
        from machina.skills.registry import SkillCurator, skill_registry

        skill_registry.load_all()
        curator = SkillCurator(skill_registry)
        health = curator.get_health_report()
        skills = skill_registry.list_skills()
        explored = sum(1 for skill in skills if skill.usage_count > 0)
        items = [
            {
                "name": skill.name,
                "uses": int(skill.usage_count),
                "views": int(skill.view_count),
                "sourceType": str(skill.source_type or ""),
                "enabled": bool(skill.enabled),
            }
            for skill in sorted(skills, key=lambda skill: (-skill.usage_count, skill.name))
        ]
        return {
            "total": int(health.get("total_skills") or 0),
            "enabled": int(health.get("enabled") or 0),
            "neverUsed": int(health.get("never_used") or 0),
            "explored": explored,
            "uses": int(health.get("total_uses") or 0),
            "views": int(health.get("total_views") or 0),
            "top": items[:MAX_SKILLS],
            "items": items,
        }
    except Exception:
        return empty


async def _totals() -> dict[str, int]:
    row = await db.fetchrow(
        "SELECT COALESCE(SUM(accounted_tokens), 0)::bigint AS lifetime_tokens, "
        "COUNT(*)::bigint AS requests, "
        "COUNT(DISTINCT session_id)::bigint AS sessions "
        "FROM llm_usage WHERE status = 'complete'"
    )
    chats = await db.fetchrow("SELECT COUNT(*)::bigint AS chats FROM sessions")
    peak = await db.fetchrow(
        "SELECT COALESCE(MAX(day_tokens), 0)::bigint AS peak_tokens FROM ("
        "SELECT SUM(accounted_tokens) AS day_tokens FROM llm_usage "
        "WHERE status = 'complete' GROUP BY date_trunc('day', created_at)"
        ") AS days"
    )
    longest = await db.fetchrow(
        "SELECT COALESCE(MAX(EXTRACT(EPOCH FROM finished - started)), 0)::bigint AS seconds FROM ("
        "SELECT MIN(created_at) AS started, MAX(completed_at) AS finished "
        "FROM llm_usage WHERE status = 'complete' AND completed_at IS NOT NULL "
        "GROUP BY session_id"
        ") AS spans"
    )
    return {
        "lifetimeTokens": int(row["lifetime_tokens"]) if row else 0,
        "requests": int(row["requests"]) if row else 0,
        "sessions": int(row["sessions"]) if row else 0,
        "chats": int(chats["chats"]) if chats else 0,
        "peakTokens": int(peak["peak_tokens"]) if peak else 0,
        "longestChatSeconds": int(longest["seconds"]) if longest else 0,
    }


async def _daily(tz: str, days: int) -> list[dict[str, Any]]:
    rows = await db.fetch(
        "SELECT (created_at AT TIME ZONE $1)::date AS day, "
        "COALESCE(SUM(accounted_tokens), 0)::bigint AS tokens "
        "FROM llm_usage WHERE status = 'complete' "
        "AND created_at >= now() - make_interval(days => $2) "
        "GROUP BY day ORDER BY day",
        tz,
        days,
    )
    return [dict(row) for row in rows]


async def _active_days(tz: str) -> list[date]:
    rows = await db.fetch(
        "SELECT DISTINCT (created_at AT TIME ZONE $1)::date AS day "
        "FROM llm_usage WHERE status = 'complete' ORDER BY day",
        tz,
    )
    return [row["day"] for row in rows if isinstance(row["day"], date)]


def _account() -> dict[str, Any] | None:
    """The signed-in subscription, when one exists (identity block only)."""
    try:
        from machina.security import opencode_go_auth

        tokens = opencode_go_auth.load_tokens()
    except Exception:
        return None
    if not tokens:
        return None
    return {
        "name": "Machina",
        "email": tokens.get("email"),
        "plan": "OpenCode Go",
    }


async def _models() -> list[dict[str, Any]]:
    rows = await db.fetch(
        "SELECT provider, model, COUNT(*)::bigint AS requests, "
        "COALESCE(SUM(accounted_tokens), 0)::bigint AS tokens "
        "FROM llm_usage WHERE status = 'complete' "
        "GROUP BY provider, model ORDER BY requests DESC, tokens DESC LIMIT $1",
        MAX_MODELS,
    )
    return [
        {
            "provider": str(row["provider"]),
            "model": str(row["model"]),
            "requests": int(row["requests"]),
            "tokens": int(row["tokens"]),
        }
        for row in rows
    ]


async def usage_report(gw: Any, params: dict[str, Any]) -> dict[str, Any]:
    gw.require_db()
    timezone = _parse_timezone(params.get("timezone"))
    days = _int(params, "days", 365, 7, 730)
    today = datetime.now(timezone).date()
    active, daily_rows, models, skills, account = await asyncio.gather(
        _active_days(str(timezone)),
        _daily(str(timezone), days),
        _models(),
        asyncio.to_thread(_skills_report),
        asyncio.to_thread(_account),
    )
    totals = await _totals()
    current, longest = _streaks(set(active), today)
    return {
        "generatedAt": datetime.now(timezone).isoformat(),
        "timezone": str(timezone),
        "days": days,
        "account": account,
        "lifetimeTokens": totals["lifetimeTokens"],
        "peakTokens": totals["peakTokens"],
        "longestChatSeconds": totals["longestChatSeconds"],
        "currentStreakDays": current,
        "longestStreakDays": longest,
        "totalChats": totals["chats"],
        "totalRequests": totals["requests"],
        "activeSessions": totals["sessions"],
        "daily": _daily_series(daily_rows, days=days, today=today),
        "models": models,
        "skills": skills,
    }
