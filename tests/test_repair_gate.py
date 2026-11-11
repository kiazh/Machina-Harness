"""Completion gate for code-repair turns that make no source change."""

from __future__ import annotations

import subprocess
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from machina.core.repair_gate import (
    REPAIR_NUDGE,
    should_nudge_repair,
    workspace_is_clean,
)


def _git_workspace(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_unchanged_repair_task_gets_one_continuation(tmp_path) -> None:
    root = _git_workspace(tmp_path)

    assert workspace_is_clean(root) is True
    assert should_nudge_repair("Fix the failing test in src/app.py", workspace=root) is True
    assert "still unchanged" in REPAIR_NUDGE


def test_changed_workspace_does_not_get_a_continuation(tmp_path) -> None:
    root = _git_workspace(tmp_path)
    (root / "app.py").write_text("fixed = True\n", encoding="utf-8")

    assert workspace_is_clean(root) is False
    assert should_nudge_repair("Fix the failing test in src/app.py", workspace=root) is False


def test_non_repair_prompt_and_second_nudge_do_not_loop(tmp_path) -> None:
    root = _git_workspace(tmp_path)

    assert should_nudge_repair("hello", workspace=root) is False
    assert should_nudge_repair("Fix the failing test in src/app.py", workspace=root, nudges=1) is False


def test_non_git_workspace_fails_safe(tmp_path) -> None:
    assert workspace_is_clean(tmp_path) is False
    assert should_nudge_repair("Fix the failing test in src/app.py", workspace=tmp_path) is False


@pytest.mark.asyncio
async def test_agent_guard_appends_nudge_and_persists_content(monkeypatch) -> None:
    from machina.core import agent as module

    monkeypatch.setattr(module, "should_nudge_repair", lambda *args, **kwargs: True)
    append = AsyncMock()
    flush = AsyncMock()
    agent = SimpleNamespace(
        agent_id="harness",
        _append_assistant_content=append,
        _flush_pending_chunks=flush,
    )
    messages: list[dict[str, str]] = []

    continued = await module.BaseReActAgent._maybe_continue_unchanged_repair(
        agent,
        session_id=uuid.uuid4(),
        user_message="Fix the failing test",
        content="No change needed.",
        messages=messages,
        pending_chunks=[],
        nudges=0,
    )

    assert continued is True
    assert messages == [{"role": "user", "content": REPAIR_NUDGE}]
    append.assert_awaited_once()
