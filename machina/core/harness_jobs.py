"""Background external-harness jobs backed by a real child session.

A harness delegation normally blocks the turn until the external CLI exits.
This registry lets the model start one in the background, keep working, and
retrieve the bounded result later. Each job also creates a child chat session
(visible in Activity, openable with Enter) that records the task and result.
Jobs are in-process, per-session, capped, and never outlive Machina.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MAX_JOBS = 64
MAX_RUNNING_PER_SESSION = 8
MAX_STDOUT_CHARS = 16_000
MAX_STDERR_CHARS = 8_000

#: A completion that reads like a request for direction becomes a quick
#: "Harness is asking" notification instead of a silent result.
_QUESTION_HINTS = (
    "?",
    "which one",
    "could mean",
    "need more",
    "more direction",
    "what do you want",
    "can you clarify",
    "let me know",
)

#: Gateway listeners receive a job on start and on every terminal transition.
listeners: set[Callable[[HarnessJob], None]] = set()


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _looks_like_question(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _QUESTION_HINTS)


@dataclass
class HarnessJob:
    id: str
    owner_session_id: str
    session_id: str
    platform: str
    task: str
    timeout: float
    status: str = "running"
    needs_input: bool = False
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    exit_code: int | None = None
    command: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    error: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_session_id": self.owner_session_id,
            "session_id": self.session_id,
            "platform": self.platform,
            "task": self.task,
            "status": self.status,
            "needs_input": self.needs_input,
            "timeout": self.timeout,
            "started_at": _iso(self.started_at),
            "ended_at": _iso(self.ended_at),
            "exit_code": self.exit_code,
            "command": list(self.command),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
        }


_JOBS: dict[str, HarnessJob] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}


def _emit(job: HarnessJob) -> None:
    for listener in list(listeners):
        try:
            listener(job)
        except Exception:
            # A disconnected gateway must never break the worker.
            continue


def _prune() -> None:
    if len(_JOBS) <= MAX_JOBS:
        return
    finished = sorted(
        (job for job in _JOBS.values() if job.status != "running"),
        key=lambda job: job.ended_at or job.started_at,
    )
    for job in finished[: max(0, len(_JOBS) - MAX_JOBS)]:
        _JOBS.pop(job.id, None)


async def _create_child_session(
    owner_session_id: str, platform: str, task: str, cwd: Any
) -> str | None:
    """Create the openable child chat; DB absence never blocks the run."""
    try:
        from machina.core.session import session_manager

        parent: uuid.UUID | None
        try:
            parent = uuid.UUID(owner_session_id)
        except (ValueError, TypeError):
            parent = None
        state: dict[str, Any] = {
            "harness_platform": platform,
            "harness_status": "running",
        }
        if cwd is not None:
            state["desktop_workspace_root"] = str(cwd)
        session = await session_manager.create(
            title=f"{platform}: {task[:50]}",
            agent_id="harness",
            state=state,
            parent_session_id=parent,
        )
        return str(session.id)
    except Exception:
        return None


async def _append_message(session_id: str, role: str, content: str) -> None:
    try:
        from machina.core.context import context_manager

        await context_manager.add_chunk(
            uuid.UUID(session_id),
            "harness",
            f"{role}_message",
            {"content": content},
            token_count=max(1, len(content) // 4),
        )
    except Exception:
        # The child is an inspection aid; a store failure never fails the run.
        pass


async def _set_child_state(session_id: str, **fields: Any) -> None:
    try:
        from machina.core.session import session_manager

        await session_manager.update_state_fields(uuid.UUID(session_id), fields)
    except Exception:
        pass


async def start(
    *,
    session_id: str,
    platform: str,
    task: str,
    timeout: float,
    cwd: Any = None,
) -> dict[str, Any]:
    """Create the child chat, schedule the run, and return its snapshot."""
    if not session_id:
        raise ValueError("background harness jobs require a session")
    running = sum(
        1
        for job in _JOBS.values()
        if job.owner_session_id == session_id and job.status == "running"
    )
    if running >= MAX_RUNNING_PER_SESSION:
        raise ValueError(
            f"this session already has {running} background harness job(s); "
            "wait for one to finish first"
        )
    child_id = await _create_child_session(session_id, platform, task, cwd)
    job = HarnessJob(
        id=uuid.uuid4().hex[:12],
        owner_session_id=session_id,
        session_id=child_id or session_id,
        platform=platform,
        task=task,
        timeout=timeout,
    )
    _JOBS[job.id] = job
    _prune()
    if child_id:
        await _append_message(child_id, "user", f"[{platform}] {task}")
    asyncio.create_task(_run(job, cwd))
    _emit(job)
    return job.snapshot()


async def _run(job: HarnessJob, cwd: Any) -> None:
    try:
        from machina import interop

        result = await asyncio.to_thread(
            interop.run_task,
            job.platform,
            job.task,
            timeout=job.timeout,
            cwd=cwd,
        )
        job.exit_code = result.exit_code
        job.command = list(result.command)
        job.stdout = str(result.stdout or "")[:MAX_STDOUT_CHARS]
        job.stderr = str(result.stderr or "")[:MAX_STDERR_CHARS]
        job.status = "complete" if result.ok else "error"
        if not result.ok and not job.error:
            job.error = result.unsupported_reason or (
                job.stderr.splitlines()[-1] if job.stderr else "harness failed"
            )
        job.needs_input = bool(
            (job.stdout.strip() or job.error.strip())
            and _looks_like_question(job.stdout or job.error)
        )
    except asyncio.CancelledError:
        job.status = "stopped"
        job.error = "cancelled"
        raise
    except Exception as exc:  # spawn/transport failures stay in the record
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.ended_at = time.time()
        if job.session_id != job.owner_session_id:
            body = job.stdout or job.stderr or job.error or "(no output)"
            if job.status != "complete":
                body = f"{job.status}: {job.error or body}"
            await _append_message(job.session_id, "assistant", body)
            await _set_child_state(
                job.session_id,
                harness_status=job.status,
                harness_needs_input=job.needs_input,
                harness_job_id=job.id,
            )
        _TASKS.pop(job.id, None)
        _emit(job)


def get(job_id: str) -> dict[str, Any] | None:
    job = _JOBS.get(job_id)
    return job.snapshot() if job is not None else None


def list_jobs(session_id: str | None = None) -> list[dict[str, Any]]:
    jobs = [
        job for job in _JOBS.values() if session_id is None or job.owner_session_id == session_id
    ]
    jobs.sort(key=lambda job: job.started_at, reverse=True)
    return [job.snapshot() for job in jobs[:MAX_JOBS]]


def reset_for_tests() -> None:
    """Cancel running tasks and drop every record (test isolation)."""
    for task in list(_TASKS.values()):
        task.cancel()
    _TASKS.clear()
    _JOBS.clear()
    listeners.clear()
