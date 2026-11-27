"""Per-prompt change history and local review runs."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from machina.gateway.features import changes
from machina.workspace import reviews, snapshots


def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("MACHINA_HOME", str(home))
    monkeypatch.setenv("MACHINA_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("MACHINA_SNAPSHOTS_ENABLED", "1")
    return workspace


def _finish_turn(
    workspace: Path,
    *,
    session: str,
    prompt: str,
    path: str,
    before: str,
    after: str,
) -> dict[str, object]:
    target = workspace / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if before:
        target.write_text(before, encoding="utf-8")
    turn = snapshots.begin_turn(
        session_id=session,
        workspace=workspace,
        prompt=prompt,
        provider="hermes",
        model="qwen/qwen4-max",
    )
    assert turn is not None
    snapshots.record_before_write(target)
    if after:
        target.write_text(after, encoding="utf-8")
    else:
        target.unlink()
    snapshots.end_turn(turn)
    found = snapshots.latest_snapshot(workspace=workspace, session_id=session)
    assert found is not None
    return found[0]


def test_prompt_change_is_stable_when_a_later_prompt_edits_the_same_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    first = _finish_turn(
        workspace,
        session="session-a",
        prompt="Make the parser accept two tokens",
        path="src/parser.py",
        before="old\n",
        after="first\n",
    )
    second = _finish_turn(
        workspace,
        session="session-a",
        prompt="Handle the third token too",
        path="src/parser.py",
        before="first\n",
        after="second\n",
    )

    first_diff = snapshots.diff_snapshot(first)
    second_diff = snapshots.diff_snapshot(second)
    assert "first" in first_diff
    assert "second" not in first_diff, "a later prompt must not leak into an earlier row"
    assert "second" in second_diff

    first_summary = snapshots.summarize(first)
    assert first_summary["prompt"] == "Make the parser accept two tokens"
    assert first_summary["provider"] == "hermes"
    assert first_summary["stats"] == {"files": 1, "additions": 1, "deletions": 1}
    assert first_summary["files"][0]["path"] == "src/parser.py"
    assert first_summary["files"][0]["change"] == "modified"


def test_noop_write_is_not_listed_as_a_prompt_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    target = workspace / "same.py"
    target.write_text("same\n", encoding="utf-8")
    turn = snapshots.begin_turn(session_id="session-noop", workspace=workspace, prompt="touch it")
    assert turn is not None
    snapshots.record_before_write(target)
    snapshots.end_turn(turn)
    assert snapshots.latest_snapshot(workspace=workspace, session_id="session-noop") is None


def test_rules_review_reports_concrete_findings_and_is_deduplicated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    payload = _finish_turn(
        workspace,
        session="session-review",
        prompt="Add the client and configuration",
        path="src/client.py",
        before="",
        after=(
            "API_KEY = 'sk-live-abcdefghijklmnopqrstuvwx'\n"
            "<<<<<<< HEAD\n"
            "print('debug')\n"
            "TODO: remove this\n"
        ),
    )
    review = reviews.run_rules(workspace, payload, force=True)
    assert review["status"] == "complete"
    rules = {finding["rule"] for finding in review["findings"]}
    assert {"hardcoded-secret", "merge-conflict", "debug-print", "unfinished-marker"} <= rules
    assert "critical" in review["summary"]
    fingerprints = [finding["fingerprint"] for finding in review["findings"]]
    assert len(fingerprints) == len(set(fingerprints))

    rerun = reviews.run_rules(workspace, payload)
    assert rerun["id"] == review["id"], "an unchanged complete run is reused"


def test_rules_review_is_clean_for_a_small_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    payload = _finish_turn(
        workspace,
        session="session-clean",
        prompt="Clarify the docstring",
        path="src/clean.py",
        before="def run():\n    return 1\n",
        after='def run():\n    """Return one."""\n    return 1\n',
    )
    review = reviews.run_rules(workspace, payload, force=True)
    assert review["status"] == "complete"
    assert review["findings"] == []
    assert "No issues" in review["summary"]


def test_a_large_first_file_does_not_hide_later_diffs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    big = workspace / "src" / "big.py"
    small = workspace / "src" / "small.py"
    big.parent.mkdir(parents=True)
    big.write_text("old\n", encoding="utf-8")
    small.write_text("old\n", encoding="utf-8")
    dense = "".join(f"line {index:03d} {'x' * 80}\n" for index in range(100))
    turn = snapshots.begin_turn(
        session_id="session-budget",
        workspace=workspace,
        prompt="Touch two files",
        provider="hermes",
        model="qwen/qwen4-max",
    )
    assert turn is not None
    snapshots.record_before_write(big)
    big.write_text(dense, encoding="utf-8")
    snapshots.record_before_write(small)
    small.write_text(dense.replace("x", "y"), encoding="utf-8")
    snapshots.end_turn(turn)
    found = snapshots.latest_snapshot(workspace=workspace, session_id="session-budget")
    assert found is not None
    payload = found[0]

    bounded_big = snapshots.diff_files(payload, max_chars=1_000_000, path="src/big.py")
    budget = len(bounded_big["files"][0]["text"])
    bounded = snapshots.diff_files(payload, max_chars=budget)
    assert bounded["truncated"] is True
    assert [item["path"] for item in bounded["files"]] == ["src/big.py"]

    selected = snapshots.diff_files(payload, max_chars=budget, path="src/small.py")
    assert [item["path"] for item in selected["files"]] == ["src/small.py"]
    assert "line 000 yyyy" in selected["files"][0]["text"]


def test_gateway_lists_prompt_changes_and_starts_a_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    payload = _finish_turn(
        workspace,
        session="session-gateway",
        prompt="Remove the dead branch",
        path="src/dead.py",
        before="if True:\n    pass\n",
        after="",
    )
    monkeypatch.setattr(changes, "configured_workspace", lambda: workspace)
    gateway = object()

    listed = asyncio.run(changes.changes_list(gateway, {"scope": "workspace", "limit": 10}))
    assert len(listed["turns"]) == 1
    turn = listed["turns"][0]
    assert turn["id"] == payload["snapshot_id"]
    assert turn["prompt"] == "Remove the dead branch"
    assert turn["review"] is None
    assert turn["stats"]["files"] == 1

    diff = asyncio.run(
        changes.changes_diff(gateway, {"snapshotId": payload["snapshot_id"]})
    )
    assert diff["files"][0]["path"] == "src/dead.py"
    assert diff["files"][0]["change"] == "deleted"

    started = asyncio.run(
        changes.review_start(
            gateway,
            {"snapshotId": payload["snapshot_id"], "engine": "rules", "force": True},
        )
    )
    assert started["review"]["status"] == "complete"
    full = asyncio.run(
        changes.review_get(gateway, {"snapshotId": payload["snapshot_id"]})
    )
    assert full["review"]["id"] == started["review"]["id"]
    assert full["review"]["status"] == "complete"

    listed_again = asyncio.run(
        changes.changes_list(gateway, {"scope": "workspace", "limit": 10})
    )
    assert listed_again["turns"][0]["review"]["status"] == "complete"
