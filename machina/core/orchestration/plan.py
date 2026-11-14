"""Delegation plan contract and deterministic parallel validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_PLAN_TASKS = 64
MIN_PARALLEL_TASKS = 2


class PlanError(ValueError):
    """The planner returned a plan the scheduler must not execute."""


@dataclass(frozen=True)
class WorkerTask:
    id: str
    agent: str
    task: str
    role: str = "worker"
    expected_output: str | None = None
    expected_artifact_path: str | None = None
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "task": self.task,
            "role": self.role,
            "expected_output": self.expected_output,
            "expected_artifact_path": self.expected_artifact_path,
            "depends_on": list(self.depends_on),
        }


@dataclass
class DelegationPlan:
    reason: str
    tasks: tuple[WorkerTask, ...]
    strategy: str = "parallel"
    warnings: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "strategy": self.strategy,
            "tasks": [task.to_dict() for task in self.tasks],
            "warnings": list(self.warnings),
        }

    def task(self, task_id: str) -> WorkerTask:
        for item in self.tasks:
            if item.id == task_id:
                return item
        raise KeyError(task_id)


_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_object(content: str) -> dict[str, Any]:
    """Extract a JSON object from plain or fenced model output."""
    text = str(content or "").strip()
    if not text:
        raise PlanError("planner returned no content")
    candidates = [text]
    match = _FENCED_JSON.search(text)
    if match:
        candidates.insert(0, match.group(1))
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    raise PlanError("planner output did not contain a JSON object")


def parse_plan(content: str, *, max_tasks: int = MAX_PLAN_TASKS) -> DelegationPlan:
    """Parse and validate a planner response."""
    payload = extract_json_object(content)
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        raise PlanError("plan.tasks must be a list")
    tasks: list[WorkerTask] = []
    seen_ids: set[str] = set()
    seen_artifacts: set[str] = set()
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict):
            raise PlanError(f"task[{index}] must be an object")
        task_id = str(raw.get("id") or "").strip()
        agent = str(raw.get("agent") or "").strip()
        task_text = str(raw.get("task") or "").strip()
        if not task_id:
            task_id = f"task-{index + 1}"
        if task_id in seen_ids:
            raise PlanError(f"duplicate task id {task_id!r}")
        if not agent:
            raise PlanError(f"task {task_id!r} has no agent")
        if not task_text:
            raise PlanError(f"task {task_id!r} has no task text")
        artifact = raw.get("expected_artifact_path")
        artifact = str(artifact).strip() if artifact else None
        if artifact:
            if artifact in seen_artifacts:
                raise PlanError(f"duplicate artifact ownership: {artifact}")
            seen_artifacts.add(artifact)
        depends = raw.get("depends_on") or ()
        if isinstance(depends, str):
            depends = [depends]
        if not isinstance(depends, (list, tuple)):
            raise PlanError(f"task {task_id!r} depends_on must be a list")
        dependencies = tuple(str(item).strip() for item in depends if str(item).strip())
        if task_id in dependencies:
            raise PlanError(f"task {task_id!r} cannot depend on itself")
        seen_ids.add(task_id)
        tasks.append(
            WorkerTask(
                id=task_id,
                agent=agent,
                task=task_text,
                role=str(raw.get("role") or "worker").strip()[:40] or "worker",
                expected_output=str(raw["expected_output"]).strip()
                if raw.get("expected_output")
                else None,
                expected_artifact_path=artifact,
                depends_on=dependencies,
            )
        )
    plan = DelegationPlan(
        reason=str(payload.get("reason") or "parallel work requested"),
        strategy=str(payload.get("strategy") or "parallel").strip().lower(),
        tasks=tuple(tasks),
        raw=payload,
    )
    validate_plan(plan, max_tasks=max_tasks)
    return plan


def validate_plan(
    plan: DelegationPlan,
    *,
    max_tasks: int = MAX_PLAN_TASKS,
    require_parallel: bool = True,
) -> None:
    """Raise :class:`PlanError` when a plan is unsafe or not actually parallel."""
    if not 1 <= len(plan.tasks) <= max_tasks:
        raise PlanError(f"plan must contain 1..{max_tasks} tasks")
    if require_parallel and len(plan.tasks) < MIN_PARALLEL_TASKS:
        raise PlanError("parallel request produced fewer than two independent tasks")
    ids = {task.id for task in plan.tasks}
    for task in plan.tasks:
        missing = [dependency for dependency in task.depends_on if dependency not in ids]
        if missing:
            raise PlanError(f"task {task.id!r} depends on unknown task(s): {', '.join(missing)}")
    _topological_order(plan)


def _topological_order(plan: DelegationPlan) -> tuple[str, ...]:
    """Kahn topological sort with a cycle error."""
    pending = {task.id: set(task.depends_on) for task in plan.tasks}
    ordered: list[str] = []
    while pending:
        ready = sorted(task_id for task_id, deps in pending.items() if not deps)
        if not ready:
            raise PlanError("plan contains a dependency cycle")
        for task_id in ready:
            ordered.append(task_id)
            pending.pop(task_id, None)
        for deps in pending.values():
            deps.difference_update(ready)
    return tuple(ordered)


def waves(plan: DelegationPlan) -> tuple[tuple[WorkerTask, ...], ...]:
    """Tasks grouped into dependency waves; every wave runs concurrently."""
    pending = {task.id: set(task.depends_on) for task in plan.tasks}
    by_id = {task.id: task for task in plan.tasks}
    result: list[tuple[WorkerTask, ...]] = []
    completed: set[str] = set()
    while pending:
        ready = sorted(task_id for task_id, deps in pending.items() if deps <= completed)
        if not ready:
            raise PlanError("plan contains a dependency cycle")
        result.append(tuple(by_id[task_id] for task_id in ready))
        for task_id in ready:
            pending.pop(task_id, None)
        completed.update(ready)
    return tuple(result)


def fallback_plan(prompt: str, *, agent: str = "coder") -> DelegationPlan | None:
    """Deterministic split for explicit semicolon/newline/bullet tracks.

    This is intentionally conservative: it only creates parallel tasks when
    the user clearly named two or more independent clauses. Otherwise the
    caller should keep normal serial behavior rather than inventing work.
    """
    text = str(prompt or "").strip()
    if not text:
        return None
    parts = [
        re.sub(r"^[-*\d.\s]+", "", part).strip(" .")
        for part in re.split(r"(?:\n|;|\band\b)", text, flags=re.IGNORECASE)
    ]
    parts = [part for part in parts if len(part) >= 12]
    if len(parts) < 2:
        return None
    tasks = tuple(
        WorkerTask(
            id=f"track-{index + 1}",
            agent=agent,
            task=part,
            role="worker",
        )
        for index, part in enumerate(parts[:MAX_PLAN_TASKS])
    )
    plan = DelegationPlan(reason="deterministic split of named independent tracks", tasks=tasks)
    try:
        validate_plan(plan, require_parallel=True)
    except PlanError:
        return None
    return plan


__all__ = [
    "DelegationPlan",
    "MAX_PLAN_TASKS",
    "MIN_PARALLEL_TASKS",
    "PlanError",
    "WorkerTask",
    "extract_json_object",
    "fallback_plan",
    "parse_plan",
    "validate_plan",
    "waves",
]
