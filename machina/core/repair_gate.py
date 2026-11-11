"""Deterministic completion guard for repository-repair turns.

A repair task that ends without changing the working tree is incomplete. The
model can still answer a task-shaped prompt with an explanation, especially
after a failed or missing edit. This gate gives it one explicit continuation
instead of silently grading an unchanged repository as finished.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from machina.core.config import config

_REPAIR_INTENT_RE = re.compile(
    r"\b(?:fix|bug|failing|failure|regression|implement|patch|repair|broken|issue)\b",
    re.IGNORECASE,
)
DEFAULT_REPAIR_DEADLINE_SECONDS = 600.0
REPAIR_DEADLINE_TEXT = (
    "Repair deadline reached. The turn stopped before the external task timeout; "
    "the current working-tree changes are ready for verification."
)

REPAIR_NUDGE = (
    "The repository is still unchanged. This is a repair task, so an explanation "
    "alone is not complete. Reproduce the failure, make the smallest source-only "
    "change, run the narrowest relevant test or direct behavior check, and then "
    "report the change and verification."
)


def workspace_is_clean(workspace: str | Path | None = None) -> bool:
    """Return whether the workspace is a clean git checkout.

    Unknown state fails closed: no continuation is sent when the workspace is
    not a working git checkout or git cannot be inspected.
    """
    root = Path(
        workspace
        or config.get("workspace_root")
        or config.get("machina_home")
        or Path.cwd()
    )
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return not result.stdout.strip()


def is_repair_task(user_message: str) -> bool:
    """Return True when the prompt carries explicit repair intent."""
    return bool(_REPAIR_INTENT_RE.search(user_message))


def repair_deadline_seconds() -> float:
    """Wall-clock ceiling for repair turns; 0 disables the deadline."""
    raw = os.environ.get("MACHINA_REPAIR_DEADLINE_SECONDS")
    if raw is None or not str(raw).strip():
        return DEFAULT_REPAIR_DEADLINE_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_REPAIR_DEADLINE_SECONDS
    return max(0.0, min(value, 86400.0))


def repair_deadline_exceeded(
    started: float,
    user_message: str,
    *,
    now: float | None = None,
) -> bool:
    """Return True when a repair turn has exceeded its configured deadline."""
    deadline = repair_deadline_seconds()
    if deadline <= 0 or not is_repair_task(user_message):
        return False
    return (time.monotonic() if now is None else now) - started >= deadline


def should_nudge_repair(
    user_message: str,
    *,
    workspace: str | Path | None = None,
    nudges: int = 0,
) -> bool:
    """Return True when a repair turn should get one continuation."""
    return nudges < 1 and is_repair_task(user_message) and workspace_is_clean(workspace)
