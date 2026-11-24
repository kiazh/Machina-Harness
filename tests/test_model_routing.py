"""Deterministic connected-model routing contracts."""

from __future__ import annotations

from dataclasses import dataclass

from machina.core.routing.catalog import build_inventory
from machina.core.routing.router import (
    build_task_profile,
    detect_parallel_intent,
    route_task,
)
from machina.core.routing.types import ModelProfile, RoutingError


@dataclass
class FakeConfig:
    values: dict

    def get(self, key: str, default=None):
        return self.values.get(key, default)


def _config(**overrides):
    values = {
        "provider": "openai",
        "model": "gpt-6.1-sol",
        "openai_api_key": "sk-test",
        "anthropic_api_key": "sk-ant",
        "deepseek_api_key": "sk-ds",
        "max_worker_routes": 4,
        "cost_tier": "balanced",
        "orchestration_mode": "multi_model",
        "worker_transport": "native",
    }
    values.update(overrides)
    return FakeConfig(values)


def _inventory(**overrides):
    return build_inventory(
        _config(**overrides),
        providers=("openai", "anthropic", "deepseek"),
        probe_kiro=False,
    )


def _profile(
    model: str,
    *,
    family: str,
    worker: float,
    utility: float | None = None,
    coding: float = 0.8,
    cost: float = 1.0,
    provider: str = "test",
) -> ModelProfile:
    return ModelProfile(
        provider=provider,
        model=model,
        canonical=f"{family}:{model}",
        display_name=model,
        family=family,
        description="test route",
        context_window=1_000_000,
        max_output_tokens=32_000,
        tools=True,
        reasoning=True,
        vision=False,
        input_cost_per_mtok=cost,
        output_cost_per_mtok=cost * 3,
        role_scores={
            "planner": 0.8,
            "supervisor": 0.8,
            "worker": worker,
            "utility": utility if utility is not None else worker,
        },
        task_scores={
            "coding": coding,
            "web-build": coding,
            "debug": coding,
            "review": coding,
            "research": coding,
            "documentation": coding,
        },
    )


def test_inventory_only_contains_connected_models() -> None:
    config = _config(anthropic_api_key="", deepseek_api_key="")
    inventory = build_inventory(
        config,
        providers=("openai", "anthropic", "deepseek"),
        probe_kiro=False,
    )

    providers = {profile.provider for profile in inventory.profiles}
    assert "openai" in providers
    assert "anthropic" not in providers
    assert "deepseek" not in providers
    assert inventory.provider_status["anthropic"] == "no API key"


def test_single_model_copies_the_pinned_session_route() -> None:
    inventory = _inventory()
    decision = route_task(
        "Build a small portfolio site",
        inventory=inventory,
        config=_config(orchestration_mode="single_model"),
        session_provider="deepseek",
        session_model="deepseek-v4-flash-vision-exp",
        mode="single_model",
        worker_count=4,
    )

    assert all(route.profile.provider == "deepseek" for route in decision.workers)
    assert decision.planner.profile.provider == "deepseek"
    assert decision.supervisor.profile.provider == "deepseek"
    assert len({route.key for route in decision.workers}) == 1
    assert decision.policy == "single_route"


def test_multi_model_selects_roles_and_bounds_worker_routes() -> None:
    inventory = _inventory()
    decision = route_task(
        "Build a portfolio website with a contact form, tests, and documentation in parallel",
        inventory=inventory,
        config=_config(orchestration_mode="multi_model"),
        mode="multi_model",
        worker_count=20,
    )

    assert decision.mode == "multi_model"
    assert decision.planner.profile.tools is True
    assert decision.supervisor.profile.tools is True
    assert len(decision.workers) == 20
    assert 1 <= len(decision.distinct_worker_routes) <= 4
    assert decision.estimated_cost() is not None
    assert "planner=" in decision.explain()


def test_worker_pool_diversifies_families_before_version_aliases() -> None:
    from machina.core.routing.catalog import ModelInventory

    inventory = ModelInventory(
        profiles=(
            _profile("sonnet-5.5", family="claude-sonnet", worker=0.95, coding=0.95, cost=2),
            _profile("sonnet-5", family="claude-sonnet", worker=0.95, coding=0.95, cost=2),
            _profile("sonnet-4.6", family="claude-sonnet", worker=0.95, coding=0.95, cost=2),
            _profile("qwen-coder", family="qwen", worker=0.86, coding=0.86, cost=0.2),
            _profile("minimax", family="minimax", worker=0.84, coding=0.84, cost=0.3),
            _profile("kimi", family="kimi-k3", worker=0.83, coding=0.88, cost=0.4),
        ),
        provider_status={"test": "configured"},
    )

    decision = route_task(
        "Implement the frontend, backend, and tests in parallel",
        inventory=inventory,
        config=_config(orchestration_mode="multi_model", max_worker_routes=4),
        mode="multi_model",
        worker_count=3,
    )

    families = {route.profile.family for route in decision.workers}
    assert len(families) == 3
    assert "claude-sonnet" in families
    assert "qwen" in families


def test_direct_tool_free_tasks_use_utility_fit_workers() -> None:
    from machina.core.routing.catalog import ModelInventory

    inventory = ModelInventory(
        profiles=(
            _profile(
                "strong-worker",
                family="claude-sonnet",
                worker=0.99,
                utility=0.60,
                coding=0.99,
                cost=10,
            ),
            _profile(
                "fast-utility",
                family="claude-haiku",
                worker=0.70,
                utility=0.99,
                coding=0.70,
                cost=0.1,
            ),
        ),
        provider_status={"test": "configured"},
    )

    decision = route_task(
        "Say hello",
        inventory=inventory,
        config=_config(orchestration_mode="multi_model"),
        mode="multi_model",
        worker_count=1,
    )

    assert decision.task.complexity == "direct"
    assert decision.workers[0].profile.model == "fast-utility"
    assert decision.workers[0].key == decision.candidates["utility"][0].route.key


def test_free_hermes_session_keeps_routing_on_free_models() -> None:
    from types import SimpleNamespace

    from machina.core.routing.router import prefer_free_hermes_routes

    free = SimpleNamespace(profile=SimpleNamespace(provider="hermes", free=True))
    paid = SimpleNamespace(profile=SimpleNamespace(provider="hermes", free=False))
    other = SimpleNamespace(profile=SimpleNamespace(provider="openrouter", free=False))

    assert list(prefer_free_hermes_routes([paid, free, other], "hermes", "x:free")) == [
        free,
        other,
    ]
    # A paid session route opts back into the full catalog.
    assert list(prefer_free_hermes_routes([paid, free], "hermes", "paid-model")) == [paid, free]


def test_free_proxy_catalog_rows_are_marked_free_and_zero_cost() -> None:
    from machina.core.routing.catalog import profile_from_row

    free = profile_from_row(
        "hermes",
        {"id": "tencent/hy4-flash:free", "description": "Hermes proxy", "free": True},
    )
    paid = profile_from_row(
        "hermes",
        {"id": "~z-ai/glm-flash-latest", "description": "Hermes proxy", "free": False},
    )

    assert free.free is True
    assert free.input_cost_per_mtok == 0.0
    assert free.output_cost_per_mtok == 0.0
    assert paid.free is False


def test_family_task_priors_keep_kiro_aliases_differentiated() -> None:
    from machina.core.routing.catalog import profile_from_row

    def profile(model: str) -> ModelProfile:
        return profile_from_row(
            "kiro",
            {
                "id": model,
                "description": f"{model} model with 1M context window",
                "contextWindow": 1_000_000,
                "source": "live",
            },
        )

    opus = profile("claude-opus-5.5")
    sonnet = profile("claude-sonnet-5.5")
    haiku = profile("claude-haiku-5.5")
    minimax = profile("minimax-m2.5")

    assert sonnet.task_scores["coding"] > opus.task_scores["coding"]
    assert opus.task_scores["review"] > sonnet.task_scores["review"]
    assert haiku.role_scores["utility"] > sonnet.role_scores["utility"]
    assert minimax.task_scores["coding"] >= 0.84


def test_high_risk_work_prefers_an_independent_supervisor_family() -> None:
    inventory = _inventory()
    decision = route_task(
        "Add authentication and security review for the production login flow",
        inventory=inventory,
        config=_config(orchestration_mode="multi_model"),
        mode="multi_model",
    )

    assert decision.task.risk == "high"
    assert decision.supervisor.profile.provider == "anthropic"


def test_context_and_tool_constraints_are_hard_filters() -> None:
    inventory = _inventory()
    # A synthetic route that is otherwise strong but cannot use tools/context.
    limited = ModelProfile(
        provider="openai",
        model="tiny-no-tools",
        canonical="tiny-no-tools",
        display_name="Tiny",
        family="gpt-nano",
        description="small",
        context_window=1_000,
        max_output_tokens=500,
        tools=False,
        reasoning=False,
        vision=False,
        input_cost_per_mtok=0.01,
        output_cost_per_mtok=0.01,
        role_scores={"planner": 0.99, "supervisor": 0.99, "worker": 0.99, "utility": 0.99},
        task_scores={"coding": 0.99, "web-build": 0.99},
    )
    from machina.core.routing.catalog import ModelInventory

    inventory = ModelInventory(
        profiles=(*inventory.profiles, limited),
        provider_status=inventory.provider_status,
        warnings=(),
        current=inventory.current,
    )
    decision = route_task(
        "Implement and test a multi-file Python service",
        inventory=inventory,
        config=_config(orchestration_mode="multi_model"),
        mode="multi_model",
    )

    assert decision.planner.profile.model != "tiny-no-tools"
    assert decision.task.needs_tools is True
    assert all("tiny-no-tools" not in route.profile.model for route in decision.workers)


def test_no_eligible_route_raises_instead_of_silently_degrading() -> None:
    from machina.core.routing.catalog import ModelInventory

    inventory = ModelInventory(profiles=(), provider_status={}, warnings=())
    try:
        route_task(
            "Implement a multi-file service",
            inventory=inventory,
            config=_config(openai_api_key=""),
            mode="multi_model",
            session_provider="",
            session_model="",
        )
    except RoutingError as error:
        assert "no connected model" in str(error)
    else:
        raise AssertionError("expected RoutingError")


def test_parallel_intent_is_explicit_and_deterministic() -> None:
    assert detect_parallel_intent("Build the frontend, backend, and tests in parallel")
    assert not detect_parallel_intent("Build the frontend and backend")
    profile = build_task_profile("Build the frontend, backend, and tests in parallel")
    assert profile.parallel is True
    assert profile.task_class in {"implement", "ambiguous"}


def test_route_decision_serializes_candidates_and_fallbacks() -> None:
    decision = route_task(
        "Refactor the parser and run tests",
        inventory=_inventory(),
        config=_config(orchestration_mode="multi_model"),
        mode="multi_model",
        worker_count=2,
    )

    payload = decision.to_dict()
    assert payload["planner"]["provider"]
    assert payload["workers"]
    assert payload["candidates"]["planner"]
    assert payload["fallbacks"]["worker"] is not None
    assert payload["task"]["cost_tier"] == "balanced"
