"""Durable batch work for Maki.

A batch is a goal plus idempotent items. Workers claim items, execute them,
and report a terminal status; the batch aggregates counts and can be paused,
resumed, or cancelled. Email/browser senders plug into the same table.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from machina.db.connection import db


def _uid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def idempotency_key(payload: dict[str, Any], *, prefix: str = "") -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _batch_view(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["id"] = str(item.get("id") or "")
    if item.get("grant_id") is not None:
        item["grantId"] = str(item["grant_id"])
    item.pop("grant_id", None)
    item["createdAt"] = item.pop("created_at", None)
    item["updatedAt"] = item.pop("updated_at", None)
    return item


async def resolve_batch_id(value: str) -> str | None:
    """Accept a full UUID or the display prefix shown by the tools."""
    uid = _uid(value)
    if uid is not None:
        return str(uid)
    prefix = str(value or "").strip()
    if len(prefix) < 6:
        return None
    row = await db.fetchrow(
        "SELECT id::text AS id FROM resident_batches WHERE id::text LIKE $1 "
        "ORDER BY created_at DESC LIMIT 1",
        prefix + "%",
    )
    return str(row["id"]) if row is not None else None


async def create_batch(
    goal: str,
    items: list[dict[str, Any]],
    *,
    grant_id: str | None = None,
    key_prefix: str = "",
) -> dict[str, Any]:
    title = goal.strip()
    if not title:
        raise ValueError("batch goal is required")
    if not items:
        raise ValueError("batch needs at least one item")
    grant = _uid(grant_id) if grant_id else None
    batch = await db.fetchrow(
        "INSERT INTO resident_batches (goal, total, grant_id) VALUES ($1, $2, $3) "
        "RETURNING id::text AS id, goal, plan, status, total, done, failed, "
        "grant_id, created_at, updated_at",
        title,
        len(items),
        grant,
    )
    if batch is None:  # pragma: no cover
        raise RuntimeError("batch write failed")
    batch_id = batch["id"]
    inserted = 0
    for payload in items:
        if not isinstance(payload, dict):
            raise ValueError("batch items must be objects")
        key = idempotency_key(payload, prefix=key_prefix)
        result = await db.execute(
            "INSERT INTO resident_batch_items (batch_id, payload, idempotency_key) "
            "VALUES ($1, $2::jsonb, $3) ON CONFLICT (batch_id, idempotency_key) DO NOTHING",
            uuid.UUID(batch_id),
            json.dumps(payload, default=str),
            key,
        )
        if result.endswith(" 1"):
            inserted += 1
    await db.execute(
        "UPDATE resident_batches SET total = $2, updated_at = now() WHERE id = $1",
        uuid.UUID(batch_id),
        inserted,
    )
    batch = dict(batch)
    batch["total"] = inserted
    return _batch_view(batch)


async def list_batches(*, limit: int = 50) -> list[dict[str, Any]]:
    rows = await db.fetch(
        "SELECT id::text AS id, goal, plan, status, total, done, failed, grant_id, "
        "created_at, updated_at FROM resident_batches ORDER BY created_at DESC LIMIT $1",
        max(1, limit),
    )
    return [_batch_view(dict(row)) for row in rows]


async def batch_status(batch_id: str) -> dict[str, Any] | None:
    resolved = await resolve_batch_id(batch_id)
    if resolved is None:
        return None
    uid = uuid.UUID(resolved)
    row = await db.fetchrow(
        "SELECT id::text AS id, goal, plan, status, total, done, failed, grant_id, "
        "created_at, updated_at FROM resident_batches WHERE id = $1",
        uid,
    )
    if row is None:
        return None
    items = await db.fetch(
        "SELECT id::text AS id, payload, idempotency_key, status, attempts, result, "
        "error, updated_at FROM resident_batch_items WHERE batch_id = $1 "
        "ORDER BY updated_at DESC LIMIT 50",
        uid,
    )
    result = _batch_view(dict(row))
    result["items"] = [dict(item) for item in items]
    return result


async def cancel_batch(batch_id: str) -> bool:
    resolved = await resolve_batch_id(batch_id)
    if resolved is None:
        return False
    uid = uuid.UUID(resolved)
    result = await db.execute(
        "UPDATE resident_batches SET status = 'cancelled', updated_at = now() "
        "WHERE id = $1 AND status IN ('queued', 'running', 'paused')",
        uid,
    )
    return result.endswith(" 1")


async def claim_items(batch_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    resolved = await resolve_batch_id(batch_id)
    if resolved is None:
        return []
    uid = uuid.UUID(resolved)
    rows = await db.fetch(
        "WITH picked AS ("
        "  SELECT id FROM resident_batch_items WHERE batch_id = $1 AND status = 'queued' "
        "  ORDER BY updated_at LIMIT $2 FOR UPDATE SKIP LOCKED"
        ") UPDATE resident_batch_items AS item SET status = 'running', "
        "attempts = item.attempts + 1, updated_at = now() FROM picked "
        "WHERE item.id = picked.id "
        "RETURNING item.id::text AS id, item.payload, item.idempotency_key, "
        "item.status, item.attempts, item.result, item.error",
        uid,
        max(1, limit),
    )
    return [dict(row) for row in rows]


async def finish_item(
    item_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> bool:
    if status not in ("done", "failed", "skipped"):
        raise ValueError("status must be done|failed|skipped")
    uid = _uid(item_id)
    if uid is None:
        return False
    row = await db.fetchrow(
        "UPDATE resident_batch_items SET status = $2, result = $3::jsonb, error = $4, "
        "updated_at = now() WHERE id = $1 "
        "RETURNING batch_id::text AS batch_id",
        uid,
        status,
        json.dumps(result) if result is not None else None,
        error[:2_000],
    )
    if row is None:
        return False
    batch = _uid(row["batch_id"])
    if batch is not None:
        await db.execute(
            "UPDATE resident_batches SET "
            "done = (SELECT count(*) FROM resident_batch_items WHERE batch_id = $1 AND status = 'done'), "
            "failed = (SELECT count(*) FROM resident_batch_items WHERE batch_id = $1 AND status IN ('failed','skipped')), "
            "status = CASE WHEN (SELECT count(*) FROM resident_batch_items WHERE batch_id = $1 AND status IN ('queued','running')) = 0 "
            "THEN 'done' ELSE status END, updated_at = now() WHERE id = $1",
            batch,
        )
    return True
