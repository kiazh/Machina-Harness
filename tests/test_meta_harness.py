"""Deterministic meta-harness routing and approval-gated execution tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import machina.core.meta_harness as meta_harness
from machina import interop
from machina.core.meta_harness import harness_cards, route_prompt
from machina.gateway.errors import RpcError
from machina.gateway.features.meta_harness import meta_harnesses, meta_route
from machina.tools.base import registry
from machina.tools.meta_harness import harness_delegate


@pytest.fixture(autouse=True)
def _clear_router_cache():
    meta_harness.clear_availability_cache()
    yield
    meta_harness.clear_availability_cache()


def _availability(installed: tuple[str, ...] = ("opencode", "codex", "hermes")):
    return {
        key: {
            "installed": key in installed,
            "registered": None,
            "run_supported": key in installed,
        }
        for key in ("opencode", "codex", "hermes")
    }


def _enable_all(monkeypatch: pytest.MonkeyPatch, installed=("opencode", "codex", "hermes")):
    rows = _availability(installed)
    monkeypatch.setattr(meta_harness, "_platform_availability", lambda refresh=False: rows)
    return rows


def test_coding_prompt_routes_to_an_installed_coding_harness(monkeypatch) -> None:
    _enable_all(monkeypatch)

    route = route_prompt("Fix the failing pytest in src/router.py and add a regression test")

    assert route.mode == "external"
    assert route.primary in {"opencode", "codex"}
    assert route.confidence > 0.4
    assert route.steps[0].family == "coding"
    assert route.candidates[0].score >= route.candidates[1].score
    assert "harness_delegate" in route.prompt_hint()
    assert route.local_only is True


def test_research_prompt_routes_to_hermes(monkeypatch) -> None:
    _enable_all(monkeypatch)

    route = route_prompt("Research the latest Kiro pricing and summarize the sources")

    assert route.mode == "external"
    assert route.primary == "hermes"
    assert "hermes" in route.prompt_hint().lower()


def test_multi_intent_prompt_keeps_separate_harness_steps(monkeypatch) -> None:
    _enable_all(monkeypatch)

    route = route_prompt(
        "Research the latest Kiro pricing; then implement a comparison page in React"
    )

    assert route.mode == "external"
    assert len(route.steps) == 2
    assert [step.family for step in route.steps] == ["research", "coding"]
    assert [step.primary for step in route.steps] == ["hermes", "opencode"]
    assert "research -> hermes" in route.prompt_hint()


def test_no_installed_harness_stays_internal(monkeypatch) -> None:
    _enable_all(monkeypatch, installed=())

    route = route_prompt("Fix the failing test and run the suite")

    assert route.mode == "internal"
    assert route.primary == "machina"
    assert route.prompt_hint() == ""


def test_conversational_prompt_stays_internal(monkeypatch) -> None:
    _enable_all(monkeypatch)

    route = route_prompt("hello")

    assert route.mode == "internal"
    assert route.task_class == "conversational"
    assert route.prompt_hint() == ""


def test_ambiguous_coding_cues_fill_the_descriptor(monkeypatch) -> None:
    _enable_all(monkeypatch)

    route = route_prompt("Add pagination to the sessions endpoint")

    assert route.mode == "external"
    assert route.primary in {"opencode", "codex"}
    assert route.steps[0].family == "coding"


def test_documentation_cue_routes_to_hermes_even_with_code_nouns(monkeypatch) -> None:
    _enable_all(monkeypatch)

    route = route_prompt("Update the API docs for the new endpoint")

    assert route.mode == "external"
    assert route.primary == "hermes"
    assert route.steps[0].family == "research"


def test_informational_turn_does_not_hand_error_text_to_a_coder(monkeypatch) -> None:
    _enable_all(monkeypatch)

    route = route_prompt("Summarize this error message: connection refused")

    assert route.mode == "internal"
    assert route.primary == "machina"


def test_short_second_intent_stays_separate(monkeypatch) -> None:
    _enable_all(monkeypatch)

    route = route_prompt("Review the API and then update the docs")

    assert route.mode == "external"
    assert len(route.steps) == 2
    assert route.steps[1].family == "research"
    assert route.steps[1].primary == "hermes"


def test_route_is_deterministic(monkeypatch) -> None:
    _enable_all(monkeypatch)

    first = route_prompt("Refactor the workspace service and run tests").to_dict()
    second = route_prompt("Refactor the workspace service and run tests").to_dict()

    assert first == second


def test_harness_cards_expose_spawn_free_availability(monkeypatch) -> None:
    _enable_all(monkeypatch, installed=("opencode",))

    cards = {card.name: card for card in harness_cards()}

    assert cards["machina"].available is True
    assert cards["opencode"].available is True
    assert cards["codex"].available is False
    assert cards["codex"].installed is False


@pytest.mark.asyncio
async def test_harness_delegate_routes_and_runs_one_task(monkeypatch, tmp_path: Path) -> None:
    _enable_all(monkeypatch)
    seen: dict = {}

    def fake_run(platform: str, task: str, *, timeout: float, cwd):
        seen.update(platform=platform, task=task, timeout=timeout, cwd=cwd)
        return interop.RunResult(
            platform=platform,
            command=["opencode", "run"],
            exit_code=0,
            stdout="done",
            stderr="",
            ok=True,
        )

    monkeypatch.setattr("machina.tools.meta_harness.interop.run_task", fake_run)
    monkeypatch.setattr(
        "machina.tools.meta_harness.active_workspace_service",
        lambda: SimpleNamespace(identity=SimpleNamespace(resolved_root=str(tmp_path))),
    )

    payload = await harness_delegate("Fix the failing test", timeout=30)

    assert seen["platform"] in {"opencode", "codex"}
    assert seen["timeout"] == 30.0
    assert seen["cwd"] == tmp_path
    assert '"ok":true' in payload
    assert '"routed":true' in payload


@pytest.mark.asyncio
async def test_harness_delegate_rejects_unsupported_platform() -> None:
    with pytest.raises(Exception, match="unsupported harness"):
        await harness_delegate("do the task", platform="not-a-harness")


def test_harness_delegate_is_registered_with_execution_effects() -> None:
    tool = registry._tools["harness_delegate"]

    assert tool.effects == ("exec", "delegate")


def test_meta_route_prompt_section_is_optional_and_byte_identical(monkeypatch) -> None:
    import machina.core.prompt as prompt

    monkeypatch.setattr(prompt, "build_persona_section", lambda *a, **k: ("system", "persona"))
    monkeypatch.setattr(
        prompt, "build_capability_section", lambda *a, **k: ("system", "capability")
    )
    monkeypatch.setattr(prompt, "build_skill_section", lambda *a, **k: ("system", "skills"))
    monkeypatch.setattr(prompt, "output_policy_section", lambda: "")
    session = SimpleNamespace(context_budget=8000)

    base = prompt.build_system_prompt(
        None,
        None,
        session,
        "hello",
        [],
        None,
        "skipped",
        {},
        [],
        set(),
    )
    routed = prompt.build_system_prompt(
        None,
        None,
        session,
        "hello",
        [],
        None,
        "skipped",
        {},
        [],
        set(),
        meta_route="Meta-harness route: test",
    )

    assert base == "personacapabilityskills"
    assert routed == base + "\n\nMeta-harness route: test"


@pytest.mark.asyncio
async def test_route_rpc_is_call_free_and_rejects_blank_prompts(monkeypatch) -> None:
    _enable_all(monkeypatch)

    result = await meta_route(object(), {"prompt": "Fix the failing test"})
    assert result["route"]["mode"] == "external"
    assert result["route"]["primary"] in {"opencode", "codex"}

    with pytest.raises(RpcError, match="non-empty"):
        await meta_route(object(), {"prompt": "   "})

    listed = await meta_harnesses(object(), {})
    assert {row["name"] for row in listed["harnesses"]} >= {"machina", "opencode"}
