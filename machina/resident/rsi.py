"""Resident preference rules: the concrete, versioned RSI layer.

Signals are collected by the resident review loop; this module owns the
durable rules. Rules are proposed, evaluated, activated, and retired — never
silently rewritten in place.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from machina.db.connection import db

_ALLOWED = frozenset({"proposed", "active", "retired"})


def rule_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "rule": str(row.get("rule") or ""),
        "category": str(row.get("category") or ""),
        "confidence": float(row.get("confidence") or 0),
        "status": str(row.get("status") or "proposed"),
        "version": int(row.get("version") or 1),
        "approvedBy": str(row.get("approved_by") or ""),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _uid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def list_rules(*, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if status is not None and status not in _ALLOWED:
        raise ValueError("status must be proposed|active|retired")
    rows = await db.fetch(
        "SELECT id::text AS id, rule, category, confidence, evidence, status, "
        "version, approved_by, created_at, updated_at FROM resident_preference_rules "
        "WHERE ($1::text IS NULL OR status = $1) ORDER BY updated_at DESC LIMIT $2",
        status,
        max(1, limit),
    )
    result = []
    for row in rows:
        item = dict(row)
        if isinstance(item.get("evidence"), str):
            try:
                item["evidence"] = json.loads(item["evidence"])
            except ValueError:
                item["evidence"] = {}
        result.append(rule_view(item))
    return result


async def propose_rule(
    rule: str,
    *,
    category: str = "style",
    confidence: float = 0.5,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = rule.strip()
    if not text:
        raise ValueError("rule is required")
    row = await db.fetchrow(
        "INSERT INTO resident_preference_rules "
        "(rule, category, confidence, evidence, status) "
        "VALUES ($1, $2, $3, $4::jsonb, 'proposed') "
        "RETURNING id::text AS id, rule, category, confidence, evidence, status, "
        "version, approved_by, created_at, updated_at",
        text,
        category.strip() or "style",
        max(0.0, min(1.0, float(confidence))),
        json.dumps(evidence or {}),
    )
    return rule_view(dict(row)) if row else {}


async def set_rule_status(rule_id: str, status: str, *, approved_by: str = "user") -> dict[str, Any] | None:
    if status not in _ALLOWED:
        raise ValueError("status must be proposed|active|retired")
    uid = _uid(rule_id)
    if uid is None:
        return None
    row = await db.fetchrow(
        "UPDATE resident_preference_rules SET status = $2, approved_by = $3, "
        "version = version + 1, updated_at = now() WHERE id = $1 "
        "RETURNING id::text AS id, rule, category, confidence, evidence, status, "
        "version, approved_by, created_at, updated_at",
        uid,
        status,
        approved_by,
    )
    return rule_view(dict(row)) if row else None
