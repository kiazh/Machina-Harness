"""Clean-Git terminal snapshot coverage and its explicit fail-closed boundary."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from machina.workspace import snapshots

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _clean_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("MACHINA_HOME", str(home))
    monkeypatch.setenv("MACHINA_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("MACHINA_SNAPSHOTS_ENABLED", "1")
    _git(workspace, "init")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    (workspace / "keep.txt").write_text("old\n", encoding="utf-8")
    (workspace / "deleted.txt").write_text("gone\n", encoding="utf-8")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "initial")
    return workspace


def test_terminal_changes_are_restored_from_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _clean_repo(monkeypatch, tmp_path)
    turn = snapshots.begin_turn(session_id="terminal-1", workspace=workspace)
    assert snapshots.begin_terminal_capture() is True

    (workspace / "keep.txt").write_text("terminal edit\n", encoding="utf-8")
    (workspace / "deleted.txt").unlink()
    (workspace / "new.txt").write_text("new\n", encoding="utf-8")
    snapshots.end_turn(turn)

    found = snapshots.latest_snapshot(workspace=workspace, session_id="terminal-1")
    assert found is not None
    payload, _ = found
    paths = {Path(item["path"]).name for item in payload["files"]}
    assert paths == {"keep.txt", "deleted.txt", "new.txt"}

    snapshots.undo_snapshot(payload)
    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "old\n"
    assert (workspace / "deleted.txt").read_text(encoding="utf-8") == "gone\n"
    assert not (workspace / "new.txt").exists()


def test_dirty_workspace_disables_terminal_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _clean_repo(monkeypatch, tmp_path)
    (workspace / "keep.txt").write_text("user work\n", encoding="utf-8")
    turn = snapshots.begin_turn(session_id="terminal-2", workspace=workspace)
    assert snapshots.begin_terminal_capture() is False
    (workspace / "keep.txt").write_text("terminal edit\n", encoding="utf-8")
    snapshots.end_turn(turn)
    assert snapshots.latest_snapshot(workspace=workspace, session_id="terminal-2") is None


def test_file_tool_after_terminal_uses_turn_start_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _clean_repo(monkeypatch, tmp_path)
    target = workspace / "keep.txt"
    turn = snapshots.begin_turn(session_id="terminal-3", workspace=workspace)
    assert snapshots.begin_terminal_capture() is True
    target.write_text("terminal edit\n", encoding="utf-8")

    snapshots.record_before_write(target)
    target.write_text("file tool edit\n", encoding="utf-8")
    snapshots.end_turn(turn)

    found = snapshots.latest_snapshot(workspace=workspace, session_id="terminal-3")
    assert found is not None
    payload, _ = found
    assert len(payload["files"]) == 1
    snapshots.undo_snapshot(payload)
    assert target.read_text(encoding="utf-8") == "old\n"
