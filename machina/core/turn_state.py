"""Per-turn state and budget decisions for the ReAct loop.

Interface
---------
``TurnBudget`` owns the three budget decisions the loop makes before each
model step:

- the repair wall-clock deadline (``deadline_exceeded``),
- the cumulative repair token budget (``token_budget_exceeded``),
- the stale-result size under token pressure (``stale_chars``).

``TurnBudget.for_turn`` reads each setting once when the turn starts, matching
the loop's previous capture-before-the-loop behavior.

``TurnState`` owns every counter and mutable reference the loop shares across
iterations: messages, pending persistence chunks, tokens, iteration, recorded
tool calls, the task-run policy, the separate whole-repository exploration
policy, the workspace snapshot, nudge/recovery counters, stagnation history,
and the recorded stop.

``TurnState.check_stop`` applies the loop's exact stop precedence: repair
deadline, then repair token budget, then the task-run policy. The caller logs
and returns the terminal; the check itself never mutates state.

Invariants
----------
- ``record_usage`` and ``record_tokens`` add exactly the supplied amount.
- ``advance_iteration`` increments by exactly one.
- ``check_stop`` does not mutate state or counters; it evaluates the same
  predicates in the same precedence the loop used.
- ``mark_stop`` stores the terminal stop for observers; the loop returns
  immediately after a stop, so no later mutation is expected.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from machina.core.repair_gate import (
    REPAIR_DEADLINE_TEXT,
    repair_deadline_exceeded,
    repair_deadline_seconds,
)
from machina.core.task_run import ExplorationPolicy, TaskRunPolicy
from machina.core.token_budget import (
    REPAIR_TOKEN_BUDGET_TEXT,
    repair_token_budget,
    repair_token_budget_exceeded,
    stale_tool_result_chars,
)

__all__ = [
    "STOP_REPAIR_DEADLINE",
    "STOP_REPAIR_TOKEN_BUDGET",
    "STOP_TASK_RUN",
    "TurnBudget",
    "TurnState",
    "TurnStop",
]

STOP_REPAIR_DEADLINE = "repair_deadline"
STOP_REPAIR_TOKEN_BUDGET = "repair_token_budget"
STOP_TASK_RUN = "task_run"


@dataclass(frozen=True)
class TurnBudget:
    """Budget settings and decisions for one turn.

    The numeric settings are captured at construction. ``deadline_exceeded``
    and ``token_budget_exceeded`` keep the exact helper semantics the loop
    used (including the deadline helper's own live env read); the captured
    values are what the loop audits and guards on.
    """

    user_message: str
    started: float
    deadline_seconds: float
    token_budget: int

    @classmethod
    def for_turn(cls, user_message: str, *, now: float | None = None) -> TurnBudget:
        """Read the turn's settings once, at turn start."""
        return cls(
            user_message=user_message,
            started=time.monotonic() if now is None else now,
            deadline_seconds=repair_deadline_seconds(),
            token_budget=repair_token_budget(),
        )

    def deadline_exceeded(self) -> bool:
        """Whether the enabled repair deadline has elapsed."""
        return self.deadline_seconds > 0 and repair_deadline_exceeded(
            self.started, self.user_message
        )

    def token_budget_exceeded(self, total_tokens: int) -> bool:
        """Whether the enabled cumulative repair budget is spent."""
        return self.token_budget > 0 and repair_token_budget_exceeded(
            total_tokens,
            self.user_message,
            budget=self.token_budget,
        )

    def stale_chars(self, total_tokens: int) -> int | None:
        """Stale-result preview size for the next request, or ``None``."""
        return stale_tool_result_chars(
            total_tokens,
            self.user_message,
            budget=self.token_budget,
        )


@dataclass(frozen=True)
class TurnStop:
    """One terminal stop selected by :meth:`TurnState.check_stop`."""

    kind: str
    text: str


@dataclass
class TurnState:
    """The shared state of one ReAct turn."""

    session_id: uuid.UUID
    messages: list[dict[str, Any]]
    pending_chunks: list[dict[str, Any]]
    task_run: TaskRunPolicy
    budget: TurnBudget
    exploration: ExplorationPolicy = field(default_factory=lambda: ExplorationPolicy(""))
    total_tokens: int = 0
    iteration: int = 0
    tool_calls_made: list[dict[str, Any]] = field(default_factory=list)
    turn_snapshot: Any = None
    repair_nudges: int = 0
    verification_nudges: int = 0
    empty_recoveries: int = 0
    progress_history: list[Any] = field(default_factory=list)
    stop: TurnStop | None = None

    def record_usage(self, usage: Mapping[str, int]) -> int:
        """Add one response's billed total; return the delta."""
        delta = usage.get("total_tokens", 0)
        self.total_tokens += delta
        return delta

    def record_tokens(self, tokens: int) -> int:
        """Add tokens spent outside the primary response (recovery/delegation)."""
        self.total_tokens += tokens
        return tokens

    def advance_iteration(self) -> int:
        """Count one completed model step."""
        self.iteration += 1
        return self.iteration

    def note_repair_nudge(self) -> int:
        self.repair_nudges += 1
        return self.repair_nudges

    def note_verification_nudge(self) -> int:
        self.verification_nudges += 1
        return self.verification_nudges

    def note_empty_recovery(self) -> int:
        self.empty_recoveries += 1
        return self.empty_recoveries

    def mark_stop(self, stop: TurnStop) -> None:
        """Record the selected terminal stop."""
        self.stop = stop

    def check_stop(self) -> TurnStop | None:
        """Apply the loop's deadline -> token -> task-run -> exploration precedence."""
        if self.budget.deadline_exceeded():
            return TurnStop(STOP_REPAIR_DEADLINE, REPAIR_DEADLINE_TEXT)
        if self.budget.token_budget_exceeded(self.total_tokens):
            return TurnStop(STOP_REPAIR_TOKEN_BUDGET, REPAIR_TOKEN_BUDGET_TEXT)
        reason = self.task_run.stop_reason(
            turns=self.iteration,
            tool_calls=len(self.tool_calls_made),
        )
        if reason is not None:
            return TurnStop(STOP_TASK_RUN, reason)
        reason = self.exploration.stop_reason()
        if reason is not None:
            return TurnStop(STOP_TASK_RUN, reason)
        return None
