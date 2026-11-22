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
    _git(workspace, "config", "core.autocrlf", "false")
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


def test_terminal_filter_attributes_are_disclosed_not_restored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _clean_repo(monkeypatch, tmp_path)
    (workspace / ".gitattributes").write_text("* text eol=crlf\n", encoding="utf-8")
    target = workspace / "filtered.txt"
    target.write_bytes(b"one\ntwo\n")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "attributes")
    target.unlink()
    _git(workspace, "checkout", "--", "filtered.txt")
    before = target.read_bytes()
    assert b"\r\n" in before

    turn = snapshots.begin_turn(session_id="terminal-4", workspace=workspace)
    assert snapshots.begin_terminal_capture() is True
    target.write_bytes(b"changed\n")
    snapshots.end_turn(turn)

    found = snapshots.latest_snapshot(workspace=workspace, session_id="terminal-4")
    assert found is not None
    payload, _ = found
    entries = [item for item in payload["files"] if Path(item["path"]).name == "filtered.txt"]
    assert entries and entries[0]["ignored"] is True
    snapshots.undo_snapshot(payload)
    assert target.read_bytes() == b"changed\n"


def test_core_autocrlf_disables_terminal_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _clean_repo(monkeypatch, tmp_path)
    _git(workspace, "config", "core.autocrlf", "true")
    turn = snapshots.begin_turn(session_id="terminal-autocrlf", workspace=workspace)
    assert snapshots.begin_terminal_capture() is False
    (workspace / "keep.txt").write_text("changed\n", encoding="utf-8")
    snapshots.end_turn(turn)
    assert snapshots.latest_snapshot(workspace=workspace, session_id="terminal-autocrlf") is None


def test_pre_existing_ignored_file_is_not_deleted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _clean_repo(monkeypatch, tmp_path)
    (workspace / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    _git(workspace, "add", ".gitignore")
    _git(workspace, "commit", "-m", "ignore")
    ignored = workspace / "ignored.txt"
    ignored.write_text("user data\n", encoding="utf-8")

    turn = snapshots.begin_turn(session_id="terminal-5", workspace=workspace)
    assert snapshots.begin_terminal_capture() is True
    ignored.write_text("terminal edit\n", encoding="utf-8")
    snapshots.end_turn(turn)

    # Ignored files are outside terminal coverage; they must never be deleted
    # by an undo that only knows about HEAD.
    assert ignored.read_text(encoding="utf-8") == "terminal edit\n"
    assert snapshots.latest_snapshot(workspace=workspace, session_id="terminal-5") is None


def test_manifest_cannot_restore_a_backup_outside_the_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _clean_repo(monkeypatch, tmp_path)
    target = workspace / "keep.txt"
    target.write_text("current\n", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    payload = {
        "workspace": str(workspace),
        "files": [
            {
                "path": str(target),
                "backup": str(outside),
                "existed": True,
                "before_sha": "x",
                "after_sha": snapshots._sha256(target),
                "ignored": False,
            }
        ],
    }

    assert "secret" not in snapshots.diff_snapshot(payload)
    assert snapshots.undo_snapshot(payload) == []
    assert target.read_text(encoding="utf-8") == "current\n"
    assert "secret" not in target.read_text(encoding="utf-8")
