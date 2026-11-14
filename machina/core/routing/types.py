"""Typed model-routing contracts.

The router is intentionally deterministic: provider/model choice is a pure
function of the connected inventory, a task profile, and an explicit policy.
No model call is made to choose a model. This keeps routing auditable and
prevents another hidden token cost on every turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ROLES: tuple[str, ...] = ("planner", "supervisor", "worker", "utility")
TRANSPORTS: tuple[str, ...] = ("native", "proxy", "mcp")
COST_TIERS: tuple[str, ...] = ("low", "balanced", "high")
ORCHESTRATION_MODES: tuple[str, ...] = ("single_model", "multi_model")


class RoutingError(RuntimeError):
    """No route can satisfy the task's hard constraints."""


@dataclass(frozen=True)
class TaskRouteProfile:
    """Deterministic task facts consumed by the model router."""

    prompt: str
    task_class: str
    task_families: tuple[str, ...]
    capabilities: tuple[str, ...]
    complexity: str
    complexity_score: int
    risk: str
    needs_tools: bool
    needs_reasoning: bool
    context_tokens: int
    parallel: bool
    cost_tier: str
    privacy: str
    reasons: tuple[str, ...] = ()

    @property
    def family(self) -> str:
        if self.task_families:
            return self.task_families[0]
        return self.task_class or "task"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_class": self.task_class,
            "task_families": list(self.task_families),
            "capabilities": list(self.capabilities),
            "complexity": self.complexity,
            "complexity_score": self.complexity_score,
            "risk": self.risk,
            "needs_tools": self.needs_tools,
            "needs_reasoning": self.needs_reasoning,
            "context_tokens": self.context_tokens,
            "parallel": self.parallel,
            "cost_tier": self.cost_tier,
            "privacy": self.privacy,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ModelProfile:
    """Normalized capability and role data for one provider model."""

    provider: str
    model: str
    canonical: str
    display_name: str
    family: str
    description: str
    context_window: int
    max_output_tokens: int
    tools: bool
    reasoning: bool
    vision: bool
    input_cost_per_mtok: float | None
    output_cost_per_mtok: float | None
    cache_read_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None
    free: bool = False
    quota_pressure: float = 0.0
    source: str = "heuristic"
    confidence: str = "low"
    role_scores: dict[str, float] = field(default_factory=dict)
    task_scores: dict[str, float] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"

    def supports_role(self, role: str) -> bool:
        return role in ROLES and float(self.role_scores.get(role, 0.0)) > 0.0

    def estimated_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.free:
            return 0.0
        if self.input_cost_per_mtok is None or self.output_cost_per_mtok is None:
            return None
        return (
            (max(0, input_tokens) / 1_000_000.0) * self.input_cost_per_mtok
            + (max(0, output_tokens) / 1_000_000.0) * self.output_cost_per_mtok
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "canonical": self.canonical,
            "display_name": self.display_name,
            "family": self.family,
            "description": self.description,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "tools": self.tools,
            "reasoning": self.reasoning,
            "vision": self.vision,
            "input_cost_per_mtok": self.input_cost_per_mtok,
            "output_cost_per_mtok": self.output_cost_per_mtok,
            "cache_read_per_mtok": self.cache_read_per_mtok,
            "cache_write_per_mtok": self.cache_write_per_mtok,
            "free": self.free,
            "quota_pressure": self.quota_pressure,
            "source": self.source,
            "confidence": self.confidence,
            "role_scores": dict(self.role_scores),
            "task_scores": dict(self.task_scores),
        }


@dataclass(frozen=True)
class ModelRoute:
    """One callable model/provider/transport tuple."""

    profile: ModelProfile
    transport: str = "native"
    available: bool = True
    reason: str = ""
    provider_model: str = ""

    @property
    def key(self) -> str:
        return f"{self.transport}:{self.profile.key}"

    @property
    def label(self) -> str:
        return f"{self.profile.provider}/{self.profile.model} ({self.transport})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.profile.provider,
            "model": self.profile.model,
            "canonical": self.profile.canonical,
            "transport": self.transport,
            "available": self.available,
            "reason": self.reason,
            "provider_model": self.provider_model or self.profile.model,
            "role_scores": dict(self.profile.role_scores),
            "free": self.profile.free,
        }


@dataclass(frozen=True)
class RouteCandidate:
    """A scored candidate retained for explanation and fallback."""

    route: ModelRoute
    score: float
    quality: float
    cost: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.to_dict(),
            "score": round(self.score, 4),
            "quality": round(self.quality, 4),
            "cost": None if self.cost is None else round(self.cost, 8),
            "reason": self.reason,
        }


@dataclass
class RouteDecision:
    """The complete, auditable routing decision for one turn or plan."""

    mode: str
    policy: str
    task: TaskRouteProfile
    planner: ModelRoute
    supervisor: ModelRoute
    workers: tuple[ModelRoute, ...]
    candidates: dict[str, tuple[RouteCandidate, ...]] = field(default_factory=dict)
    fallbacks: dict[str, tuple[ModelRoute, ...]] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0

    def route_for(self, role: str) -> ModelRoute:
        if role == "planner":
            return self.planner
        if role == "supervisor":
            return self.supervisor
        if role == "worker":
            return self.workers[0]
        if role == "utility":
            return self.workers[0]
        raise KeyError(role)

    @property
    def distinct_worker_routes(self) -> tuple[ModelRoute, ...]:
        seen: set[str] = set()
        result: list[ModelRoute] = []
        for route in self.workers:
            if route.key not in seen:
                seen.add(route.key)
                result.append(route)
        return tuple(result)

    def estimated_cost(self) -> float | None:
        routes = (self.planner, self.supervisor, *self.workers)
        costs = [
            route.profile.estimated_cost(
                self.estimated_input_tokens,
                self.estimated_output_tokens,
            )
            for route in routes
        ]
        known = [cost for cost in costs if cost is not None]
        return sum(known) if len(known) == len(costs) else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "policy": self.policy,
            "task": self.task.to_dict(),
            "planner": self.planner.to_dict(),
            "supervisor": self.supervisor.to_dict(),
            "workers": [route.to_dict() for route in self.workers],
            "distinct_worker_routes": [route.to_dict() for route in self.distinct_worker_routes],
            "candidates": {
                role: [candidate.to_dict() for candidate in rows]
                for role, rows in self.candidates.items()
            },
            "fallbacks": {
                role: [route.to_dict() for route in rows]
                for role, rows in self.fallbacks.items()
            },
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "estimated_cost_usd": self.estimated_cost(),
        }

    def explain(self) -> str:
        """Compact human-readable route explanation for CLI/UI surfaces."""
        lines = [
            f"mode={self.mode} policy={self.policy}",
            f"task={self.task.task_class}/{self.task.family} "
            f"complexity={self.task.complexity} risk={self.task.risk}",
            f"planner={self.planner.label}",
            f"supervisor={self.supervisor.label}",
            f"workers={len(self.workers)} route(s)="
            + ", ".join(route.label for route in self.distinct_worker_routes),
        ]
        if self.estimated_cost() is not None:
            lines.append(f"estimated_cost_usd={self.estimated_cost():.6f}")
        for warning in self.warnings:
            lines.append(f"warning={warning}")
        return "\n".join(lines)
