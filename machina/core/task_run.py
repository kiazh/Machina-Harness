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

DEFAULT_MAX_TURNS = 60
DEFAULT_MAX_TOOL_CALLS = 150
DEFAULT_MAX_REPEATS = 3
DEFAULT_EDIT_NUDGE_TURNS = 10
DEFAULT_ARTIFACT_NUDGE_READS = 6
DEFAULT_JOB_NUDGE_POLLS = 6
MAX_TURNS_ENV = "MACHINA_TASK_RUN_MAX_TURNS"
MAX_TOOL_CALLS_ENV = "MACHINA_TASK_RUN_MAX_TOOL_CALLS"

# A repair turn should not spend context on web research, memory management,
# delegation, or skill installation unless the task explicitly needs them.
# Keeping this list small also keeps the tool schema out of every request.
REPAIR_TOOL_NAMES = frozenset(
    {
        "read_file",
        "write_file",
        "list_files",
        "search_files",
        "terminal",
        "terminal_job",
        "code_intelligence",
        "learn_directory",
        "artifact_read",
        "artifact_search",
        "artifact_describe",
    }
)

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
TASK_RUN_ARTIFACT_NUDGE = (
    "You are spending turns paging the same retained artifact. Stop paging: use "
    "the excerpt already visible, make the source change, and verify the behavior."
)
TASK_RUN_JOB_NUDGE = (
    "You are polling background jobs repeatedly. Wait for the relevant job once, "
    "then act on its result; do not busy-poll while other work remains."
)
TASK_RUN_FINALIZE_NUDGE = (
    "A source edit and a successful targeted verification are already present. "
    "Stop adding scope: report the final diff and the exact verification result now."
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
        self.artifact_reads = 0
        self.job_polls = 0
        self._artifact_counts: dict[str, int] = {}
        self._failure_counts: dict[str, int] = {}
        self._edit_nudged = False
        self._artifact_nudged = False
        self._job_nudged = False
        self._finalize_nudged = False

    def filter_tool_defs(self, tool_defs: list[Any]) -> list[Any]:
        """Return the lean repair toolset; fail open if nothing matches."""
        if not self.active:
            return tool_defs
        selected = [
            tool
            for tool in tool_defs
            if getattr(tool, "name", None) in REPAIR_TOOL_NAMES
        ]
        return selected or tool_defs

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
            if entry.get("tool") == "artifact_read":
                self.artifact_reads += 1
                args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
                handle = str(args.get("artifact_id") or "")
                if handle:
                    self._artifact_counts[handle] = (
                        self._artifact_counts.get(handle, 0) + 1
                    )
            elif entry.get("tool") == "terminal_job":
                self.job_polls += 1
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
        """Return one bounded process nudge, if a loop is becoming wasteful."""
        if not self.active:
            return None
        if (
            not self._artifact_nudged
            and max(self._artifact_counts.values(), default=0)
            >= DEFAULT_ARTIFACT_NUDGE_READS
        ):
            self._artifact_nudged = True
            return TASK_RUN_ARTIFACT_NUDGE
        if not self._job_nudged and self.job_polls >= DEFAULT_JOB_NUDGE_POLLS:
            self._job_nudged = True
            return TASK_RUN_JOB_NUDGE
        if (
            not self._finalize_nudged
            and self.edited
            and self.verified
        ):
            self._finalize_nudged = True
            return TASK_RUN_FINALIZE_NUDGE
        if self._edit_nudged:
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
            "artifact_reads": self.artifact_reads,
            "job_polls": self.job_polls,
        }
