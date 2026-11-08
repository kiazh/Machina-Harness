"""Bounded, evidence-backed domain experience briefs.

Harness2 keeps a per-domain library of adopted harnesses and lessons. Machina
already records execution outcomes in RSI, but only exposed the chosen
strategy. This module turns the same durable evidence into a compact brief:
which tools repeatedly succeeded, which failed, how many episodes support the
claim, and the selected approach.

The brief contains tool names and aggregate outcomes only. It never includes
prompt text, tool arguments, or model output, and it is advisory rather than
authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["build_experience_brief"]

_MAX_CHARS = 700
_MAX_NAMES = 3


def _names(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            name
            for name in values
            if isinstance(name, str) and name.strip() and len(name) <= 80
        )
    )[:_MAX_NAMES]


def _ranked_tools(tools: Mapping[str, Any], *, high: bool) -> tuple[str, ...]:
    ranked = sorted(
        (
            (name, float(weight))
            for name, weight in tools.items()
            if isinstance(name, str)
            and isinstance(weight, (int, float))
            and ((weight >= 1.05) if high else (weight <= 0.95))
        ),
        key=lambda item: (-item[1] if high else item[1], item[0]),
    )
    return _names([name for name, _weight in ranked])


def build_experience_brief(
    domain: str,
    payload: Mapping[str, Any] | None,
    *,
    max_chars: int = _MAX_CHARS,
) -> str:
    """Render one compact domain brief from aggregate RSI metrics.

    ``payload`` is the per-domain object produced by ``rsi.aggregate``. Missing
    or malformed fields degrade to an empty brief; a cold-start domain makes
    no claim.
    """
    if not isinstance(payload, Mapping) or max_chars < 80:
        return ""
    try:
        observations = int(payload.get("observations") or 0)
    except (TypeError, ValueError):
        observations = 0
    if observations <= 0:
        return ""
    tools = payload.get("tools")
    tools = tools if isinstance(tools, Mapping) else {}
    strong = _ranked_tools(tools, high=True)
    weak = _ranked_tools(tools, high=False)
    failures_raw = payload.get("tool_failures")
    failures_raw = failures_raw if isinstance(failures_raw, Mapping) else {}
    failures = sorted(
        (
            (name, int(count))
            for name, count in failures_raw.items()
            if isinstance(name, str) and isinstance(count, (int, float)) and count > 0
        ),
        key=lambda item: (-item[1], item[0]),
    )[:_MAX_NAMES]

    parts = [f"Domain experience: {domain or 'general'}; {observations} observed episode{'s' if observations != 1 else ''}."]
    if strong:
        parts.append("Strong tools: " + ", ".join(strong) + ".")
    if weak:
        parts.append("Weaker tools: " + ", ".join(weak) + ".")
    if failures:
        parts.append(
            "Failed outcomes: "
            + ", ".join(f"{name} ({count})" for name, count in failures)
            + "."
        )
    parts.append("This is fallible evidence from past outcomes, not authority.")
    text = " ".join(parts)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"
