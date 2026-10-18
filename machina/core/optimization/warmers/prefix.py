"""Prompt-cache prefix economics.

No function here sends a provider request. The plan is an operator estimate;
synthetic keepalive calls are not implemented.
"""

from __future__ import annotations

from dataclasses import dataclass

from machina.core.optimization.capabilities import ModelCapabilityProfile

__all__ = ["PrefixPlan", "plan_prefix_warm"]


@dataclass(frozen=True)
class PrefixPlan:
    allowed: bool
    reason: str
    estimated_savings_usd: float | None = None


def _rate(value: float | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def plan_prefix_warm(
    profile: ModelCapabilityProfile,
    *,
    prefix_tokens: int,
    expected_turns: float,
    min_prefix_tokens: int = 4096,
    min_expected_turns: float = 2.0,
    max_budget_usd: float = 0.5,
) -> PrefixPlan:
    """Estimate a paid prefix-warm decision from verified metadata only."""
    if not profile.supports_paid_warmers:
        return PrefixPlan(False, "unverified_capability")
    if prefix_tokens < max(0, int(min_prefix_tokens)):
        return PrefixPlan(False, "prefix_too_small")
    if expected_turns < max(0.0, float(min_expected_turns)):
        return PrefixPlan(False, "reuse_too_low")

    price = profile.price
    input_rate = _rate(price.input_per_mtok) if price else None
    read_rate = _rate(price.cache_read_per_mtok) if price else None
    if input_rate is None or read_rate is None:
        return PrefixPlan(False, "unknown_cache_economics")
    if read_rate >= input_rate:
        return PrefixPlan(False, "no_read_savings")

    # One write of the prefix, then reads for each remaining turn. The write
    # premium is unknown for most vendors; using ordinary input as the write
    # basis is conservative (it can only overstate cost, not understate it).
    write_rate = _rate(price.cache_write_per_mtok) if price else None
    if write_rate is None:
        write_rate = input_rate
    tokens = float(prefix_tokens)
    gross_savings = tokens * (input_rate - read_rate) * max(0.0, expected_turns - 1.0)
    write_cost = tokens * write_rate
    net = (gross_savings - write_cost) / 1_000_000.0
    if net <= 0:
        return PrefixPlan(False, "negative_net_savings", net)
    if net > max(0.0, float(max_budget_usd)):
        return PrefixPlan(False, "budget_exceeded", net)
    return PrefixPlan(True, "positive_net_savings", net)
