"""Characterization traces for the ReAct turn loop (plan R0.2).

Each golden file records one scenario: the ordered loop events, the terminal
shape, provider attempts, gate decisions, tool-batch identity, persistence
flushes, and the counters the loop is allowed to consult. Content is hashed,
ids are normalized, and no wall-clock value is stored.

Run ``pytest tests/test_golden_turns.py`` normally. After a reviewed behavior
change, regenerate with ``pytest tests/test_golden_turns.py --update-golden``
and inspect the diff before committing it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from golden.harness import (
    GOLDEN_DIR,
    HangStream,
    ProviderHTTPError,
    Scenario,
    ToolBatch,
    ToolStep,
    TurnSpec,
    assert_no_raw_text,
    assert_trace_matches,
    canonical_trace,
    run_scenario,
)

from machina.core.models import AgentResponse, LLMResponse

REPAIR = "Fix the failing test in tests/test_math.py."
CHAT = "Explain the deployment flow."


def llm(
    content: str = "",
    *,
    tokens: int = 10,
    prompt: int = 6,
    completion: int = 4,
    tool_calls: tuple[dict[str, Any], ...] = (),
) -> LLMResponse:
    return LLMResponse(
        model="scripted-model",
        content=content,
        usage={
            "total_tokens": tokens,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
        },
        tool_calls=list(tool_calls),
    )


def call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, sort_keys=True)},
    }


def batch(*steps: ToolStep, order: tuple[int, ...] | None = None) -> ToolBatch:
    return ToolBatch(steps=tuple(steps), completion_order=order)


def scenario(name: str, *turns: TurnSpec) -> Scenario:
    return Scenario(name=name, turns=tuple(turns))


SCENARIOS: tuple[Scenario, ...] = (
    scenario(
        "plain-answer",
        TurnSpec(
            prompt=CHAT,
            stream=True,
            stream_steps=(
                llm("The deployment flow has three stages.", tokens=12, prompt=8, completion=4),
            ),
        ),
    ),
    scenario(
        "one-tool",
        TurnSpec(
            prompt="List the files in the workspace.",
            stream=True,
            stream_steps=(
                llm(tool_calls=(call("call-1", "terminal", {"command": "ls"}),)),
                llm("There are two files: a.py and b.py.", tokens=9),
            ),
            tool_batches=(batch(ToolStep("terminal", {"command": "ls"}, "a.py\nb.py")),),
        ),
    ),
    scenario(
        "concurrent-tools",
        TurnSpec(
            prompt="Read all three files.",
            stream=True,
            stream_steps=(
                llm(
                    tool_calls=(
                        call("call-1", "read_file", {"path": "a.py"}),
                        call("call-2", "read_file", {"path": "b.py"}),
                        call("call-3", "read_file", {"path": "c.py"}),
                    )
                ),
                llm("All three files were read.", tokens=9),
            ),
            tool_batches=(
                batch(
                    ToolStep("read_file", {"path": "a.py"}, "A"),
                    ToolStep("read_file", {"path": "b.py"}, "B"),
                    ToolStep("read_file", {"path": "c.py"}, "C"),
                    order=(2, 0, 1),
                ),
            ),
        ),
    ),
    scenario(
        "approval-pause-resume",
        TurnSpec(
            prompt="Run the deploy command.",
            stream=True,
            stream_steps=(llm(tool_calls=(call("call-1", "terminal", {"command": "deploy"}),)),),
            tool_batches=(
                batch(
                    ToolStep(
                        "terminal",
                        {"command": "deploy"},
                        "",
                        outcome="paused",
                        needs_approvals=(
                            {
                                "request_id": "req-1",
                                "operation": "tool.exec",
                                "target": "terminal",
                            },
                        ),
                    ),
                ),
            ),
        ),
        TurnSpec(
            prompt="The deploy action was approved; report the result.",
            stream=True,
            stream_steps=(llm("The approved action completed.", tokens=8),),
        ),
    ),
    scenario(
        "cancellation-mid-stream",
        TurnSpec(
            prompt=CHAT,
            stream=True,
            stream_steps=(HangStream("partial "),),
            cancel=True,
        ),
    ),
    scenario(
        "retry-502",
        TurnSpec(
            prompt=CHAT,
            stream=True,
            stream_steps=(
                ProviderHTTPError(502, "bad gateway"),
                llm("Recovered after transient failure.", tokens=11),
            ),
        ),
    ),
    scenario(
        "auth-401",
        TurnSpec(
            prompt=CHAT,
            stream=False,
            complete_steps=(ProviderHTTPError(401, "unauthorized"),),
        ),
    ),
    scenario(
        "empty-response-recovery",
        TurnSpec(
            prompt="Check the build status.",
            stream=False,
            complete_steps=(
                llm(tool_calls=(call("call-1", "terminal", {"command": "make status"}),)),
                llm("", tokens=5),
                llm("", tokens=7),
                llm("Recovered final summary.", tokens=9),
            ),
            tool_batches=(batch(ToolStep("terminal", {"command": "make status"}, "build ok")),),
        ),
    ),
    scenario(
        "repair-edit-verification",
        TurnSpec(
            prompt=REPAIR,
            stream=False,
            complete_steps=(
                llm(
                    tool_calls=(
                        call(
                            "call-1",
                            "write_file",
                            {"path": "machina/core/math.py", "content": "fixed"},
                        ),
                    )
                ),
                llm(
                    tool_calls=(
                        call(
                            "call-2",
                            "terminal",
                            {"command": "pytest -q tests/test_math.py"},
                        ),
                    )
                ),
                llm("Fixed the failing test; pytest passes.", tokens=9),
            ),
            tool_batches=(
                batch(
                    ToolStep(
                        "write_file",
                        {"path": "machina/core/math.py", "content": "fixed"},
                        "wrote machina/core/math.py",
                    )
                ),
                batch(
                    ToolStep(
                        "terminal",
                        {"command": "pytest -q tests/test_math.py"},
                        "1 passed",
                        returncode=0,
                    )
                ),
            ),
        ),
    ),
    scenario(
        "auto-parallel",
        TurnSpec(
            prompt="Work on these in parallel: docs and tests.",
            stream=True,
            auto_parallel=AgentResponse(content="Parallel synthesis complete.", tokens_used=42),
        ),
    ),
    scenario(
        "delegation",
        TurnSpec(
            prompt="Delegate the documentation review.",
            stream=True,
            tool_defs=("read_file", "write_file", "terminal", "delegate"),
            stream_steps=(
                llm(tool_calls=(call("call-1", "delegate", {"task": "Review the docs"}),)),
                llm("Documentation review complete.", tokens=5),
            ),
            tool_batches=(
                batch(
                    ToolStep(
                        "delegate",
                        {"task": "Review the docs"},
                        "Worker report: docs reviewed.",
                        delegated_tokens=13,
                    ),
                ),
            ),
        ),
    ),
    scenario(
        "budget-exceeded",
        TurnSpec(
            prompt=REPAIR,
            stream=False,
            token_budget=100,
            complete_steps=(
                llm(tool_calls=(call("call-1", "terminal", {"command": "pytest -q"}),), tokens=150),
            ),
            tool_batches=(
                batch(ToolStep("terminal", {"command": "pytest -q"}, "1 failed", returncode=1)),
            ),
        ),
    ),
)


@pytest.fixture(scope="session")
def update_golden(request) -> bool:
    return bool(request.config.getoption("--update-golden"))


def _golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


@pytest.mark.asyncio
@pytest.mark.parametrize("item", SCENARIOS, ids=[item.name for item in SCENARIOS])
async def test_golden_turn_trace(item: Scenario, monkeypatch, update_golden: bool) -> None:
    actual = await run_scenario(item, monkeypatch)
    assert_no_raw_text(actual)

    path = _golden_path(item.name)
    if update_golden:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_trace(actual), encoding="utf-8")
        return

    assert path.exists(), f"missing {path.name}; regenerate with --update-golden and review"
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert_trace_matches(actual, expected)


@pytest.mark.asyncio
async def test_every_scenario_has_a_golden_file() -> None:
    missing = [item.name for item in SCENARIOS if not _golden_path(item.name).exists()]
    assert missing == []


@pytest.mark.asyncio
async def test_traces_are_byte_stable_across_two_runs(monkeypatch) -> None:
    for item in SCENARIOS:
        first = canonical_trace(await run_scenario(item, monkeypatch))
        second = canonical_trace(await run_scenario(item, monkeypatch))
        assert first == second, f"{item.name} trace is not deterministic"


@pytest.mark.asyncio
async def test_reordered_events_fail_the_trace_comparison(monkeypatch) -> None:
    item = next(candidate for candidate in SCENARIOS if candidate.name == "plain-answer")
    trace = await run_scenario(item, monkeypatch)
    tampered = json.loads(canonical_trace(trace))
    events = tampered["turns"][0]["events"]
    assert len(events) >= 2
    events[0], events[1] = events[1], events[0]

    with pytest.raises(AssertionError):
        assert_trace_matches(tampered, trace)


def test_scenario_count_matches_the_plan() -> None:
    assert len(SCENARIOS) == 12
    assert len({item.name for item in SCENARIOS}) == 12
