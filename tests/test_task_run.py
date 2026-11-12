"""Deterministic TaskRun phase and budget behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from machina.core.task_run import (
    TASK_RUN_ARTIFACT_NUDGE,
    TASK_RUN_EDIT_NUDGE,
    TASK_RUN_FINALIZE_NUDGE,
    TASK_RUN_JOB_NUDGE,
    TASK_RUN_REPEAT_TEXT,
    TASK_RUN_TOOL_LIMIT_TEXT,
    TASK_RUN_TURN_LIMIT_TEXT,
    TaskRunPolicy,
)

REPAIR = "Fix the failing test in the repository."
CHAT = "Explain how the repository is organized."


def _failed(command: str) -> dict:
    return {
        "tool": "terminal",
        "args": {"command": command},
        "outcome": "failed",
        "returncode": 1,
        "result_preview": "1 failed",
    }


def test_chat_turns_are_not_controlled_or_mutated() -> None:
    policy = TaskRunPolicy(CHAT, max_turns=1, max_tool_calls=1)
    policy.observe([_failed("pytest -q")])

    assert policy.active is False
    assert policy.stop_reason(turns=99, tool_calls=99) is None
    assert policy.take_nudge() is None
    assert policy.phase == "orient"


def test_phases_follow_reproduction_edit_and_verification() -> None:
    policy = TaskRunPolicy(REPAIR, max_turns=20, max_tool_calls=20)

    assert policy.phase == "orient"
    policy.observe([_failed("pytest -q tests/test_app.py")])
    assert policy.reproduced is True
    assert policy.phase == "reproduce"

    policy.observe(
        [
            {
                "tool": "write_file",
                "args": {"path": "app.py", "content": "fixed"},
                "outcome": "completed",
            }
        ]
    )
    assert policy.edited is True
    assert policy.phase == "edit"

    policy.observe(
        [
            {
                "tool": "terminal",
                "args": {"command": "pytest -q tests/test_app.py"},
                "outcome": "completed",
                "returncode": 0,
                "result_preview": "1 passed",
            }
        ]
    )
    assert policy.verified is True
    assert policy.phase == "verify"


def test_repeated_identical_failure_stops_the_run() -> None:
    policy = TaskRunPolicy(REPAIR, max_turns=20, max_tool_calls=20)
    for _ in range(3):
        policy.observe([_failed("pytest -q")])

    assert policy.repeated_failure is True
    assert policy.stop_reason() == TASK_RUN_REPEAT_TEXT


def test_turn_and_tool_caps_are_independent() -> None:
    turns = TaskRunPolicy(REPAIR, max_turns=2, max_tool_calls=20)
    assert turns.stop_reason(turns=1, tool_calls=0) is None
    assert turns.stop_reason(turns=2, tool_calls=0) == TASK_RUN_TURN_LIMIT_TEXT

    tools = TaskRunPolicy(REPAIR, max_turns=20, max_tool_calls=2)
    assert tools.stop_reason(turns=0, tool_calls=1) is None
    assert tools.stop_reason(turns=0, tool_calls=2) == TASK_RUN_TOOL_LIMIT_TEXT


def test_no_edit_nudge_is_emitted_once() -> None:
    policy = TaskRunPolicy(REPAIR, edit_nudge_turns=2)
    read = {"tool": "read_file", "args": {"path": "app.py"}, "outcome": "completed"}

    policy.observe([read])
    assert policy.take_nudge() is None
    policy.observe([read])
    assert policy.take_nudge() == TASK_RUN_EDIT_NUDGE
    assert policy.take_nudge() is None


def test_successful_verification_gets_one_finalize_nudge() -> None:
    policy = TaskRunPolicy(REPAIR)
    policy.observe(
        [
            {
                "tool": "write_file",
                "args": {"path": "a.py"},
                "outcome": "completed",
            },
            {
                "tool": "terminal",
                "args": {"command": "pytest -q testing/test_a.py::test_a"},
                "outcome": "completed",
                "returncode": 0,
                "result_preview": "1 passed",
            },
        ]
    )

    assert policy.take_nudge() == TASK_RUN_FINALIZE_NUDGE
    assert policy.take_nudge() is None


def test_artifact_paging_and_job_polling_get_one_nudge() -> None:
    policy = TaskRunPolicy(REPAIR)
    for offset in range(6):
        policy.observe(
            [
                {
                    "tool": "artifact_read",
                    "args": {"artifact_id": "artifact-1", "offset": offset},
                    "outcome": "completed",
                }
            ]
        )
    assert policy.take_nudge() == TASK_RUN_ARTIFACT_NUDGE
    assert policy.take_nudge() is None

    jobs = TaskRunPolicy(REPAIR)
    for _ in range(6):
        jobs.observe(
            [
                {
                    "tool": "terminal_job",
                    "args": {"action": "wait"},
                    "outcome": "completed",
                }
            ]
        )
    assert jobs.take_nudge() == TASK_RUN_JOB_NUDGE
    assert jobs.take_nudge() is None


def test_edit_suppresses_the_no_edit_nudge() -> None:
    policy = TaskRunPolicy(REPAIR, edit_nudge_turns=2)
    policy.observe([{"tool": "write_file", "args": {"path": "a.py"}, "outcome": "completed"}])
    policy.observe([{"tool": "read_file", "args": {"path": "a.py"}, "outcome": "completed"}])

    assert policy.take_nudge() is None
    assert policy.summary()["edited"] is True


def test_repair_toolset_is_lean_and_fails_open() -> None:
    def tool(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name)

    tools = [tool("read_file"), tool("write_file"), tool("web_search"), tool("recall")]
    selected = TaskRunPolicy(REPAIR).filter_tool_defs(tools)
    assert [item.name for item in selected] == ["read_file", "write_file"]

    chat = TaskRunPolicy(CHAT)
    assert chat.filter_tool_defs(tools) == tools

    only_unknown = [tool("custom_tool")]
    assert TaskRunPolicy(REPAIR).filter_tool_defs(only_unknown) == only_unknown


def test_environment_limits_are_configurable(monkeypatch) -> None:
    monkeypatch.setenv("MACHINA_TASK_RUN_MAX_TURNS", "3")
    monkeypatch.setenv("MACHINA_TASK_RUN_MAX_TOOL_CALLS", "7")
    policy = TaskRunPolicy(REPAIR)

    assert policy.max_turns == 3
    assert policy.max_tool_calls == 7


@pytest.mark.asyncio
async def test_agent_stops_at_the_task_run_turn_limit(monkeypatch) -> None:
    from machina.core import agent as module
    from machina.core.models import LLMResponse, Session

    monkeypatch.setenv("MACHINA_TASK_RUN_MAX_TURNS", "1")
    session = Session(id=uuid4(), agent_id="harness")
    messages = [{"role": "user", "content": REPAIR}]

    async def gate(msgs, **kwargs):
        return SimpleNamespace(messages=msgs, failure=None)

    async def model(**kwargs):
        return LLMResponse(
            model="test",
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        )

    monkeypatch.setattr(module, "evaluate_request", gate)
    agent = SimpleNamespace(
        agent_id="harness",
        provider=object(),
        _prepare_context=AsyncMock(return_value=(session, messages)),
        _flush_pending_chunks=AsyncMock(),
        _append_assistant_content=AsyncMock(),
        _condense_history=lambda _: None,
        _tool_defs=lambda: [],
        _call_llm_with_retry=model,
        _execute_tool_calls=AsyncMock(),
        _finish_turn_completion=AsyncMock(),
        _schedule_memory_consolidation=lambda *args: None,
    )

    response = await module.ReActAgent.run.__wrapped__(
        agent, session.id, REPAIR, False
    )

    assert response.outcome == "failed"
    assert "turn limit" in response.content.lower()
    agent._finish_turn_completion.assert_not_awaited()
