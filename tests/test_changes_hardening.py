"""Adversarial checks for the Changes surface: path escapes and bad RPC params."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import pytest

from machina.gateway.errors import INVALID_PARAMS, RpcError
from machina.gateway.features import changes
from machina.workspace import snapshots


def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("MACHINA_HOME", str(home))
    monkeypatch.setenv("MACHINA_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("MACHINA_SNAPSHOTS_ENABLED", "1")
    return workspace


def _turn(workspace: Path, *, path: str = "src/a.py") -> dict[str, object]:
    target = workspace / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old\n", encoding="utf-8")
    turn = snapshots.begin_turn(
        session_id="session-harden",
        workspace=workspace,
        prompt="Change it",
        provider="hermes",
        model="qwen/qwen4-max",
    )
    assert turn is not None
    snapshots.record_before_write(target)
    target.write_text("new\n", encoding="utf-8")
    snapshots.end_turn(turn)
    found = snapshots.latest_snapshot(workspace=workspace, session_id="session-harden")
    assert found is not None
    return found[0]


def test_changes_diff_path_traversal_never_leaves_the_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(changes, "configured_workspace", lambda: workspace)
    payload = _turn(workspace)
    secret = tmp_path / "outside.txt"
    secret.write_text("SECRET\n", encoding="utf-8")

    for requested in (
        "../outside.txt",
        "../../../../etc/passwd",
        str(secret),
        "src/../../outside.txt",
        "src\\..\\..\\outside.txt",
    ):
        result = asyncio.run(
            changes.changes_diff(
                object(), {"snapshotId": payload["snapshot_id"], "path": requested}
            )
        )
        assert result["files"] == []

    exact = asyncio.run(
        changes.changes_diff(
            object(), {"snapshotId": payload["snapshot_id"], "path": "src/a.py"}
        )
    )
    assert [item["path"] for item in exact["files"]] == ["src/a.py"]


def test_diff_and_undo_ignore_files_outside_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("SECRET\n", encoding="utf-8")
    payload = {
        "workspace": str(workspace),
        "files": [
            {"path": str(secret), "binary": False},
            {"path": "../outside.txt", "binary": False},
        ],
    }
    assert snapshots.diff_files(payload)["files"] == []
    assert snapshots.undo_snapshot(payload, force=True) == []
    assert secret.read_text(encoding="utf-8") == "SECRET\n"


def test_symlinks_cannot_snapshot_content_outside_the_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    secret = tmp_path / "outside.txt"
    secret.write_text("SECRET\n", encoding="utf-8")
    link = workspace / "src" / "link.py"
    link.parent.mkdir(parents=True)
    try:
        os.symlink(secret, link)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    turn = snapshots.begin_turn(
        session_id="session-link",
        workspace=workspace,
        prompt="Follow the link",
        provider="hermes",
        model="qwen/qwen4-max",
    )
    assert turn is not None
    snapshots.record_before_write(link)
    secret.write_text("CHANGED OUTSIDE\n", encoding="utf-8")
    snapshots.end_turn(turn)
    found = snapshots.latest_snapshot(workspace=workspace, session_id="session-link")
    payload = found[0] if found is not None else {"workspace": str(workspace), "files": []}
    assert "SECRET" not in str(snapshots.diff_files(payload))
    assert snapshots.diff_files(payload)["files"] == []


def test_changes_rpc_emits_debug_summaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    payload = _turn(workspace)
    monkeypatch.setattr(changes, "configured_workspace", lambda: workspace)
    with caplog.at_level(logging.DEBUG, logger="machina.gateway.features.changes"):
        asyncio.run(changes.changes_list(object(), {"scope": "workspace", "limit": 10}))
        asyncio.run(
            changes.changes_diff(object(), {"snapshotId": payload["snapshot_id"]})
        )
        asyncio.run(
            changes.review_start(
                object(), {"snapshotId": payload["snapshot_id"], "force": True}
            )
        )
    assert "changes.list" in caplog.text
    assert "changes.diff" in caplog.text
    assert "review.start" in caplog.text
    assert payload["snapshot_id"] in caplog.text


def test_summarize_tolerates_corrupt_counters(tmp_path: Path) -> None:
    payload = {
        "workspace": str(tmp_path),
        "snapshot_id": "s" * 32,
        "files": [
            {"path": "a.py", "change": "modified", "additions": "many", "deletions": "-3"},
            {"path": "b.py", "change": "added", "additions": 4, "deletions": None},
        ],
        "stats": {"files": "two", "additions": "lots", "deletions": None},
    }
    summary = snapshots.summarize(payload)
    assert summary["files"][0]["additions"] == 0
    assert summary["files"][0]["deletions"] == 0
    assert summary["files"][1]["additions"] == 4
    assert summary["stats"] == {"files": 2, "additions": 4, "deletions": 0}


def test_changes_rpc_rejects_bad_types_and_bounds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(changes, "configured_workspace", lambda: workspace)
    payload = _turn(workspace)
    snapshot_id = payload["snapshot_id"]

    bad_calls = [
        (changes.changes_list, {"scope": "bogus"}),
        (changes.changes_list, {"scope": "session", "sessionId": 17}),
        (changes.changes_list, {"scope": "workspace", "limit": 0}),
        (changes.changes_list, {"scope": "workspace", "limit": True}),
        (changes.changes_diff, {"snapshotId": 17}),
        (changes.changes_diff, {"snapshotId": snapshot_id, "path": "x" * 5000}),
        (changes.changes_diff, {"snapshotId": snapshot_id, "path": 17}),
        (changes.review_start, {"snapshotId": snapshot_id, "engine": "nope"}),
        (changes.review_start, {"snapshotId": snapshot_id, "force": "yes"}),
        (changes.review_cancel, {}),
    ]
    for handler, params in bad_calls:
        with pytest.raises(RpcError) as exc:
            asyncio.run(handler(object(), params))
        assert exc.value.code == INVALID_PARAMS, (handler.__name__, params, exc.value.message)
