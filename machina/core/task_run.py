"""Deterministic phase controller for repository-repair turns.

The repair protocol is advisory prompt text. This controller supplies the
authoritative part: it counts turns and tool calls, notices repeated failed
actions, tracks whether the run has edited source and verified it, and emits
one bounded nudge when the model has spent too long without editing.

The controller is intentionally conservative. It does not force a tool or
rewrite the model's plan; it stops runaway loops and keeps the final evidence
gate honest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from machina.core.repair_gate import has_verification_evidence, is_repair_task

DEFAULT_MAX_TURNS = 40
DEFAULT_MAX_TOOL_CALLS = 120
DEFAULT_MAX_REPEATS = 3
DEFAULT_EDIT_NUDGE_TURNS = 10
MAX_TURNS_ENV = "MACHINA_TASK_RUN_MAX_TURNS"
MAX_TOOL_CALLS_ENV = "MACHINA_TASK_RUN_MAX_TOOL_CALLS"

TASK_RUN_TURN_LIMIT_TEXT = (
    "Task-run turn limit reached. The turn stopped to prevent a runaway loop; "
    "the current working-tree changes are preserved for verification."
)
TASK_RUN_TOOL_LIMIT_TEXT = (
    "Task-run tool-call limit reached. The turn stopped to prevent a runaway loop; "
    "the current working-tree changes are preserved for verification."
)
TASK_RUN_REPEAT_TEXT = (
    "Repeated identical failed action detected. The turn stopped after the same "
    "failure repeated; change approach before any further run."
)
TASK_RUN_EDIT_NUDGE = (
    "No source edit has been made after a substantial investigation. Stop widening "
    "the search: use the failure evidence you already have, make the smallest "
    "source-only change, and then run the narrowest relevant verification."
)

_EDIT_COMMAND_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:"
    r"apply_patch|git\s+apply|patch\s+-p|sed\s+-i|perl\s+-pi|"
    r"tee\s+|cat\s*>|python(?:3)?\s+-\s*<<|"
    r"python(?:3)?\s+-c\s+.*(?:write_text|write_bytes|open\()"
    r")",
    re.IGNORECASE,
)
_VERIFY_COMMAND_RE = re.compile(
    r"\b(?:pytest|py\.test|unittest|nox|tox|npm\s+(?:run\s+)?test|"
    r"pnpm\s+test|yarn\s+test|cargo\s+test|go\s+test|ruff|mypy|tsc|eslint)\b",
    re.IGNORECASE,
)


def _bounded_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    return max(0, min(value, 100_000))


def configured_max_turns() -> int:
    return _bounded_env_int(MAX_TURNS_ENV, DEFAULT_MAX_TURNS)


def configured_max_tool_calls() -> int:
    return _bounded_env_int(MAX_TOOL_CALLS_ENV, DEFAULT_MAX_TOOL_CALLS)


def _command(entry: dict[str, Any]) -> str:
    args = entry.get("args")
    if not isinstance(args, dict):
        return ""
    command = args.get("command")
    return command if isinstance(command, str) else ""


def _is_edit(entry: dict[str, Any]) -> bool:
    if entry.get("tool") == "write_file":
        return True
    return bool(_EDIT_COMMAND_RE.search(_command(entry)))


def _is_verification(entry: dict[str, Any]) -> bool:
    return bool(_VERIFY_COMMAND_RE.search(_command(entry)))


def _failed(entry: dict[str, Any]) -> bool:
    returncode = entry.get("returncode")
    return entry.get("outcome") in {"failed", "denied"} or (
        isinstance(returncode, int) and returncode != 0
    )


def _fingerprint(entry: dict[str, Any]) -> str:
    payload = {
        "tool": entry.get("tool"),
        "args": entry.get("args"),
        "returncode": entry.get("returncode"),
        "preview": entry.get("result_preview"),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TaskRunPolicy:
    """Phase and budget state for one repair turn."""

    def __init__(
        self,
        user_message: str,
        *,
        max_turns: int | None = None,
        max_tool_calls: int | None = None,
        max_repeats: int = DEFAULT_MAX_REPEATS,
        edit_nudge_turns: int = DEFAULT_EDIT_NUDGE_TURNS,
    ) -> None:
        self.active = is_repair_task(user_message)
        self.max_turns = (
            configured_max_turns() if max_turns is None else max(0, max_turns)
        )
        self.max_tool_calls = (
            configured_max_tool_calls()
            if max_tool_calls is None
            else max(0, max_tool_calls)
        )
        self.max_repeats = max(1, max_repeats)
        self.edit_nudge_turns = max(1, edit_nudge_turns)
        self.turns = 0
        self.tool_calls = 0
        self.edited = False
        self.reproduced = False
        self.verified = False
        self.turns_without_edit = 0
        self.repeated_failure = False
        self._failure_counts: dict[str, int] = {}
        self._edit_nudged = False

    @property
    def phase(self) -> str:
        if self.verified:
            return "verify"
        if self.edited:
            return "edit"
        if self.reproduced:
            return "reproduce"
        return "orient"

    def observe(self, batch: list[dict[str, Any]] | None) -> None:
        """Fold one completed tool batch into controller state."""
        if not self.active:
            return
        self.turns += 1
        edited_this_turn = False
        for entry in batch or []:
            if not isinstance(entry, dict):
                continue
            self.tool_calls += 1
            if _is_edit(entry):
                edited_this_turn = True
            if _is_verification(entry):
                self.reproduced = True
                if has_verification_evidence([entry]):
                    self.verified = True
            if _failed(entry):
                digest = _fingerprint(entry)
                count = self._failure_counts.get(digest, 0) + 1
                self._failure_counts[digest] = count
                if count >= self.max_repeats:
                    self.repeated_failure = True

        if edited_this_turn:
            self.edited = True
            self.turns_without_edit = 0
        else:
            self.turns_without_edit += 1

    def stop_reason(
        self,
        *,
        turns: int | None = None,
        tool_calls: int | None = None,
    ) -> str | None:
        """Return a terminal reason, or ``None`` when the run may continue."""
        if not self.active:
            return None
        current_turns = self.turns if turns is None else max(0, turns)
        current_tools = self.tool_calls if tool_calls is None else max(0, tool_calls)
        if self.repeated_failure:
            return TASK_RUN_REPEAT_TEXT
        if self.max_turns > 0 and current_turns >= self.max_turns:
            return TASK_RUN_TURN_LIMIT_TEXT
        if self.max_tool_calls > 0 and current_tools >= self.max_tool_calls:
            return TASK_RUN_TOOL_LIMIT_TEXT
        return None

    def take_nudge(self) -> str | None:
        """Return the one bounded no-edit nudge, if it is due."""
        if not self.active or self._edit_nudged:
            return None
        if self.edited or self.turns_without_edit < self.edit_nudge_turns:
            return None
        self._edit_nudged = True
        return TASK_RUN_EDIT_NUDGE

    def summary(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "phase": self.phase,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "edited": self.edited,
            "reproduced": self.reproduced,
            "verified": self.verified,
            "turns_without_edit": self.turns_without_edit,
            "repeated_failure": self.repeated_failure,
        }
