"""Background external-harness jobs backed by a real child session.

A harness delegation normally blocks the turn until the external CLI exits.
This registry lets the model start one in the background, keep working, and
retrieve the bounded result later. Each job also creates a child chat session
(visible in Activity, openable with Enter) that records the task and result.
Jobs are in-process, per-session, capped, and never outlive Machina.
"""

from __future__ import annotations

import asyncio
import threading
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


#: Provider text that describes a failed attempt the runner then retries.
#: Showing it in the live chat reads as a fatal error, so it is kept out of
#: the user-visible stream and persisted transcript; the run itself still
#: sees and reacts to it.
_TRANSIENT_FAILURE_HINTS = (
    "didn't answer after",
    "didn't answer",
    "temporarily unavailable",
    "send /retry",
    "switch models with /model",
    "add a backup provider",
    "fallback add",
)


def _is_transient_failure_line(line: str) -> bool:
    lowered = (line or "").lower()
    if any(hint in lowered for hint in _TRANSIENT_FAILURE_HINTS):
        return True
    try:
        from machina.interop import _is_credit_failure

        return _is_credit_failure(line)
    except Exception:
        return False


def _visible_output(text: str) -> str:
    """Drop retried-attempt failure prose from user-visible harness output."""
    if not text:
        return ""
    kept = [
        line
        for line in text.splitlines(keepends=True)
        if line.strip()
        and not _is_transient_failure_line(line)
        and not line.strip().lower().startswith("session_id:")
    ]
    visible = "".join(kept)
    if visible and not visible.endswith("\n"):
        # Redaction strips trailing whitespace; restore the separator so
        # streamed chunks concatenate without gluing lines together.
        visible += "\n"
    return visible


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
    persist: bool = False
    done: bool = False
    progress: list[str] = field(default_factory=list)
    on_progress: Callable[[str], None] | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_session_id": self.owner_session_id,
            "session_id": self.session_id,
            "platform": self.platform,
            "task": self.task,
            # A terminal status is published only once the run has flushed its
            # last progress and stored its transcript; polling never observes
            # a half-finished terminal state.
            "status": self.status if self.done else "running",
            "needs_input": self.needs_input,
            "timeout": self.timeout,
            "started_at": _iso(self.started_at),
            "ended_at": _iso(self.ended_at),
            "exit_code": self.exit_code,
            "command": list(self.command),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "progress": list(self.progress),
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
    reuse_session: bool = False,
    record_user: bool = True,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create the child chat (or reuse it), schedule the run, return snapshot."""
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
    child_id = (
        None if reuse_session else await _create_child_session(session_id, platform, task, cwd)
    )
    job = HarnessJob(
        id=uuid.uuid4().hex[:12],
        owner_session_id=session_id,
        session_id=child_id or session_id,
        platform=platform,
        task=task,
        timeout=timeout,
    )
    job.persist = bool(child_id or reuse_session)
    job.on_progress = on_progress
    _JOBS[job.id] = job
    _prune()
    if record_user and child_id:
        await _append_message(child_id, "user", f"[{platform}] {task}")
    asyncio.create_task(_run(job, cwd))
    _emit(job)
    return job.snapshot()


async def _run(job: HarnessJob, cwd: Any) -> None:
    pending: list[str] = []
    lock = threading.Lock()
    stop = threading.Event()

    def on_output(_source: str, text: str) -> None:
        with lock:
            pending.append(text)

    async def drain() -> None:
        carry = ""
        last_emit = 0.0
        while not stop.is_set():
            await asyncio.sleep(0.3)
            carry, emitted = _flush_progress(job, pending, lock, carry)
            now = time.monotonic()
            if emitted and now - last_emit >= 0.9:
                last_emit = now
                _emit(job)
        _flush_progress(job, pending, lock, carry, final=True)

    drain_task = asyncio.create_task(drain())
    final_status = "error"
    try:
        from machina import interop

        result = await asyncio.to_thread(
            interop.run_task,
            job.platform,
            job.task,
            timeout=job.timeout,
            cwd=cwd,
            on_output=on_output,
        )
        job.exit_code = result.exit_code
        job.command = list(result.command)
        job.stdout = str(result.stdout or "")[:MAX_STDOUT_CHARS]
        job.stderr = str(result.stderr or "")[:MAX_STDERR_CHARS]
        final_status = "complete" if result.ok else "error"
        if not result.ok and not job.error:
            job.error = result.unsupported_reason or (
                job.stderr.splitlines()[-1] if job.stderr else "harness failed"
            )
        job.needs_input = final_status == "complete" and bool(
            (job.stdout.strip() or job.error.strip())
            and _looks_like_question(job.stdout or job.error)
        )
    except asyncio.CancelledError:
        final_status = "stopped"
        job.error = "cancelled"
        raise
    except Exception as exc:  # spawn/transport failures stay in the record
        final_status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        stop.set()
        try:
            await asyncio.shield(drain_task)
        except asyncio.CancelledError:
            drain_task.cancel()
        except Exception:
            pass
        job.ended_at = time.time()
        if job.persist:
            answer = job.stdout or job.stderr or job.error or "(no output)"
            if final_status != "complete":
                answer = f"{final_status}: {job.error or answer}"
            log = "\n".join(job.progress).strip()
            if log and answer and answer.strip() not in log:
                body = f"{log}\n\n{answer}"
            else:
                body = log or answer
            await _append_message(job.session_id, "assistant", body[:MAX_STDOUT_CHARS])
            await _set_child_state(
                job.session_id,
                harness_status=final_status,
                harness_needs_input=job.needs_input,
                harness_job_id=job.id,
            )
        _TASKS.pop(job.id, None)
        job.status = final_status
        job.done = True
        _emit(job)


def _flush_progress(
    job: HarnessJob,
    pending: list[str],
    lock: threading.Lock,
    carry: str,
    *,
    final: bool = False,
) -> tuple[str, bool]:
    """Move complete output lines into the job snapshot; return carry/emitted."""
    with lock:
        text = carry + "".join(pending)
        pending.clear()
    if not text:
        return "", False
    if not final and "\n" not in text:
        return text, False
    head, _, carry = text.rpartition("\n")
    text = head + ("\n" if head else "") + (carry if final else "")
    if not text.strip():
        return carry, False
    try:
        from machina.interop import _redact_output

        text = _redact_output(text)
    except Exception:
        pass
    visible = _visible_output(text)
    lines = [line for line in visible.splitlines() if line.strip()]
    if lines:
        job.progress.extend(lines)
        job.progress[:] = job.progress[-80:]
    callback = job.on_progress
    if callback is not None and visible:
        try:
            callback(visible)
        except Exception:
            pass
    return carry, bool(lines)


async def wait(job_id: str, timeout: float = 300.0) -> dict[str, Any] | None:
    """Wait for one job to reach a terminal state (bounded)."""
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        if job.done:
            return job.snapshot()
        await asyncio.sleep(0.25)
    job = _JOBS.get(job_id)
    return job.snapshot() if job is not None else None


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
