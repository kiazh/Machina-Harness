"""Pure reuse estimate for prompt-cache warmers.

The estimate is intentionally simple, deterministic, and bounded. It uses the
session's observed turn counts, not prompt content, and never calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ReuseEstimate", "estimate_expected_turns"]

_MAX_EXPECTED_TURNS = 12.0
_MIN_EXPECTED_TURNS = 1.0


@dataclass(frozen=True)
class ReuseEstimate:
    expected_turns: float


def estimate_expected_turns(history_turn_counts: list[int] | None) -> ReuseEstimate:
    """Predict remaining turns from recent completed sessions.

    Recent sessions weigh more. Empty history is not evidence of reuse and
    yields the conservative floor (1.0); warmers must not invent demand.
    """
    counts = [
        max(1, min(int(value), int(_MAX_EXPECTED_TURNS * 2)))
        for value in (history_turn_counts or [])
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not counts:
        return ReuseEstimate(expected_turns=_MIN_EXPECTED_TURNS)
    recent = counts[-5:]
    weighted = 0.0
    weight_sum = 0.0
    for index, value in enumerate(recent):
        weight = float(index + 1)
        weighted += value * weight
        weight_sum += weight
    average = weighted / weight_sum if weight_sum else _MIN_EXPECTED_TURNS
    # A turn already completed still counts as one cached continuation; the
    # average is bounded so a single long session cannot authorize runaway
    # synthetic calls.
    expected = max(_MIN_EXPECTED_TURNS, min(_MAX_EXPECTED_TURNS, average))
    return ReuseEstimate(expected_turns=expected)
