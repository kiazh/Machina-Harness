"""Dependency-aware execution of a validated delegation plan."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from machina.core.config import config as live_config
from machina.core.orchestration.plan import DelegationPlan, WorkerTask, waves
from machina.core.routing import ModelInventory, route_task
from machina.core.routing.catalog import build_inventory
from machina.observability.diagnostics import record_failure

try:  # pragma: no cover - import guard for type-only use
    from machina.core.orchestrator import DelegationResult, Orchestrator
except Exception:  # pragma: no cover
    DelegationResult = Any  # type: ignore[assignment,misc]
    Orchestrator = Any  # type: ignore[assignment,misc]


@dataclass
class TaskOutcome:
    task_id: str
    agent: str
    status: str
    session_id: str | None = None
    response: str = ""
    tokens: int = 0
    route: dict[str, Any] | None = None
    error: str = ""
    elapsed_seconds: float = 0.0
    needs_approval: list[dict[str, Any]] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status in {
            "error",
            "failed",
            "interrupted",
            "blocked",
            "cancelled",
            "waiting_approval",
            "paused",
        }

    @property
    def completed(self) -> bool:
        return self.status in {"complete", "completed", "success"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "status": self.status,
            "session_id": self.session_id,
            "response": self.response,
            "tokens": self.tokens,
            "route": self.route,
            "error": self.error,
            "needs_approval": list(self.needs_approval),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


@dataclass
class PlanResult:
    plan: DelegationPlan
    outcomes: tuple[TaskOutcome, ...]
    warnings: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0

    @property
    def tokens(self) -> int:
        return sum(outcome.tokens for outcome in self.outcomes)

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(outcome.completed for outcome in self.outcomes)

    def summary(self) -> str:
        completed = sum(1 for outcome in self.outcomes if outcome.completed)
        awaiting = sum(
            1
            for outcome in self.outcomes
            if outcome.status in {"waiting_approval", "paused"}
        )
        failed = sum(
            1
            for outcome in self.outcomes
            if outcome.failed and outcome.status not in {"waiting_approval", "paused"}
        )
        details = [f"{completed} completed"]
        if awaiting:
            details.append(f"{awaiting} awaiting approval")
        if failed:
            details.append(f"{failed} failed")
        lines = [
            f"Parallel plan: {len(self.outcomes)} track(s), " + ", ".join(details) + "."
        ]
        for outcome in self.outcomes:
            if outcome.completed:
                state = "completed"
            elif outcome.status in {"waiting_approval", "paused"}:
                state = "awaiting approval"
            elif outcome.failed:
                state = "failed"
            else:
                state = outcome.status
            lines.append(f"\n## {outcome.task_id} ({outcome.agent}, {state})")
            if outcome.session_id:
                lines.append(f"Child session: {outcome.session_id}")
            if outcome.error:
                lines.append(f"Error: {outcome.error}")
            if outcome.response:
                lines.append(outcome.response[:6000])
        if self.warnings:
            lines.append("\nWarnings: " + "; ".join(self.warnings))
        return "\n".join(lines).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "ok": self.ok,
            "tokens": self.tokens,
            "warnings": list(self.warnings),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def _mcp_platform() -> str | None:
    """Best installed external harness for MCP-mode workers."""
    try:
        from machina import interop

        snapshot = interop.detect_cached()
        detected = snapshot[0] if isinstance(snapshot, tuple) else snapshot
        if not detected:
            detected = interop.detect(("opencode", "codex", "hermes"))
        order = ("opencode", "codex", "hermes")
        for key in order:
            entry = detected.get(key)
            if entry is not None and getattr(entry, "installed", False):
                return key
    except Exception as error:
        record_failure("orchestration.run_plan.mcp_detect", error)
    return None


async def _run_mcp_task(task: WorkerTask, *, timeout: float = 600.0) -> TaskOutcome:
    started = time.monotonic()
    platform = _mcp_platform()
    if platform is None:
        return TaskOutcome(
            task_id=task.id,
            agent=task.agent,
            status="error",
            error="MCP transport selected but no installed external harness is available",
            elapsed_seconds=time.monotonic() - started,
        )
    try:
        from machina import interop

        result = await asyncio.to_thread(
            interop.run_task,
            platform,
            task.task,
            timeout=timeout,
        )
        status = "complete" if result.ok else "error"
        return TaskOutcome(
            task_id=task.id,
            agent=f"mcp:{platform}",
            status=status,
            response=str(result.stdout or result.stderr or ""),
            tokens=0,
            route={
                "provider": platform,
                "model": "",
                "transport": "mcp",
                "label": f"{platform} MCP worker",
                "reason": "worker_transport=mcp",
            },
            error="" if result.ok else str(result.stderr or "MCP worker failed"),
            elapsed_seconds=time.monotonic() - started,
        )
    except Exception as error:
        record_failure("orchestration.run_plan.mcp_task", error)
        return TaskOutcome(
            task_id=task.id,
            agent=f"mcp:{platform}",
            status="error",
            error=f"{type(error).__name__}: {error}",
            elapsed_seconds=time.monotonic() - started,
        )


async def run_plan(
    plan: DelegationPlan,
    *,
    parent_session_id: Any = None,
    from_agent: str = "parallel-delegator",
    authority: dict | None = None,
    config: Any = None,
    inventory: ModelInventory | None = None,
    parent_provider: str | None = None,
    parent_model: str | None = None,
    max_concurrency: int | None = None,
    orchestrator: Orchestrator | None = None,
) -> PlanResult:
    """Execute every ready task concurrently, respecting dependencies.

    The first wave starts together; later waves start only after all their
    dependencies complete. A failed dependency blocks its dependents while
    independent branches continue. Route selection is completed before each
    worker starts, and the chosen route is recorded in the outcome.
    """
    if config is None:
        config = live_config
    if orchestrator is None:
        from machina.core.orchestrator import orchestrator as global_orchestrator

        orchestrator = global_orchestrator
    if inventory is None:
        inventory = build_inventory(config)
    if max_concurrency is None:
        try:
            max_concurrency = int(config.get("max_delegate_concurrency") or 8)
        except Exception:
            max_concurrency = 8
    semaphore = asyncio.Semaphore(max(1, min(int(max_concurrency), 64)))
    started = time.monotonic()
    outcomes: dict[str, TaskOutcome] = {}
    warnings: list[str] = []
    try:
        worker_transport = str(config.get("worker_transport") or "native").lower()
        model_selection = str(config.get("model_selection") or "auto").lower()
        orchestration_mode = str(config.get("orchestration_mode") or "multi_model").lower()
    except Exception:
        worker_transport = "native"
        model_selection = "auto"
        orchestration_mode = "multi_model"

    async def run_task(task: WorkerTask) -> TaskOutcome:
        task_started = time.monotonic()
        missing = [dep for dep in task.depends_on if dep not in outcomes]
        blocked = [
            dep for dep in task.depends_on if dep in outcomes and outcomes[dep].failed
        ]
        if missing or blocked:
            reason = "missing dependency" if missing else "dependency failed"
            return TaskOutcome(
                task_id=task.id,
                agent=task.agent,
                status="blocked",
                error=f"{reason}: {', '.join(missing or blocked)}",
                elapsed_seconds=time.monotonic() - task_started,
            )
        if worker_transport == "mcp":
            return await _run_mcp_task(task)
        route_payload = None
        provider_override = None
        model_override = None
        if model_selection == "pinned":
            route_payload = {
                "provider": parent_provider or "",
                "model": parent_model or "",
                "transport": "native",
                "label": f"{parent_provider or 'parent'}/{parent_model or 'route'}",
                "reason": "model_selection=pinned",
            }
        else:
            try:
                decision = route_task(
                    task.task,
                    inventory=inventory,
                    config=config,
                    mode=orchestration_mode,
                    session_provider=parent_provider,
                    session_model=parent_model,
                    worker_count=1,
                )
                worker = decision.route_for("worker")
                route_payload = {
                    "provider": worker.profile.provider,
                    "model": worker.profile.model,
                    "transport": worker.transport,
                    "label": worker.label,
                    "planner": decision.planner.label,
                    "supervisor": decision.supervisor.label,
                    "reason": decision.reasons[0] if decision.reasons else "",
                }
                if worker.transport in {"native", "proxy"}:
                    provider_override = worker.profile.provider
                    model_override = worker.profile.model
                else:
                    warnings.append(
                        f"{task.id}: selected {worker.label}; MCP transport is not executed "
                        "by the in-process scheduler, using the inherited route"
                    )
            except Exception as error:
                record_failure("orchestration.run_plan.route", error)
                warnings.append(f"{task.id}: model routing fell back to the parent route")
        try:
            async with semaphore:
                result = await orchestrator.delegate(
                    task.agent,
                    task.task,
                    from_agent=from_agent,
                    parent_session_id=parent_session_id,
                    authority=authority,
                    expected_output=task.expected_output,
                    expected_artifact_path=task.expected_artifact_path,
                    provider_override=provider_override,
                    model_override=model_override,
                )
            return TaskOutcome(
                task_id=task.id,
                agent=task.agent,
                status=str(getattr(result, "status", "complete")),
                session_id=str(getattr(result, "session_id", "") or ""),
                response=str(getattr(result, "response", "") or ""),
                tokens=int(getattr(result, "tokens", 0) or 0),
                route=route_payload,
                elapsed_seconds=time.monotonic() - task_started,
                needs_approval=list(getattr(result, "needs_approval", []) or []),
            )
        except Exception as error:
            record_failure("orchestration.run_plan.task", error)
            return TaskOutcome(
                task_id=task.id,
                agent=task.agent,
                status="error",
                error=f"{type(error).__name__}: {error}",
                route=route_payload,
                elapsed_seconds=time.monotonic() - task_started,
            )

    for wave in waves(plan):
        results = await asyncio.gather(*(run_task(task) for task in wave))
        for outcome in results:
            outcomes[outcome.task_id] = outcome

    ordered = tuple(outcomes[task.id] for task in plan.tasks if task.id in outcomes)
    return PlanResult(
        plan=plan,
        outcomes=ordered,
        warnings=tuple(dict.fromkeys(warnings)),
        elapsed_seconds=time.monotonic() - started,
    )


__all__ = ["PlanResult", "TaskOutcome", "run_plan"]
