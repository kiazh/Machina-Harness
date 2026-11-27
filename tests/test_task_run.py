"""Deterministic TaskRun phase and budget behavior."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from machina.core.task_run import (
    EXPLORATION_DELEGATION_DIRECTIVE,
    EXPLORATION_FALLBACK_DIRECTIVE,
    EXPLORATION_FINAL_DIRECTIVE,
    EXPLORATION_FINAL_TEXT,
    EXPLORATION_LISTING_NUDGE,
    TASK_RUN_ARTIFACT_NUDGE,
    TASK_RUN_EDIT_NUDGE,
    TASK_RUN_EXPLORATION_LIMIT_TEXT,
    TASK_RUN_FINALIZE_NUDGE,
    TASK_RUN_FORCE_EDIT_NUDGE,
    TASK_RUN_JOB_NUDGE,
    TASK_RUN_NETWORK_NUDGE,
    TASK_RUN_RECALL_LIMIT_TEXT,
    TASK_RUN_REPEAT_ACTION_TEXT,
    TASK_RUN_REPEAT_TEXT,
    TASK_RUN_TOOL_LIMIT_TEXT,
    TASK_RUN_TURN_LIMIT_TEXT,
    ExplorationPolicy,
    TaskRunPolicy,
    repair_network_blocked,
    set_repair_network_blocked,
)

REPAIR = "Fix the failing test in the repository."
CHAT = "Explain how the repository is organized."
EXPORT = (
    "can you learn the entire directoyr line line get a holistic full view of the "
    "entire project and understadn what everything does use as many subagents as "
    "you need to explore"
)


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


def _successful_terminal(command: str, workdir: str = "C:/work") -> dict:
    return {
        "tool": "terminal",
        "args": {"command": command, "workdir": workdir},
        "outcome": "completed",
        "returncode": 0,
        "result_preview": "listed",
    }


def test_repeated_successful_terminal_action_stops_even_for_chat() -> None:
    policy = TaskRunPolicy(CHAT)
    for _ in range(3):
        policy.observe(
            [_successful_terminal("python -c \"import os; print(os.listdir('C:/work/src'))\"")]
        )

    assert policy.repeated_action is True
    assert policy.stop_reason() == TASK_RUN_REPEAT_ACTION_TEXT


def test_cycling_terminal_listings_still_hit_the_repeat_guard() -> None:
    policy = TaskRunPolicy(CHAT)
    for _ in range(3):
        for path in ("src", "gui", "tests"):
            policy.observe(
                [
                    _successful_terminal(
                        f"python -c \"import os; print(os.listdir('C:/work/{path}'))\""
                    )
                ]
            )

    assert policy.repeated_action is True
    assert policy.stop_reason() == TASK_RUN_REPEAT_ACTION_TEXT


def test_directory_exploration_budget_stops_a_listing_loop() -> None:
    policy = TaskRunPolicy(CHAT)
    for index in range(10):
        policy.observe(
            [
                _successful_terminal(
                    f"python -c \"import os; print(os.listdir('C:/work/d{index}'))\""
                )
            ]
        )

    assert policy.exploration_calls == 10
    assert policy.exploration_loop is True
    assert policy.stop_reason() == TASK_RUN_EXPLORATION_LIMIT_TEXT


def test_read_only_file_exploration_remains_unbounded() -> None:
    policy = TaskRunPolicy(CHAT)
    read = {"tool": "read_file", "args": {"path": "app.py"}, "outcome": "completed"}
    for _ in range(12):
        policy.observe([read])

    assert policy.repeated_action is False
    assert policy.stop_reason() is None


def test_reads_between_directory_listings_reset_the_exploration_budget() -> None:
    policy = TaskRunPolicy(CHAT)
    read = {"tool": "read_file", "args": {"path": "app.py"}, "outcome": "completed"}
    for index in range(20):
        policy.observe([_successful_terminal(f"ls dir-{index}")])
        policy.observe([read])

    assert policy.exploration_calls == 0
    assert policy.exploration_loop is False
    assert policy.stop_reason() is None


def test_an_edit_between_directory_listings_resets_the_exploration_budget() -> None:
    policy = TaskRunPolicy(CHAT)
    for _ in range(20):
        for _ in range(9):
            policy.observe([_successful_terminal("find . -maxdepth 2")])
        policy.observe([{"tool": "write_file", "args": {"path": "app.py"}, "outcome": "completed"}])

    assert policy.exploration_calls == 0
    assert policy.exploration_loop is False


def test_repo_wide_exploration_can_list_beyond_the_normal_budget() -> None:
    prompt = (
        "Learn the entire directory line by line, get a holistic full view of "
        "the entire project, and understand what everything does. Use as many "
        "subagents as needed to explore."
    )
    policy = TaskRunPolicy(prompt)
    for index in range(30):
        policy.observe([_successful_terminal(f"ls dir-{index}")])

    assert policy.repo_wide_exploration is True
    assert policy.max_exploration_calls > 30
    assert policy.exploration_calls == 30
    assert policy.exploration_loop is False
    assert policy.stop_reason() is None


def test_ordinary_repair_turn_keeps_the_normal_exploration_budget() -> None:
    policy = TaskRunPolicy(REPAIR)
    for index in range(10):
        policy.observe([_successful_terminal(f"ls dir-{index}")])

    assert policy.repo_wide_exploration is False
    assert policy.exploration_loop is True
    assert policy.stop_reason() == TASK_RUN_EXPLORATION_LIMIT_TEXT


def test_repo_wide_exploration_still_stops_a_repeated_action() -> None:
    policy = TaskRunPolicy("Learn the whole project and read every file.")
    for _ in range(3):
        policy.observe([_successful_terminal("ls src")])

    assert policy.repeated_action is True
    assert policy.stop_reason() == TASK_RUN_REPEAT_ACTION_TEXT


def test_a_real_read_clears_a_latched_exploration_stop() -> None:
    policy = TaskRunPolicy(CHAT)
    for index in range(10):
        policy.observe([_successful_terminal(f"ls dir-{index}")])
    assert policy.exploration_loop is True

    policy.observe([{"tool": "read_file", "args": {"path": "app.py"}, "outcome": "completed"}])

    assert policy.exploration_calls == 0
    assert policy.exploration_loop is False
    assert policy.stop_reason() is None


def test_terminal_reads_reset_the_budget_without_counting_as_listings() -> None:
    policy = TaskRunPolicy(CHAT)
    for index in range(9):
        policy.observe([_successful_terminal(f"ls dir-{index}")])
    policy.observe([_successful_terminal("sed -n '1,80p' src/dir/app.py")])

    assert policy.exploration_calls == 0
    assert policy.exploration_loop is False

    for index in range(9, 18):
        policy.observe([_successful_terminal(f"ls dir-{index}")])

    assert policy.exploration_calls == 9
    assert policy.stop_reason() is None


def test_edit_file_resets_exploration_and_counts_as_a_source_edit() -> None:
    policy = TaskRunPolicy(REPAIR)
    for index in range(9):
        policy.observe([_successful_terminal(f"ls dir-{index}")])
    policy.observe(
        [
            {
                "tool": "edit_file",
                "args": {"path": "src/app.py", "old_text": "before", "new_text": "after"},
                "outcome": "completed",
            }
        ]
    )

    assert policy.exploration_calls == 0
    assert policy.exploration_loop is False
    assert policy.edited is True
    assert policy.source_edited is True
    assert policy.stop_reason() is None


def test_repeated_identical_search_is_guarded() -> None:
    policy = TaskRunPolicy(CHAT)
    search = {
        "tool": "search_files",
        "args": {"pattern": "TODO", "path": "."},
        "outcome": "completed",
    }
    for _ in range(3):
        policy.observe([search])

    assert policy.repeated_action is True
    assert policy.stop_reason() == TASK_RUN_REPEAT_ACTION_TEXT


def test_an_edit_resets_the_action_repeat_counter() -> None:
    policy = TaskRunPolicy(CHAT)
    command = _successful_terminal("python -c \"print('probe')\"")
    for _ in range(2):
        policy.observe([command])
    policy.observe(
        [
            {
                "tool": "write_file",
                "args": {"path": "app.py", "content": "changed"},
                "outcome": "completed",
            }
        ]
    )
    for _ in range(2):
        policy.observe([command])

    assert policy.repeated_action is False
    assert policy.stop_reason() is None


def test_recall_budget_stops_a_recall_loop() -> None:
    policy = TaskRunPolicy(CHAT)
    for index in range(12):
        policy.observe(
            [
                {
                    "tool": "session_recall",
                    "args": {"query": f"past evidence {index}"},
                    "outcome": "completed",
                }
            ]
        )

    assert policy.recall_calls == 12
    assert policy.recall_loop is True
    assert policy.stop_reason() == TASK_RUN_RECALL_LIMIT_TEXT


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


def test_artifact_paging_nudge_also_applies_outside_repair_tasks() -> None:
    policy = TaskRunPolicy("Build a portfolio website")
    for offset in range(3):
        policy.observe(
            [
                {
                    "tool": "artifact_read",
                    "args": {"artifact_id": "artifact-1", "offset": offset},
                    "outcome": "completed",
                }
            ]
        )

    assert policy.active is False
    assert policy.take_nudge() == TASK_RUN_ARTIFACT_NUDGE
    assert policy.take_nudge() is None


def test_edit_suppresses_the_no_edit_nudge() -> None:
    policy = TaskRunPolicy(REPAIR, edit_nudge_turns=2)
    policy.observe([{"tool": "write_file", "args": {"path": "a.py"}, "outcome": "completed"}])
    policy.observe([{"tool": "read_file", "args": {"path": "a.py"}, "outcome": "completed"}])

    assert policy.take_nudge() is None
    assert policy.summary()["edited"] is True


def test_network_attempts_nudge_without_aborting_repair() -> None:
    policy = TaskRunPolicy(REPAIR)
    denied = {
        "tool": "terminal",
        "args": {"command": "curl https://example.invalid/source"},
        "outcome": "failed",
        "result_preview": "Error: Network access is disabled during a repair turn.",
    }
    policy.observe([denied])
    assert policy.take_nudge() == TASK_RUN_NETWORK_NUDGE
    policy.observe([denied])
    assert policy.stop_reason() is None
    assert policy.network_attempts == 2


def test_investigation_without_edit_forces_the_write_surface() -> None:
    def tool(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name)

    policy = TaskRunPolicy(REPAIR, edit_nudge_turns=1, force_edit_turns=2)
    read = {"tool": "read_file", "args": {"path": "app.py"}, "outcome": "completed"}
    tools = [tool("read_file"), tool("terminal"), tool("write_file")]
    policy.observe([read])
    policy.observe([read])

    assert policy.force_edit is True
    assert [item.name for item in policy.filter_tool_defs(tools)] == ["write_file"]
    nudge = policy.take_nudge()
    assert nudge is not None and nudge.startswith(TASK_RUN_FORCE_EDIT_NUDGE)

    policy.observe(
        [
            {
                "tool": "write_file",
                "args": {"path": "src/_pytest/pathlib.py", "content": "fixed"},
                "outcome": "completed",
            }
        ]
    )
    assert policy.force_edit is False
    assert policy.source_edited is True


def test_force_edit_is_opt_in() -> None:
    policy = TaskRunPolicy(REPAIR, edit_nudge_turns=1)
    read = {"tool": "read_file", "args": {"path": "app.py"}, "outcome": "completed"}
    policy.observe([read])
    policy.observe([read])

    assert policy.force_edit is False
    nudge = policy.take_nudge()
    assert nudge is None or not nudge.startswith(TASK_RUN_FORCE_EDIT_NUDGE)


def test_non_source_probe_does_not_end_forced_edit() -> None:
    policy = TaskRunPolicy(REPAIR, edit_nudge_turns=1, force_edit_turns=2)
    read = {"tool": "read_file", "args": {"path": "app.py"}, "outcome": "completed"}
    policy.observe([read])
    policy.observe([read])
    policy.observe(
        [
            {
                "tool": "write_file",
                "args": {"path": "src/package/py.typed", "content": "marker"},
                "outcome": "completed",
            }
        ]
    )

    assert policy.source_edited is False
    assert policy.force_edit is True


def test_repair_network_policy_is_explicitly_scoped() -> None:
    set_repair_network_blocked(True)
    assert repair_network_blocked() is True
    set_repair_network_blocked(False)
    assert repair_network_blocked() is False


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


def test_lean_repair_toolset_keeps_explicit_collaboration_tools() -> None:
    tools = [
        SimpleNamespace(name="read_file"),
        SimpleNamespace(name="write_file"),
        SimpleNamespace(name="skill_read"),
        SimpleNamespace(name="delegate"),
        SimpleNamespace(name="web_search"),
    ]

    selected = TaskRunPolicy(
        "Fix the failing build and use skill_read plus delegate for a specialist."
    ).filter_tool_defs(tools)

    assert [item.name for item in selected] == ["read_file", "write_file", "skill_read", "delegate"]


def test_lean_repair_tools_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("MACHINA_REPAIR_LEAN_TOOLS", "0")
    tools = [SimpleNamespace(name="read_file"), SimpleNamespace(name="web_search")]
    assert TaskRunPolicy(REPAIR).filter_tool_defs(tools) == tools


def test_environment_limits_are_configurable(monkeypatch) -> None:
    monkeypatch.setenv("MACHINA_TASK_RUN_MAX_TURNS", "3")
    monkeypatch.setenv("MACHINA_TASK_RUN_MAX_TOOL_CALLS", "7")
    policy = TaskRunPolicy(REPAIR)

    assert policy.max_turns == 3
    assert policy.max_tool_calls == 7


@pytest.mark.asyncio
async def test_agent_stops_at_the_task_run_turn_limit(monkeypatch) -> None:
    from machina.core import agent as agent_module
    from machina.core import react_loop as loop_module
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

    monkeypatch.setattr(loop_module, "evaluate_request", gate)
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

    response = await agent_module.ReActAgent.run.__wrapped__(agent, session.id, REPAIR, False)

    assert response.outcome == "failed"
    assert "turn limit" in response.content.lower()
    agent._finish_turn_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_stops_a_repeated_successful_terminal_loop(monkeypatch) -> None:
    from machina.core import agent as agent_module
    from machina.core import react_loop as loop_module
    from machina.core.models import LLMResponse, Session

    session = Session(id=uuid4(), agent_id="harness")
    messages = [{"role": "user", "content": CHAT}]
    command = "python -c \"import os; print(os.listdir('C:/work/src'))\""

    async def gate(msgs, **kwargs):
        return SimpleNamespace(messages=msgs, failure=None)

    async def model(**kwargs):
        return LLMResponse(
            model="test",
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": command}),
                    },
                }
            ],
        )

    async def execute(response, msgs, made, *args, **kwargs):
        made.append(
            {
                "tool": "terminal",
                "args": {"command": command, "workdir": "C:/work"},
                "outcome": "completed",
                "returncode": 0,
                "result_preview": "listed",
            }
        )

    monkeypatch.setattr(loop_module, "evaluate_request", gate)
    agent = SimpleNamespace(
        agent_id="harness",
        provider=object(),
        _prepare_context=AsyncMock(return_value=(session, messages)),
        _flush_pending_chunks=AsyncMock(),
        _append_assistant_content=AsyncMock(),
        _condense_history=lambda _: None,
        _tool_defs=lambda: [],
        _call_llm_with_retry=model,
        _execute_tool_calls=execute,
        _finish_turn_completion=AsyncMock(),
        _schedule_memory_consolidation=lambda *args: None,
    )

    response = await agent_module.ReActAgent.run.__wrapped__(agent, session.id, CHAT, False)

    assert response.outcome == "failed"
    assert "repeated identical tool action" in response.content.lower()
    agent._finish_turn_completion.assert_not_awaited()


# ── Explicit whole-repository exploration policy ────────────────────────────


def test_export_prompt_is_repo_wide_exploration() -> None:
    policy = ExplorationPolicy(EXPORT)

    assert policy.active is True
    assert policy.preload_tools == ("delegate", "delegate_status")
    assert TaskRunPolicy(EXPORT).repo_wide_exploration is True


def test_ordinary_prompts_are_not_repo_wide_exploration() -> None:
    for prompt in (
        CHAT,
        "What does this function do?",
        "Fix the failing test in the repository.",
        "Implement the new endpoint and run the tests.",
        "explore the source tree",
    ):
        assert ExplorationPolicy(prompt).active is False


def test_repair_turns_never_activate_exploration_policy() -> None:
    repair = "Fix the bug after learning the entire project line by line."
    policy = ExplorationPolicy(repair)

    assert policy.active is False
    assert policy.preload_tools == ()
    assert policy.take_nudge() is None
    assert policy.stop_reason() is None


def test_exploration_policy_primes_delegate_on_the_first_step() -> None:
    from machina.core.agent import ReActAgent
    from machina.core.tool_projection import reset_dynamic_tool_state
    from machina.tools.base import registry
    from machina.tools.tool_loader import tool_load

    reset_dynamic_tool_state()
    agent = ReActAgent(provider=object(), dynamic_tools=True)
    assert "delegate" not in {tool.name for tool in agent._tool_defs()}

    policy = ExplorationPolicy(EXPORT)
    loaded, rejected = policy.prime_tools(set(registry._tools))

    assert loaded == ["delegate", "delegate_status"]
    assert rejected == []
    assert policy.tools_primed is True
    assert {"delegate", "delegate_status"} <= {tool.name for tool in agent._tool_defs()}
    # Already available on the next step, so a second load is a no-op.
    assert "Already available" in tool_load(["delegate"])
    reset_dynamic_tool_state()


def test_exploration_policy_injects_one_delegation_directive() -> None:
    policy = ExplorationPolicy(EXPORT)
    messages = [
        {"role": "system", "content": "Base prompt."},
        {"role": "user", "content": EXPORT},
    ]

    assert policy.inject_directive(messages) is True
    assert EXPLORATION_DELEGATION_DIRECTIVE in messages[0]["content"]
    assert messages[0]["content"].startswith("Base prompt.")
    assert policy.inject_directive(messages) is False
    assert messages[0]["content"].count(EXPLORATION_DELEGATION_DIRECTIVE) == 1


def test_exploration_policy_uses_fallback_when_delegate_is_not_visible() -> None:
    policy = ExplorationPolicy(EXPORT)
    messages = [{"role": "system", "content": "Base prompt."}]

    assert policy.inject_directive(messages, delegate_visible=False) is True
    assert EXPLORATION_FALLBACK_DIRECTIVE in messages[0]["content"]
    assert EXPLORATION_DELEGATION_DIRECTIVE not in messages[0]["content"]


def test_exploration_listing_cap_nudges_then_forces_a_report() -> None:
    policy = ExplorationPolicy(EXPORT)
    assert policy.max_listing_calls == 25

    for index in range(25):
        policy.observe([_successful_terminal(f"ls dir-{index}")])

    assert policy.take_nudge() == EXPLORATION_LISTING_NUDGE
    assert policy.take_nudge() is None
    assert policy.stop_reason() is None

    policy.observe([_successful_terminal("ls over-the-cap")])

    assert policy.force_answer is True
    # The next step is tool-free so the model can write the report.
    assert policy.stop_reason() is None
    messages = [{"role": "system", "content": "Base prompt."}]
    assert policy.inject_final_directive(messages) is True
    assert EXPLORATION_FINAL_DIRECTIVE in messages[0]["content"]
    assert policy.inject_final_directive(messages) is False

    policy.note_answer_step()
    assert policy.stop_reason() == EXPLORATION_FINAL_TEXT


def test_exploration_listing_budget_resets_after_reads_or_delegation() -> None:
    read = {"tool": "read_file", "args": {"path": "app.py"}, "outcome": "completed"}
    delegated = {
        "tool": "delegate",
        "args": {"agent": "researcher", "task": "Map the core runtime."},
        "outcome": "completed",
    }

    for progress in (read, delegated):
        policy = ExplorationPolicy(EXPORT)
        for index in range(20):
            policy.observe([_successful_terminal(f"ls dir-{index}")])
        policy.observe([progress])

        assert policy.listing_calls == 0
        assert policy.stop_reason() is None
    assert policy.delegated is True


def test_exploration_policy_never_touches_ordinary_or_repair_turns() -> None:
    for prompt in (CHAT, REPAIR):
        policy = ExplorationPolicy(prompt)
        for index in range(30):
            policy.observe([_successful_terminal(f"ls dir-{index}")])

        assert policy.active is False
        assert policy.take_nudge() is None
        assert policy.stop_reason() is None


def test_turn_state_checks_the_exploration_stop_after_the_repair_guards() -> None:
    from uuid import uuid4

    from machina.core.turn_state import TurnBudget, TurnState

    state = TurnState(
        session_id=uuid4(),
        messages=[],
        pending_chunks=[],
        task_run=TaskRunPolicy(EXPORT),
        exploration=ExplorationPolicy(EXPORT),
        budget=TurnBudget.for_turn(EXPORT),
    )
    for index in range(25):
        state.exploration.observe([_successful_terminal(f"ls dir-{index}")])
    assert state.exploration.take_nudge() == EXPLORATION_LISTING_NUDGE
    assert state.check_stop() is None

    state.exploration.observe([_successful_terminal("ls still-going")])
    assert state.exploration.force_answer is True
    assert state.check_stop() is None

    state.exploration.note_answer_step()
    stop = state.check_stop()

    assert stop is not None
    assert stop.text == EXPLORATION_FINAL_TEXT


@pytest.mark.asyncio
async def test_exploration_turn_advertises_delegate_and_injects_the_directive(monkeypatch) -> None:
    from machina.core import agent as agent_module
    from machina.core import react_loop as loop_module
    from machina.core.models import LLMResponse, Session

    session = Session(id=uuid4(), agent_id="harness")
    messages = [
        {"role": "system", "content": "Base prompt."},
        {"role": "user", "content": EXPORT},
    ]
    surfaces: list[list[str]] = []
    system_prompts: list[str] = []

    async def gate(msgs, **kwargs):
        surfaces.append([tool.name for tool in kwargs.get("tool_defs", [])])
        system_prompts.append(msgs[0]["content"])
        return SimpleNamespace(messages=msgs, failure=None)

    async def model(**_kwargs):
        return LLMResponse(model="test", content="Report.", tool_calls=[])

    monkeypatch.setattr(loop_module, "evaluate_request", gate)
    agent = SimpleNamespace(
        agent_id="harness",
        provider=object(),
        dynamic_tools=True,
        delegated=False,
        allowed_tools=None,
        _prepare_context=AsyncMock(return_value=(session, messages)),
        _flush_pending_chunks=AsyncMock(),
        _append_assistant_content=AsyncMock(),
        _condense_history=lambda *_args, **_kwargs: None,
        _call_llm_with_retry=model,
        _execute_tool_calls=AsyncMock(),
        _finish_turn_completion=AsyncMock(return_value=SimpleNamespace(content="Report.")),
        _schedule_memory_consolidation=lambda *_args: None,
    )
    agent._tool_defs = lambda: agent_module.ToolBatchExecutor(agent).definitions()

    await agent_module.ReActAgent.run.__wrapped__(agent, session.id, EXPORT, False)

    assert "delegate" in surfaces[0]
    assert "delegate_status" in surfaces[0]
    assert EXPLORATION_DELEGATION_DIRECTIVE in system_prompts[0]
    assert system_prompts[0].count(EXPLORATION_DELEGATION_DIRECTIVE) == 1
