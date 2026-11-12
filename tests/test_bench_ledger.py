"""Canonical benchmark ledger and task lockbox behavior."""

from __future__ import annotations

import json

import pytest

from machina.bench import Ledger, RunRecord, TaskLockbox, TaskSpec, canonical_hash


def _task(task_id: str = "task-1") -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        suite="swe-bench-pro",
        repo="example/repo",
        base_commit="abc123",
        prompt_sha256="a" * 64,
        test_patch_sha256="b" * 64,
    )


def _record(task_id: str = "task-1", *, trial: int = 0, resolved: bool = True) -> RunRecord:
    return RunRecord(
        task_id=task_id,
        suite="swe-bench-pro",
        harness="machina",
        model="deepseek/deepseek-flash",
        trial=trial,
        resolved=resolved,
        duration_s=12.5,
        started_at="2026-11-12T00:00:00+00:00",
        cost_usd=0.01,
        tokens_in=100,
        tokens_out=20,
        failure_class="" if resolved else "wrong_patch",
    )


def test_ledger_roundtrip_is_canonical_and_deduplicated(tmp_path) -> None:
    ledger = Ledger(tmp_path / "runs.jsonl")
    record = _record()
    first = ledger.append(record)
    second = ledger.append(record)

    assert first == second == canonical_hash(record.to_dict())
    assert ledger.records() == [record]
    assert ledger.summary()["records"] == 1


def test_ledger_summary_reports_pass_at_1_and_pass_pow_k(tmp_path) -> None:
    ledger = Ledger(tmp_path / "runs.jsonl")
    ledger.append(_record(trial=0, resolved=True))
    ledger.append(_record(trial=1, resolved=False))
    ledger.append(_record("task-2", trial=0, resolved=True))
    ledger.append(_record("task-2", trial=1, resolved=True))

    summary = ledger.summary()["groups"][0]
    assert summary["tasks"] == 2
    assert summary["trials"] == 4
    assert summary["pass_at_1"] == 0.75
    assert summary["pass_pow_k"] == 0.5
    assert summary["known_cost_usd"] == pytest.approx(0.04)


def test_lockbox_freezes_identity_and_detects_changes(tmp_path) -> None:
    lockbox = TaskLockbox(tmp_path / "lockbox.json")
    task = _task()
    lockbox.freeze([task])

    assert lockbox.verify([task]) == []
    changed = TaskSpec.from_dict(
        {
            **task.identity(),
            "prompt_sha256": "c" * 64,
        }
    )
    assert lockbox.verify([changed]) == ["task identity changed: task-1"]

    with pytest.raises(ValueError, match="lockbox mismatch"):
        lockbox.assert_matches([changed])


def test_lockbox_detects_missing_and_extra_tasks(tmp_path) -> None:
    lockbox = TaskLockbox(tmp_path / "lockbox.json")
    lockbox.freeze([_task("task-1")])

    assert lockbox.verify([]) == ["missing task: task-1"]
    assert lockbox.verify([_task("task-1"), _task("task-2")]) == [
        "unexpected task: task-2"
    ]


def test_lockbox_is_json_and_does_not_contain_prompt_body(tmp_path) -> None:
    path = tmp_path / "lockbox.json"
    TaskLockbox(path).freeze([_task()])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "prompt" not in data["tasks"][0]
    assert data["tasks"][0]["identity"]["prompt_sha256"] == "a" * 64
