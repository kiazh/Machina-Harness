"""TurnState/TurnBudget preserve every loop stop reason and counter transition."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

import pytest

from machina.core import turn_state as turn_state_module
from machina.core.repair_gate import REPAIR_DEADLINE_TEXT
from machina.core.task_run import (
    TASK_RUN_EXPLORATION_LIMIT_TEXT,
    TASK_RUN_RECALL_LIMIT_TEXT,
    TASK_RUN_REPEAT_ACTION_TEXT,
    TASK_RUN_REPEAT_TEXT,
    TASK_RUN_TOOL_LIMIT_TEXT,
    TASK_RUN_TURN_LIMIT_TEXT,
    TaskRunPolicy,
)
from machina.core.token_budget import (
    HIGH_STALE_CHARS,
    REPAIR_TOKEN_BUDGET_TEXT,
    SOFT_STALE_CHARS,
)
from machina.core.turn_state import (
    STOP_REPAIR_DEADLINE,
    STOP_REPAIR_TOKEN_BUDGET,
    STOP_TASK_RUN,
    TurnBudget,
    TurnState,
    TurnStop,
)

REPAIR = "Fix the failing test in the repository."
CHAT = "Explain how the repository is organized."


@pytest.fixture(autouse=True)
def _repair_deadline_setting(monkeypatch):
    # repair_deadline_exceeded re-reads this setting at call time (legacy
    # behavior); pin it so the deadline table is independent of the host env.
    monkeypatch.setenv("MACHINA_REPAIR_DEADLINE_SECONDS", "5")


def budget(prompt: str = REPAIR, *, deadline: float = 0.0, tokens: int = 0) -> TurnBudget:
    return TurnBudget(
        user_message=prompt,
        started=time.monotonic(),
        deadline_seconds=deadline,
        token_budget=tokens,
    )


def state(*, prompt: str = REPAIR, **policy: int) -> TurnState:
    return TurnState(
        session_id=uuid.uuid4(),
        messages=[],
        pending_chunks=[],
        task_run=TaskRunPolicy(prompt, **policy),
        budget=budget(prompt),
    )


def _failed() -> dict:
    return {
        "tool": "terminal",
        "args": {"command": "pytest -q"},
        "outcome": "failed",
        "returncode": 1,
        "result_preview": "1 failed",
    }


def _terminal(command: str = "python -c pass") -> dict:
    return {
        "tool": "terminal",
        "args": {"command": command},
        "outcome": "completed",
        "returncode": 0,
        "result_preview": "ok",
    }


def _observe(state_: TurnState, entry: dict, times: int = 1) -> TurnState:
    for _ in range(times):
        state_.task_run.observe([entry])
    return state_


# ── Stop reasons ───────────────────────────────────────────────────────────


def _deadline_stop() -> TurnState:
    state_ = state()
    state_.budget = TurnBudget(REPAIR, time.monotonic() - 10, 5, 0)
    state_.iteration = 60
    state_.task_run.max_turns = 1
    return state_


def _token_stop() -> TurnState:
    state_ = state()
    state_.budget = budget(tokens=100)
    state_.total_tokens = 100
    state_.iteration = 60
    state_.task_run.max_turns = 1
    return state_


def _turn_limit_stop() -> TurnState:
    state_ = state(max_turns=2, max_tool_calls=0)
    state_.iteration = 2
    return state_


def _tool_limit_stop() -> TurnState:
    state_ = state(max_turns=0, max_tool_calls=1)
    state_.tool_calls_made.append(_terminal())
    return state_


def _repeated_failure_stop() -> TurnState:
    return _observe(state(max_turns=0, max_tool_calls=0), _failed(), 3)


def _repeated_action_stop() -> TurnState:
    return _observe(state(prompt=CHAT), _terminal(), 3)


def _recall_stop() -> TurnState:
    return _observe(
        state(prompt=CHAT),
        {"tool": "session_recall", "args": {"query": "x"}, "outcome": "completed"},
        12,
    )


def _exploration_stop() -> TurnState:
    return _observe(
        state(prompt=CHAT),
        {"tool": "list_files", "args": {"path": "."}, "outcome": "completed"},
        10,
    )


@pytest.mark.parametrize(
    ("build", "kind", "text"),
    [
        (_deadline_stop, STOP_REPAIR_DEADLINE, REPAIR_DEADLINE_TEXT),
        (_token_stop, STOP_REPAIR_TOKEN_BUDGET, REPAIR_TOKEN_BUDGET_TEXT),
        (_turn_limit_stop, STOP_TASK_RUN, TASK_RUN_TURN_LIMIT_TEXT),
        (_tool_limit_stop, STOP_TASK_RUN, TASK_RUN_TOOL_LIMIT_TEXT),
        (_repeated_failure_stop, STOP_TASK_RUN, TASK_RUN_REPEAT_TEXT),
        (_repeated_action_stop, STOP_TASK_RUN, TASK_RUN_REPEAT_ACTION_TEXT),
        (_recall_stop, STOP_TASK_RUN, TASK_RUN_RECALL_LIMIT_TEXT),
        (_exploration_stop, STOP_TASK_RUN, TASK_RUN_EXPLORATION_LIMIT_TEXT),
    ],
)
def test_every_stop_reason_and_its_precedence_kind(
    build: Callable[[], TurnState], kind: str, text: str
) -> None:
    stop = build().check_stop()
    assert stop == TurnStop(kind=kind, text=text)


def test_check_stop_returns_none_when_no_rail_applies() -> None:
    idle = state(prompt=CHAT, max_turns=1, max_tool_calls=1)
    idle.iteration = 99
    idle.tool_calls_made.append(_terminal())
    assert idle.check_stop() is None


def test_check_stop_is_pure() -> None:
    state_ = _repeated_failure_stop()
    before = (
        state_.total_tokens,
        state_.iteration,
        len(state_.tool_calls_made),
        state_.stop,
    )
    first = state_.check_stop()
    second = state_.check_stop()
    assert first == second
    assert before == (
        state_.total_tokens,
        state_.iteration,
        len(state_.tool_calls_made),
        state_.stop,
    )


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (_deadline_stop, STOP_REPAIR_DEADLINE),
        (_token_stop, STOP_REPAIR_TOKEN_BUDGET),
        (_turn_limit_stop, STOP_TASK_RUN),
    ],
)
def test_mark_stop_records_the_terminal_kind(build: Callable[[], TurnState], expected: str) -> None:
    state_ = build()
    stop = state_.check_stop()
    assert stop is not None
    state_.mark_stop(stop)
    assert state_.stop == stop
    assert state_.stop is not None and state_.stop.kind == expected


# ── Counter transitions ────────────────────────────────────────────────────


def test_counter_transitions_are_exact() -> None:
    state_ = state()
    assert state_.total_tokens == state_.iteration == 0
    assert state_.repair_nudges == state_.verification_nudges == 0
    assert state_.empty_recoveries == 0
    assert state_.tool_calls_made == []
    assert state_.progress_history == []

    assert state_.record_usage({"total_tokens": 7}) == 7
    assert state_.total_tokens == 7
    assert state_.record_usage({}) == 0
    assert state_.total_tokens == 7
    assert state_.record_tokens(5) == 5
    assert state_.total_tokens == 12

    assert state_.advance_iteration() == 1
    assert state_.advance_iteration() == 2
    assert state_.iteration == 2

    assert state_.note_repair_nudge() == 1
    assert state_.note_repair_nudge() == 2
    assert state_.repair_nudges == 2
    assert state_.note_verification_nudge() == 1
    assert state_.verification_nudges == 1
    assert state_.note_empty_recovery() == 1
    assert state_.empty_recoveries == 1


def test_state_holds_the_loops_own_lists_without_copying() -> None:
    messages = [{"role": "user", "content": "hello"}]
    pending: list[dict] = []
    entries: list[dict] = []
    state_ = TurnState(
        session_id=uuid.uuid4(),
        messages=messages,
        pending_chunks=pending,
        task_run=TaskRunPolicy(CHAT),
        budget=budget(CHAT),
        tool_calls_made=entries,
    )

    messages.append({"role": "assistant", "content": "hi"})
    pending.append({"chunk_type": "user_message"})
    entries.append(_terminal())
    state_.messages.append({"role": "user", "content": "next"})

    assert len(messages) == 3
    assert len(pending) == 1
    assert len(entries) == 1
    assert state_.messages is messages
    assert state_.pending_chunks is pending
    assert state_.tool_calls_made is entries


# ── Budget decisions ───────────────────────────────────────────────────────


def test_for_turn_reads_each_setting_once(monkeypatch) -> None:
    monkeypatch.setattr(turn_state_module, "repair_deadline_seconds", lambda: 42.0)
    monkeypatch.setattr(turn_state_module, "repair_token_budget", lambda: 777)

    captured = TurnBudget.for_turn(REPAIR, now=123.0)
    assert captured.started == 123.0
    assert captured.deadline_seconds == 42.0
    assert captured.token_budget == 777

    monkeypatch.setattr(turn_state_module, "repair_deadline_seconds", lambda: 1.0)
    monkeypatch.setattr(turn_state_module, "repair_token_budget", lambda: 1)
    assert captured.deadline_seconds == 42.0
    assert captured.token_budget == 777


def test_deadline_decision_table(monkeypatch) -> None:
    monkeypatch.setenv("MACHINA_REPAIR_DEADLINE_SECONDS", "5")
    expired = TurnBudget(REPAIR, time.monotonic() - 10, 5, 0)
    fresh = TurnBudget(REPAIR, time.monotonic(), 5, 0)
    disabled = TurnBudget(REPAIR, time.monotonic() - 10, 0, 0)
    chat = TurnBudget(CHAT, time.monotonic() - 10, 5, 0)

    assert expired.deadline_exceeded() is True
    assert fresh.deadline_exceeded() is False
    assert disabled.deadline_exceeded() is False
    assert chat.deadline_exceeded() is False


def test_token_budget_decision_table() -> None:
    assert TurnBudget(REPAIR, 0.0, 0.0, 100).token_budget_exceeded(100) is True
    assert TurnBudget(REPAIR, 0.0, 0.0, 100).token_budget_exceeded(99) is False
    assert TurnBudget(REPAIR, 0.0, 0.0, 0).token_budget_exceeded(1_000_000) is False
    assert TurnBudget(CHAT, 0.0, 0.0, 100).token_budget_exceeded(1_000_000) is False


def test_stale_chars_decision_table() -> None:
    repair = TurnBudget(REPAIR, 0.0, 0.0, 1_000_000)
    assert repair.stale_chars(100_000) is None
    assert repair.stale_chars(750_000) == SOFT_STALE_CHARS
    assert repair.stale_chars(900_000) == HIGH_STALE_CHARS

    assert TurnBudget(CHAT, 0.0, 0.0, 1_000_000).stale_chars(900_000) is None
    assert TurnBudget(REPAIR, 0.0, 0.0, 0).stale_chars(900_000) is None
