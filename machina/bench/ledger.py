"""Canonical benchmark task manifests and append-only run evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def canonical_hash(value: Any) -> str:
    """Stable SHA-256 over JSON data with sorted keys and no whitespace drift."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


@dataclass(frozen=True)
class TaskSpec:
    """Identity of one benchmark task before any agent runs it."""

    task_id: str
    suite: str
    repo: str
    base_commit: str
    prompt_sha256: str
    test_patch_sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TaskSpec:
        return cls(
            task_id=str(value["task_id"]),
            suite=str(value["suite"]),
            repo=str(value["repo"]),
            base_commit=str(value["base_commit"]),
            prompt_sha256=str(value["prompt_sha256"]),
            test_patch_sha256=str(value["test_patch_sha256"]),
            metadata=dict(value.get("metadata") or {}),
        )

    def identity(self) -> dict[str, Any]:
        """Fields that must match for two runs to be the same task."""
        return {
            "task_id": self.task_id,
            "suite": self.suite,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "prompt_sha256": self.prompt_sha256,
            "test_patch_sha256": self.test_patch_sha256,
        }

    def digest(self) -> str:
        return canonical_hash(self.identity())


@dataclass(frozen=True)
class RunRecord:
    """One trial of one harness/model on one task."""

    task_id: str
    suite: str
    harness: str
    model: str
    trial: int
    resolved: bool
    duration_s: float
    started_at: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    commit: str = ""
    reasoning_effort: str = ""
    seed: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    tool_calls: int | None = None
    failure_class: str = ""
    verifier_integrity: bool = True
    artifacts: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunRecord:
        known = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: value[key] for key in known if key in value})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Ledger:
    """Append-only JSONL evidence with canonical deduplication."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, record: RunRecord) -> str:
        payload = record.to_dict()
        digest = canonical_hash(payload)
        existing = {canonical_hash(item.to_dict()) for item in self.records()}
        if digest in existing:
            return digest
        line = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        return digest

    def records(self) -> list[RunRecord]:
        if not self.path.is_file():
            return []
        result: list[RunRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            result.append(RunRecord.from_dict(json.loads(line)))
        return result

    def summary(self) -> dict[str, Any]:
        groups: dict[tuple[str, str, str], list[RunRecord]] = {}
        for record in self.records():
            groups.setdefault((record.suite, record.harness, record.model), []).append(record)
        summaries = []
        for (suite, harness, model), records in sorted(groups.items()):
            by_task: dict[str, list[RunRecord]] = {}
            for record in records:
                by_task.setdefault(record.task_id, []).append(record)
            solved_trials = sum(record.resolved for record in records)
            all_attempts_solved = sum(
                all(item.resolved for item in task_records)
                for task_records in by_task.values()
            )
            summaries.append(
                {
                    "suite": suite,
                    "harness": harness,
                    "model": model,
                    "trials": len(records),
                    "tasks": len(by_task),
                    "pass_at_1": solved_trials / len(records) if records else 0.0,
                    "pass_pow_k": all_attempts_solved / len(by_task) if by_task else 0.0,
                    "known_cost_usd": sum(
                        record.cost_usd or 0.0 for record in records
                    ),
                    "duration_s": sum(record.duration_s for record in records),
                }
            )
        return {"groups": summaries, "records": len(self.records())}


class TaskLockbox:
    """A frozen manifest of task identities for a benchmark run."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def freeze(self, tasks: Iterable[TaskSpec]) -> dict[str, Any]:
        entries = [
            {"task_id": task.task_id, "digest": task.digest(), "identity": task.identity()}
            for task in sorted(tasks, key=lambda item: item.task_id)
        ]
        manifest = {
            "version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "tasks": entries,
        }
        _atomic_write(self.path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return manifest

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise FileNotFoundError(f"Lockbox manifest not found: {self.path}")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def verify(self, tasks: Iterable[TaskSpec]) -> list[str]:
        expected = {entry["task_id"]: entry for entry in self.load().get("tasks", [])}
        actual = {task.task_id: task for task in tasks}
        problems: list[str] = []
        for task_id in sorted(set(expected) | set(actual)):
            if task_id not in actual:
                problems.append(f"missing task: {task_id}")
            elif task_id not in expected:
                problems.append(f"unexpected task: {task_id}")
            elif expected[task_id].get("digest") != actual[task_id].digest():
                problems.append(f"task identity changed: {task_id}")
        return problems

    def assert_matches(self, tasks: Iterable[TaskSpec]) -> None:
        problems = self.verify(tasks)
        if problems:
            raise ValueError("lockbox mismatch: " + "; ".join(problems))
