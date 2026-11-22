"""Per-turn snapshot diff and undo behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from machina.workspace import snapshots


def _snapshot_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("MACHINA_HOME", str(home))
    monkeypatch.setenv("MACHINA_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("MACHINA_SNAPSHOTS_ENABLED", "1")
    return workspace


def test_snapshot_restores_existing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = _snapshot_workspace(monkeypatch, tmp_path)
    target = workspace / "app.py"
    target.write_text("before\n", encoding="utf-8")

    turn = snapshots.begin_turn(session_id="session-1", workspace=workspace)
    snapshots.record_before_write(target)
    target.write_text("after\n", encoding="utf-8")
    snapshots.end_turn(turn)

    found = snapshots.latest_snapshot(workspace=workspace, session_id="session-1")
    assert found is not None
    payload, manifest = found
    assert "before" in snapshots.diff_snapshot(payload)
    assert "after" in snapshots.diff_snapshot(payload)

    restored = snapshots.undo_snapshot(payload)
    assert restored == [str(target.resolve())]
    assert target.read_text(encoding="utf-8") == "before\n"
    snapshots.mark_undone(payload, manifest)
    assert snapshots.latest_snapshot(workspace=workspace, session_id="session-1") is None


def test_snapshot_deletes_new_file_and_refuses_later_edits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _snapshot_workspace(monkeypatch, tmp_path)
    target = workspace / "new.py"

    turn = snapshots.begin_turn(session_id="session-2", workspace=workspace)
    snapshots.record_before_write(target)
    target.write_text("new\n", encoding="utf-8")
    snapshots.end_turn(turn)

    payload, manifest = snapshots.latest_snapshot(workspace=workspace, session_id="session-2")  # type: ignore[misc]
    target.write_text("user edit\n", encoding="utf-8")
    with pytest.raises(snapshots.SnapshotConflict):
        snapshots.undo_snapshot(payload)

    restored = snapshots.undo_snapshot(payload, force=True)
    assert restored == [str(target.resolve())]
    assert not target.exists()
    snapshots.mark_undone(payload, manifest)


def test_sensitive_paths_are_not_copied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = _snapshot_workspace(monkeypatch, tmp_path)
    secret = workspace / ".env"
    secret.write_text("TOKEN=secret\n", encoding="utf-8")

    turn = snapshots.begin_turn(session_id="session-3", workspace=workspace)
    snapshots.record_before_write(secret)
    secret.write_text("TOKEN=changed\n", encoding="utf-8")
    snapshots.end_turn(turn)

    assert snapshots.latest_snapshot(workspace=workspace, session_id="session-3") is None


def test_paths_outside_the_workspace_are_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _snapshot_workspace(monkeypatch, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")

    turn = snapshots.begin_turn(session_id="session-4", workspace=workspace)
    snapshots.record_before_write(outside)
    outside.write_text("changed\n", encoding="utf-8")
    snapshots.end_turn(turn)

    assert snapshots.latest_snapshot(workspace=workspace, session_id="session-4") is None
