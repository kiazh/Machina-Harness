"""Deterministic RSI review of approval outcomes.

No model judge: the signal is the user's own approve/deny history. Repeated
denials become an "ask first" proposal; repeated clean approvals become a
low-risk "proceed when a grant covers it" proposal. Proposals never activate
themselves.
"""

from __future__ import annotations

from typing import Any

from machina.db.connection import db
from machina.resident import rsi


def build_proposals(counts: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for operation, values in sorted(counts.items()):
        approved = int(values.get("approved") or 0)
        denied = int(values.get("denied") or 0)
        if not operation:
            continue
        if denied >= 2 and denied >= approved:
            proposals.append(
                {
                    "rule": f"Ask before {operation.replace('_', ' ')}; the user denied it repeatedly.",
                    "category": "policy",
                    "confidence": min(0.9, 0.5 + denied * 0.08),
                    "evidence": {"operation": operation, "approved": approved, "denied": denied},
                }
            )
        elif approved >= 5 and denied == 0:
            proposals.append(
                {
                    "rule": f"Proceed with {operation.replace('_', ' ')} when an active grant covers it.",
                    "category": "tool",
                    "confidence": min(0.85, 0.55 + approved * 0.02),
                    "evidence": {"operation": operation, "approved": approved, "denied": denied},
                }
            )
    return proposals


async def review(*, days: int = 30) -> list[dict[str, Any]]:
    rows = await db.fetch(
        "SELECT detail->>'operation' AS operation, "
        "COUNT(*) FILTER (WHERE verdict = 'approved')::int AS approved, "
        "COUNT(*) FILTER (WHERE verdict = 'denied')::int AS denied "
        "FROM resident_actions WHERE action = 'approval.resolve' "
        "AND created_at > now() - make_interval(days => $1) "
        "GROUP BY 1",
        max(1, min(365, days)),
    )
    counts = {
        str(row["operation"] or ""): {
            "approved": int(row["approved"] or 0),
            "denied": int(row["denied"] or 0),
        }
        for row in rows
    }
    existing = {
        str(rule.get("rule") or "")
        for rule in await rsi.list_rules(limit=200)
        if rule.get("status") in ("proposed", "active")
    }
    created: list[dict[str, Any]] = []
    for proposal in build_proposals(counts):
        if proposal["rule"] in existing:
            continue
        created.append(
            await rsi.propose_rule(
                proposal["rule"],
                category=proposal["category"],
                confidence=proposal["confidence"],
                evidence=proposal["evidence"],
            )
        )
    return created
