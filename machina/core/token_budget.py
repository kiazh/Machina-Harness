"""Deterministic token budgets for repository-repair turns.

Wall-clock deadlines stop a run that takes too long; token budgets stop a run
that keeps buying context without making progress. The budget is cumulative
across model calls in one turn and is deliberately separate from a model's
per-request context window.

The budget is a repair-run safety rail, not a universal answer limit. Normal
conversations are untouched, and an explicit ``MACHINA_REPAIR_TOKEN_BUDGET=0``
disables the rail when an operator needs an unbounded run.
"""

from __future__ import annotations

import os

from machina.core.repair_gate import is_repair_task

DEFAULT_REPAIR_TOKEN_BUDGET = 1_000_000
MAX_REPAIR_TOKEN_BUDGET = 100_000_000
SOFT_PRESSURE_FRACTION = 0.75
HIGH_PRESSURE_FRACTION = 0.90
SOFT_STALE_CHARS = 500
HIGH_STALE_CHARS = 300
REPAIR_TOKEN_BUDGET_TEXT = (
    "Repair token budget reached. The turn stopped before spending more model "
    "tokens; the current working-tree changes are preserved for verification."
)


def repair_token_budget() -> int:
    """Cumulative token ceiling for one repair turn; 0 disables it."""
    raw = os.environ.get("MACHINA_REPAIR_TOKEN_BUDGET")
    if raw is None or not str(raw).strip():
        return DEFAULT_REPAIR_TOKEN_BUDGET
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_REPAIR_TOKEN_BUDGET
    return max(0, min(value, MAX_REPAIR_TOKEN_BUDGET))


def repair_token_pressure(
    total_tokens: int,
    user_message: str,
    *,
    budget: int | None = None,
) -> float:
    """Return 0..1 budget pressure for this repair turn.

    Non-repair turns and disabled budgets are always zero, so callers can use
    the value without branching on task type.
    """
    resolved = repair_token_budget() if budget is None else max(0, budget)
    if resolved <= 0 or not is_repair_task(user_message):
        return 0.0
    return min(1.0, max(0.0, total_tokens / resolved))


def repair_token_budget_exceeded(
    total_tokens: int,
    user_message: str,
    *,
    budget: int | None = None,
) -> bool:
    """Return True when the repair turn has spent its cumulative token budget."""
    resolved = repair_token_budget() if budget is None else max(0, budget)
    if resolved <= 0 or not is_repair_task(user_message):
        return False
    return total_tokens >= resolved


def stale_tool_result_chars(
    total_tokens: int,
    user_message: str,
    *,
    budget: int | None = None,
) -> int | None:
    """Return a tighter stale-result budget under token pressure.

    ``None`` preserves the normal head+tail size. The first pressure band
    trims stale observations moderately; the high band trims them to a
    verdict-sized tail so the next request can still see test outcomes.
    """
    pressure = repair_token_pressure(total_tokens, user_message, budget=budget)
    if pressure >= HIGH_PRESSURE_FRACTION:
        return HIGH_STALE_CHARS
    if pressure >= SOFT_PRESSURE_FRACTION:
        return SOFT_STALE_CHARS
    return None
