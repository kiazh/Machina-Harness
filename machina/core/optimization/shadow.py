"""Bounded optimization telemetry.

Only hashes, counts, decisions, and reason codes are persisted. Prompt text,
response text, credentials, and previews never enter this table. Telemetry is
best-effort: a logging/database failure must never break a provider call.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from machina.core.config import config
from machina.core.metrics import metrics
from machina.core.optimization.policy import breakers, config_mode
from machina.db.lazy import LazyDb

__all__ = [
    "ShadowEvent",
    "drain_shadow_tasks",
    "promotion_report",
    "prune_shadow_events",
    "record_shadow",
    "schedule_shadow",
]

logger = logging.getLogger(__name__)

_MAX_REASON_LENGTH = 160
_MAX_HASH_LENGTH = 128
_SHADOW_TASKS: set[asyncio.Task] = set()


@dataclass(frozen=True)
class ShadowEvent:
    subsystem: str
    decision: str
    reason_codes: tuple[str, ...] = ()
    mode: str = ""
    session_id: uuid.UUID | None = None
    agent_id: str = ""
    provider: str = ""
    model: str = ""
    task_class: str = ""
    request_hash: str = ""
    tokens_before: int = 0
    tokens_after: int = 0
    similarity: float | None = None
    latency_ms: float | None = None


def _bounded(value: str, limit: int) -> str:
    return str(value or "")[:limit]


async def record_shadow(event: ShadowEvent, *, database: Any | None = None) -> None:
    """Record one decision; never raise into the request path."""
    try:
        metrics.increment_counter(f"optimization.{event.subsystem}.{event.decision}")
    except Exception:
        pass

    db = database if database is not None else LazyDb()
    try:
        if not db.connected:
            return
    except Exception:
        return
    try:
        await db.execute(
            """
            INSERT INTO optimization_shadow_events (
                subsystem, mode, decision, reason_codes, session_id, agent_id,
                provider, model, task_class, request_hash, tokens_before,
                tokens_after, similarity, latency_ms
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            """,
            _bounded(event.subsystem, 64),
            _bounded(event.mode or config_mode(event.subsystem), 16),
            _bounded(event.decision, 64),
            [_bounded(code, _MAX_REASON_LENGTH) for code in event.reason_codes],
            event.session_id,
            _bounded(event.agent_id, 128),
            _bounded(event.provider, 64),
            _bounded(event.model, 128),
            _bounded(event.task_class, 64),
            _bounded(event.request_hash, _MAX_HASH_LENGTH),
            max(0, int(event.tokens_before)),
            max(0, int(event.tokens_after)),
            event.similarity,
            event.latency_ms,
        )
    except Exception as exc:
        # One bounded line; never include prompt/response content.
        logger.debug("optimization shadow event dropped (%s)", type(exc).__name__)
        breakers().record_failure(f"shadow:{event.subsystem}")


def schedule_shadow(event: ShadowEvent) -> None:
    """Queue telemetry without adding database latency to the request path."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        task = loop.create_task(record_shadow(event))
        _SHADOW_TASKS.add(task)
        task.add_done_callback(_SHADOW_TASKS.discard)
    except Exception:
        pass


async def drain_shadow_tasks(timeout: float = 2.0) -> int:
    """Settle queued telemetry before the database/event loop closes."""
    tasks = [task for task in _SHADOW_TASKS if not task.done()]
    if not tasks:
        return 0
    try:
        await asyncio.wait(tasks, timeout=max(0.0, timeout))
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        pending = [task for task in tasks if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    return len(tasks)


async def promotion_report(
    *, days: int = 7, database: Any | None = None
) -> dict[str, dict[str, Any]]:
    """Aggregate decisions for the read-only status surface.

    The query returns counts only. No prompt or response text exists in the
    table, so this report cannot leak content.
    """
    db = database if database is not None else LazyDb()
    try:
        if not db.connected:
            return {}
    except Exception:
        return {}
    days = max(1, min(int(days), 365))
    try:
        rows = await db.fetch(
            """
            SELECT subsystem, decision, COUNT(*) AS events,
                   COALESCE(SUM(tokens_before), 0) AS tokens_before,
                   COALESCE(SUM(tokens_after), 0) AS tokens_after,
                   AVG(similarity) AS avg_similarity,
                   AVG(latency_ms) AS avg_latency_ms
            FROM optimization_shadow_events
            WHERE created_at > now() - make_interval(days => $1)
            GROUP BY subsystem, decision
            ORDER BY subsystem, decision
            """,
            days,
        )
    except Exception:
        return {}
    report: dict[str, dict[str, Any]] = {}
    for row in rows:
        subsystem = str(row["subsystem"])
        bucket = report.setdefault(subsystem, {"decisions": {}, "mode": config_mode(subsystem)})
        bucket["decisions"][str(row["decision"])] = {
            "events": int(row["events"]),
            "tokensBefore": int(row["tokens_before"]),
            "tokensAfter": int(row["tokens_after"]),
            "avgSimilarity": float(row["avg_similarity"]) if row["avg_similarity"] else None,
            "avgLatencyMs": float(row["avg_latency_ms"]) if row["avg_latency_ms"] else None,
        }
    for subsystem, bucket in report.items():
        bucket["mode"] = config_mode(subsystem)
    return report


async def prune_shadow_events(*, days: int | None = None, database: Any | None = None) -> int:
    """Delete telemetry older than the configured retention window."""
    db = database if database is not None else LazyDb()
    try:
        if not db.connected:
            return 0
    except Exception:
        return 0
    if days is None:
        try:
            days = int(config.get("optimization_shadow_retention_days"))
        except (TypeError, ValueError):
            days = 30
    if days <= 0:
        return 0
    try:
        result = await db.execute(
            "DELETE FROM optimization_shadow_events "
            "WHERE created_at < now() - make_interval(days => $1)",
            int(days),
        )
    except Exception:
        return 0
    try:
        return int(str(result).split()[-1])
    except (TypeError, ValueError, IndexError):
        return 0
