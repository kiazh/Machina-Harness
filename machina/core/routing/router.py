"""Deterministic task-to-model routing.

Selection is a pure function of the task profile and connected inventory. It
does not call a model, spawn a harness, or mutate provider state. The result is
a complete role assignment (planner, supervisor, workers) with fallbacks and
an explanation suitable for CLI/TUI/gateway surfaces.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from machina.controller.complexity import score_complexity
from machina.core.routing.catalog import ModelInventory, build_inventory, profile_from_row
from machina.core.routing.types import (
    COST_TIERS,
    ORCHESTRATION_MODES,
    ROLES,
    ModelProfile,
    ModelRoute,
    RouteCandidate,
    RouteDecision,
    RoutingError,
    TaskRouteProfile,
)
from machina.core.task_descriptor import build_task_descriptor

_PARALLEL_CUE = re.compile(
    r"\b(?:in\s+parallel|parallel(?:ize|ise|ly)?|at\s+the\s+same\s+time|"
    r"simultaneously|independent\s+tracks?|separate\s+tracks?|concurrently)\b",
    re.IGNORECASE,
)
_RESEARCH_CUE = re.compile(r"\b(?:research|investigat|sources?|cite|literature)\b", re.I)
_RISK_CUE = re.compile(
    r"\b(?:production|deploy|delete|drop\s+table|payment|billing|security|auth|"
    r"credential|secret|migration|destructive)\b",
    re.I,
)

#: Routes that need an executable transport. Tests and callers may expand this.
_DEFAULT_TRANSPORTS = ("native", "proxy")

#: Cost tier -> (input/output token budget, cost weight). Planner planning and
#: a small worker turn are enough for selection; actual spend is metered.
_TIER_BUDGET: dict[str, tuple[int, int, float]] = {
    "low": (6_000, 1_200, 1.15),
    "balanced": (10_000, 2_500, 0.55),
    "high": (18_000, 4_000, 0.15),
}


def _cfg(config: Any, key: str, default: Any = None) -> Any:
    if config is None:
        return default
    try:
        value = config.get(key)
    except Exception:
        return default
    return default if value is None or (isinstance(value, str) and not value.strip()) else value


def detect_parallel_intent(prompt: str) -> bool:
    """Whether the user explicitly asked for independent parallel work."""
    return bool(_PARALLEL_CUE.search(prompt or ""))


def _estimate_tokens(prompt: str) -> int:
    # Conservative, dependency-free estimate; the exact gate uses the provider
    # tokenizer later. Routing only needs a capacity/fit bucket.
    return max(512, min(64_000, (len(prompt or "") // 3) + 512))


def build_task_profile(
    prompt: str,
    *,
    cost_tier: str = "balanced",
    context_tokens: int | None = None,
    parallel: bool | None = None,
    project_metadata: Mapping[str, Any] | str | None = None,
) -> TaskRouteProfile:
    """Build the deterministic routing profile for one prompt."""
    tier = str(cost_tier or "balanced").strip().lower()
    if tier not in COST_TIERS:
        tier = "balanced"
    descriptor = build_task_descriptor(prompt, project_metadata=project_metadata)
    complexity = score_complexity(prompt)
    text = prompt or ""
    capabilities = tuple(descriptor.requested_capabilities)
    risk = "high" if _RISK_CUE.search(text) or {
        "security",
        "payments",
        "authentication",
    } & set(capabilities) else "medium" if complexity["score"] >= 5 else "low"
    needs_tools = descriptor.operation_scope == "mutating" or descriptor.task_class in {
        "implement",
        "debug",
        "review-readonly",
        "learn-directory",
    }
    needs_reasoning = descriptor.task_class in {"implement", "debug", "review-readonly"} or bool(
        _RESEARCH_CUE.search(text)
    )
    estimated = context_tokens if context_tokens is not None else _estimate_tokens(text)
    return TaskRouteProfile(
        prompt=text,
        task_class=descriptor.task_class,
        task_families=descriptor.task_families,
        capabilities=capabilities,
        complexity=str(complexity["level"]),
        complexity_score=int(complexity["score"]),
        risk=risk,
        needs_tools=needs_tools,
        needs_reasoning=needs_reasoning,
        context_tokens=max(1, int(estimated)),
        parallel=detect_parallel_intent(text) if parallel is None else bool(parallel),
        cost_tier=tier,
        privacy="local-only"
        if "local-only" in descriptor.constraints or "no-network" in descriptor.constraints
        else "any",
        reasons=tuple(str(reason) for reason in complexity.get("reasons", ())),
    )


def _task_family_score(profile: ModelProfile, task: TaskRouteProfile) -> float:
    keys = [task.family, task.task_class, *task.task_families]
    values = [profile.task_scores.get(key) for key in keys if profile.task_scores.get(key) is not None]
    if values:
        return float(max(values))
    return float(profile.task_scores.get("coding", 0.6))


def _context_fit(profile: ModelProfile, task: TaskRouteProfile) -> float:
    if task.context_tokens <= 0:
        return 1.0
    if profile.context_window <= 0:
        return 0.5
    ratio = task.context_tokens / profile.context_window
    if ratio <= 0.25:
        return 1.0
    if ratio <= 0.6:
        return 0.85
    if ratio <= 0.9:
        return 0.55
    return 0.0


def _transport_allowed(route: ModelRoute, allowed: Iterable[str]) -> bool:
    return route.transport in set(allowed)


def _eligible_routes(
    inventory: ModelInventory,
    task: TaskRouteProfile,
    *,
    allowed_transports: Iterable[str],
) -> tuple[ModelRoute, ...]:
    allowed = tuple(dict.fromkeys(allowed_transports))
    result: list[ModelRoute] = []
    for route in inventory.routes:
        if not route.available or not _transport_allowed(route, allowed):
            continue
        profile = route.profile
        if _context_fit(profile, task) <= 0.0:
            continue
        if task.privacy == "local-only" and profile.provider not in {"ollama", "custom"}:
            continue
        result.append(route)
    return tuple(result)


def _score_route(
    route: ModelRoute,
    task: TaskRouteProfile,
    role: str,
    *,
    input_tokens: int,
    output_tokens: int,
) -> RouteCandidate:
    profile = route.profile
    role_score = float(profile.role_scores.get(role, 0.0))
    task_score = _task_family_score(profile, task)
    context_score = _context_fit(profile, task)
    tool_score = 1.0 if (profile.tools or not task.needs_tools) else 0.0
    reasoning_score = 1.0 if (profile.reasoning or not task.needs_reasoning) else 0.55
    if role == "planner":
        quality = (
            0.60 * role_score
            + 0.16 * task_score
            + 0.09 * context_score
            + 0.07 * tool_score
            + 0.08 * reasoning_score
        )
    elif role == "supervisor":
        quality = (
            0.60 * role_score
            + 0.18 * task_score
            + 0.08 * context_score
            + 0.07 * tool_score
            + 0.07 * reasoning_score
        )
    else:
        quality = (
            0.42 * role_score
            + 0.24 * task_score
            + 0.14 * context_score
            + 0.10 * tool_score
            + 0.10 * reasoning_score
        )
    tier_budget = _TIER_BUDGET[task.cost_tier]
    cost = profile.estimated_cost(input_tokens, output_tokens)
    cost_weight = tier_budget[2]
    if role == "planner":
        cost_weight *= 0.30
    elif role == "supervisor":
        cost_weight *= 0.25
    elif role == "utility":
        cost_weight *= 1.35
    if task.risk == "high" and role in {"planner", "supervisor"}:
        cost_weight *= 0.35
    if cost is None:
        cost_penalty = 0.045
        cost_label = "unknown"
    else:
        cost_penalty = min(0.35, cost * cost_weight)
        cost_label = f"${cost:.6f}"
    quota_penalty = min(0.2, max(0.0, profile.quota_pressure) * 0.25)
    free_bonus = 0.015 if profile.free else 0.0
    score = quality - cost_penalty - quota_penalty + free_bonus
    if role == "planner":
        score += 0.04 * role_score
    elif role == "supervisor":
        score += 0.025 * role_score
    elif role == "worker":
        score += 0.02 * (profile.task_scores.get("coding", 0.5))
    reason = (
        f"{role}_fit={role_score:.2f} task_fit={task_score:.2f} "
        f"context_fit={context_score:.2f} cost={cost_label}"
    )
    return RouteCandidate(route=route, score=score, quality=quality, cost=cost, reason=reason)


def _version_key(model: str) -> tuple[int, ...]:
    """Newer numeric revisions sort before older ones on a score tie."""
    numbers = [int(part) for part in re.findall(r"\d+", str(model or ""))[:4]]
    return tuple([-number for number in numbers] + [0] * (4 - len(numbers)))


def _rank_routes(
    routes: Iterable[ModelRoute],
    task: TaskRouteProfile,
    role: str,
    *,
    input_tokens: int,
    output_tokens: int,
) -> tuple[RouteCandidate, ...]:
    ranked = [
        _score_route(
            route,
            task,
            role,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        for route in routes
    ]
    ranked.sort(
        key=lambda candidate: (
            -candidate.score,
            _version_key(candidate.route.profile.model),
            candidate.route.key,
        )
    )
    return tuple(ranked)


def _parse_pin(pin: Any) -> tuple[str, str] | None:
    if isinstance(pin, ModelRoute):
        return (pin.profile.provider, pin.profile.model)
    if isinstance(pin, ModelProfile):
        return (pin.provider, pin.model)
    if isinstance(pin, Mapping):
        provider = str(pin.get("provider") or "").strip()
        model = str(pin.get("model") or "").strip()
        if provider and model:
            return (provider, model)
        return None
    text = str(pin or "").strip()
    if not text:
        return None
    if "/" in text:
        provider, model = text.split("/", 1)
        return (provider.strip(), model.strip())
    return None


def _find_route(
    routes: Iterable[ModelRoute],
    provider: str,
    model: str,
) -> ModelRoute | None:
    needle = str(model or "").strip().lower().replace(".", "-")
    for route in routes:
        if route.profile.provider != provider:
            continue
        candidate = route.profile.model.lower().replace(".", "-")
        canonical = route.profile.canonical.lower().replace(".", "-")
        if needle == candidate or needle == canonical or needle in {candidate, canonical}:
            return route
    return None


def _current_route(inventory: ModelInventory, provider: str, model: str) -> ModelRoute | None:
    if not provider or not model:
        return None
    for route in inventory.routes:
        if route.profile.provider == provider and route.profile.model == model:
            return route
    row = {"id": model, "displayName": model, "description": "Configured route"}
    return ModelRoute(profile=profile_from_row(provider, row), transport="native")


def _auto_worker_count(task: TaskRouteProfile, mode: str, requested: Any) -> int:
    if requested not in (None, "", 0, "0", "auto"):
        try:
            return max(1, min(int(requested), 64))
        except (TypeError, ValueError):
            pass
    if mode == "single_model":
        return 1
    if not task.parallel:
        return 1
    if task.complexity == "subagents":
        return 8
    if task.complexity == "single":
        return 4
    return 2


def _role_candidates(
    routes: Iterable[ModelRoute],
    task: TaskRouteProfile,
    role: str,
    *,
    input_tokens: int,
    output_tokens: int,
) -> tuple[RouteCandidate, ...]:
    # A text-only route may plan or summarize, but it cannot be trusted as a
    # file-editing worker or artifact-reading supervisor. Those roles require
    # tool support when the task is mutating or review-oriented.
    eligible = list(routes)
    if task.needs_tools and role in {"worker", "supervisor"}:
        eligible = [route for route in eligible if route.profile.tools]
    return _rank_routes(
        eligible,
        task,
        role,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def route_task(
    prompt: str,
    *,
    inventory: ModelInventory | None = None,
    config: Any = None,
    mode: str | None = None,
    session_provider: str | None = None,
    session_model: str | None = None,
    pins: Mapping[str, Any] | None = None,
    worker_count: Any = None,
    allowed_transports: Iterable[str] | None = None,
    cost_tier: str | None = None,
    project_metadata: Mapping[str, Any] | str | None = None,
) -> RouteDecision:
    """Choose models for a prompt and return an auditable decision.

    ``mode`` is ``single_model`` or ``multi_model``. In single-model mode the
    effective session route (or the highest-scoring single route) is copied to
    every role. In multi-model mode the planner, supervisor, and worker pool
    are selected independently.
    """
    mode_value = str(mode or _cfg(config, "orchestration_mode", "single_model")).strip().lower()
    if mode_value not in ORCHESTRATION_MODES:
        mode_value = "single_model"
    tier = str(cost_tier or _cfg(config, "cost_tier", "balanced")).strip().lower()
    if tier not in COST_TIERS:
        tier = "balanced"
    task = build_task_profile(prompt, cost_tier=tier, project_metadata=project_metadata)
    if inventory is None:
        inventory = build_inventory(config)
    allowed = tuple(allowed_transports or _DEFAULT_TRANSPORTS)
    if config is not None and _cfg(config, "worker_transport", "native") == "mcp":
        allowed = (*allowed, "mcp")
    routes = _eligible_routes(inventory, task, allowed_transports=allowed)
    warnings: list[str] = list(inventory.warnings)
    if not routes:
        current = _current_route(inventory, session_provider or "", session_model or "")
        if current is None:
            raise RoutingError(
                "no connected model satisfies this task "
                f"(tools={task.needs_tools}, context={task.context_tokens}, "
                f"transports={','.join(allowed)})"
            )
        routes = (current,)
        warnings.append("using the configured route because no inventory candidate matched")
    input_tokens, output_tokens, _weight = _TIER_BUDGET[tier]
    input_tokens = max(input_tokens, min(task.context_tokens, 24_000))
    pins = pins or {}
    pin_map = {role: _parse_pin(pins.get(role)) for role in ROLES}
    current = _current_route(inventory, session_provider or "", session_model or "")

    if mode_value == "single_model":
        selected = current
        if selected is None:
            pin = pin_map.get("planner") or pin_map.get("worker")
            selected = _find_route(routes, *pin) if pin else None
        if selected is None:
            # A single-model run is one route for every role; score it as a
            # general worker/planner blend and let the task family break ties.
            ranked = _role_candidates(
                routes,
                task,
                "worker",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            if not ranked:
                raise RoutingError("no connected model with tool support satisfies this task")
            selected = ranked[0].route
        if task.needs_tools and not selected.profile.tools:
            ranked = _role_candidates(
                routes,
                task,
                "worker",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            if not ranked:
                raise RoutingError(
                    "the pinned route is text-only and no tool-capable model is connected"
                )
            warnings.append(
                f"pinned route {selected.label} is text-only; using {ranked[0].route.label} "
                "for this tool-using task"
            )
            selected = ranked[0].route
        candidates = {
            role: _role_candidates(
                routes,
                task,
                role,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            for role in ("planner", "supervisor", "worker")
        }
        count = _auto_worker_count(task, mode_value, worker_count)
        return RouteDecision(
            mode=mode_value,
            policy="single_route",
            task=task,
            planner=selected,
            supervisor=selected,
            workers=tuple(selected for _ in range(count)),
            candidates=candidates,
            fallbacks={
                "planner": tuple(
                    candidate.route
                    for candidate in candidates["planner"]
                    if candidate.route.key != selected.key
                )[:4],
                "worker": tuple(
                    candidate.route
                    for candidate in candidates["worker"]
                    if candidate.route.key != selected.key
                )[:4],
            },
            reasons=(
                "single_model copies the effective route to all roles",
                f"selected={selected.label}",
            ),
            warnings=tuple(warnings),
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
        )

    candidates_by_role: dict[str, tuple[RouteCandidate, ...]] = {}
    for role in ("planner", "supervisor", "worker", "utility"):
        ranked = _role_candidates(
            routes,
            task,
            role,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        candidates_by_role[role] = ranked

    def pick(role: str) -> ModelRoute:
        pin = pin_map.get(role)
        if pin:
            pinned = _find_route(routes, *pin)
            if pinned is not None:
                return pinned
            warnings.append(f"{role} pin {pin[0]}/{pin[1]} is not connected; using auto route")
        ranked = candidates_by_role.get(role) or candidates_by_role["worker"]
        if not ranked:
            raise RoutingError(f"no candidate satisfies the {role} role")
        return ranked[0].route

    planner = pick("planner")
    supervisor = pick("supervisor")
    # High-risk work benefits from an independent review family. Keep the
    # planner as supervisor when no independent family is available.
    if task.risk == "high" and supervisor.profile.family == planner.profile.family:
        independent = next(
            (
                candidate.route
                for candidate in candidates_by_role["supervisor"]
                if candidate.route.profile.family != planner.profile.family
            ),
            None,
        )
        if independent is not None:
            supervisor = independent

    count = _auto_worker_count(task, mode_value, worker_count)
    ranked_workers = list(candidates_by_role.get("worker") or ())
    if not ranked_workers:
        raise RoutingError(
            "no connected worker model has the tool support this task requires"
            if task.needs_tools
            else "no worker candidate satisfies the task"
        )
    worker_routes: list[ModelRoute] = []
    # Reuse a bounded set of worker routes. Twenty workers should not mean
    # twenty different models: that defeats cache reuse and coherent review.
    distinct_limit = max(1, min(int(_cfg(config, "max_worker_routes", 4) or 4), 8))
    cycle = [candidate.route for candidate in ranked_workers[:distinct_limit]]
    for index in range(count):
        worker_routes.append(cycle[index % len(cycle)])
    if count > len(cycle):
        warnings.append(
            f"{count} workers reuse {len(cycle)} worker route(s) to preserve cache and coherence"
        )

    fallbacks = {
        role: tuple(
            candidate.route
            for candidate in candidates_by_role.get(role, ())
            if candidate.route.key
            not in {
                planner.key,
                supervisor.key,
                *(route.key for route in worker_routes),
            }
        )[:4]
        for role in ("planner", "supervisor", "worker", "utility")
    }
    reasons = (
        f"mode={mode_value} policy={tier}",
        f"task_family={task.family} complexity={task.complexity} risk={task.risk}",
        f"planner={planner.label}: {candidates_by_role['planner'][0].reason}",
        f"supervisor={supervisor.label}",
        f"worker_pool={len(cycle)} route(s) for {count} worker(s)",
    )
    return RouteDecision(
        mode=mode_value,
        policy=f"multi_model:{tier}",
        task=task,
        planner=planner,
        supervisor=supervisor,
        workers=tuple(worker_routes),
        candidates=candidates_by_role,
        fallbacks=fallbacks,
        reasons=reasons,
        warnings=tuple(warnings),
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
    )


def route_from_session(
    session: Any,
    prompt: str,
    *,
    inventory: ModelInventory | None = None,
    config: Any = None,
    **kwargs: Any,
) -> RouteDecision:
    """Route using a session's effective provider/model pins."""
    provider = getattr(session, "provider", None) if session is not None else None
    model = getattr(session, "model", None) if session is not None else None
    return route_task(
        prompt,
        inventory=inventory,
        config=config,
        session_provider=provider,
        session_model=model,
        **kwargs,
    )


__all__ = [
    "build_task_profile",
    "detect_parallel_intent",
    "route_from_session",
    "route_task",
]
