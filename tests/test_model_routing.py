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
