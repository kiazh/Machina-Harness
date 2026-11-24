"""Background external-harness jobs.

A harness delegation normally blocks the turn until the external CLI exits.
This registry lets the model start one in the background, keep working, and
retrieve the bounded result later (Activity and ``harness_job``). Jobs are
in-process, per-session, capped, and never outlive the Machina process.
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

#: Gateway listeners receive a job on start and on every terminal transition.
listeners: set[Callable[[HarnessJob], None]] = set()


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).isoformat()


@dataclass
class HarnessJob:
    id: str
    session_id: str
    platform: str
    task: str
    timeout: float
    status: str = "running"
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
            "session_id": self.session_id,
            "platform": self.platform,
            "task": self.task,
            "status": self.status,
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


def start(
    *,
    session_id: str,
    platform: str,
    task: str,
    timeout: float,
    cwd: Any = None,
) -> dict[str, Any]:
    """Schedule one background harness run and return its initial snapshot."""
    if not session_id:
        raise ValueError("background harness jobs require a session")
    running = sum(
        1 for job in _JOBS.values() if job.session_id == session_id and job.status == "running"
    )
    if running >= MAX_RUNNING_PER_SESSION:
        raise ValueError(
            f"this session already has {running} background harness job(s); "
            "wait for one to finish first"
        )
    job = HarnessJob(
        id=uuid.uuid4().hex[:12],
        session_id=session_id,
        platform=platform,
        task=task,
        timeout=timeout,
    )
    _JOBS[job.id] = job
    _prune()
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
    except asyncio.CancelledError:
        job.status = "stopped"
        job.error = "cancelled"
        raise
    except Exception as exc:  # spawn/transport failures stay in the record
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.ended_at = time.time()
        _TASKS.pop(job.id, None)
        _emit(job)


def get(job_id: str) -> dict[str, Any] | None:
    job = _JOBS.get(job_id)
    return job.snapshot() if job is not None else None


def list_jobs(session_id: str | None = None) -> list[dict[str, Any]]:
    jobs = [job for job in _JOBS.values() if session_id is None or job.session_id == session_id]
    jobs.sort(key=lambda job: job.started_at, reverse=True)
    return [job.snapshot() for job in jobs[:MAX_JOBS]]


def reset_for_tests() -> None:
    """Cancel running tasks and drop every record (test isolation)."""
    for task in list(_TASKS.values()):
        task.cancel()
    _TASKS.clear()
    _JOBS.clear()
    listeners.clear()
