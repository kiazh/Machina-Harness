"""Session-owned background subprocesses with bounded output tails."""

from __future__ import annotations

import asyncio
import atexit
import codecs
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from machina.core import child_process

MAX_TAIL = 64_000


@dataclass
class ShellJob:
    id: str
    session_id: UUID
    command: str
    process: Any
    started: float = field(default_factory=time.time)
    status: str = "running"
    output: str = ""
    truncated: bool = False
    task: Any = None
    cleanup: Any = None
    announced: tuple | None = None
    windows_handle: int | None = None
    foreground: bool = False
    deadline: float | None = None
    timed_out: bool = False
    detached: asyncio.Event = field(default_factory=asyncio.Event)
    output_limit: asyncio.Event = field(default_factory=asyncio.Event)
    guard: Any = None

    def snapshot(self, include_output: bool = False) -> dict:
        from machina.memory.redaction import redact_diagnostic

        result = {
            "id": self.id,
            "command": redact_diagnostic(self.command),
            "status": self.status,
            "returncode": self.process.poll(),
            "started": self.started,
            "truncated": self.truncated,
            "foreground": self.foreground,
            "deadline": self.deadline,
            "timedOut": self.timed_out,
        }
        if include_output:
            result["output"] = redact_diagnostic(self.output)
        return result


def _resolve_windows_launch_args(
    args: list[str], env: dict[str, str], *, windows: bool | None = None
) -> list[str]:
    """Resolve the first argv against the child PATH when Windows will wrap it."""
    launch_args = list(args)
    if not launch_args or not (os.name == "nt" if windows is None else windows):
        return launch_args
    path_value = next((value for key, value in env.items() if key.upper() == "PATH"), "")
    resolved = shutil.which(launch_args[0], path=path_value)
    if resolved:
        launch_args[0] = resolved
    return launch_args


class ShellJobs:
    def __init__(self):
        self.jobs: dict[str, ShellJob] = {}
        self.listeners: set = set()
        self.starting = 0
        self.updates: dict[UUID, list[dict]] = {}
        self.closing = False
        self.admissions: set[asyncio.Future] = set()

    def reopen(self) -> None:
        if self.admissions or any(
            job.status in {"running", "stopping"} for job in self.jobs.values()
        ):
            raise RuntimeError("background commands are still shutting down")
        self.closing = False

    def notify(self, job: ShellJob) -> None:
        from machina.observability.diagnostics import record_failure

        marker = (job.session_id, job.status)
        if (
            not job.foreground
            and job.status in {"exited", "failed", "stopped"}
            and job.announced != marker
        ):
            pending = self.updates.setdefault(job.session_id, [])
            pending.append({"id": job.id, "status": job.status, "returncode": job.process.poll()})
            del pending[:-128]
            job.announced = marker
        for listener in list(self.listeners):
            try:
                listener(job)
            except Exception as error:
                record_failure("shell_jobs.notify", error)

    async def start(
        self,
        session_id: UUID,
        args: list[str],
        cwd: str,
        env: dict,
        command: str = "",
        cleanup=None,
        *,
        foreground: bool = False,
        timeout: int | None = None,
    ) -> ShellJob:
        if self.closing:
            raise RuntimeError("background commands are shutting down")
        retired = asyncio.get_running_loop().create_future()
        self.admissions.add(retired)
        try:
            return await self._start(
                session_id, args, cwd, env, command, cleanup, foreground, timeout
            )
        finally:
            self.admissions.discard(retired)
            if not retired.done():
                retired.set_result(None)

    async def _start(
        self,
        session_id: UUID,
        args: list[str],
        cwd: str,
        env: dict,
        command: str = "",
        cleanup=None,
        foreground: bool = False,
        timeout: int | None = None,
    ) -> ShellJob:
        if (
            self.starting + sum(job.status in {"running", "stopping"} for job in self.jobs.values())
            >= 16
        ):
            raise ValueError("16 background commands are already running")
        # Bound retained completed jobs; never evict a running process handle.
        for key in list(self.jobs):
            if len(self.jobs) < 128:
                break
            if self.jobs[key].status not in {"running", "stopping"}:
                del self.jobs[key]
        kwargs = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        self.starting += 1
        launch_args = _resolve_windows_launch_args(args, env)
        if os.name == "nt":
            # Windows CreateProcess searches the launcher's directory before
            # PATH. The launcher is Machina's interpreter, so a bare `python`
            # or `pytest` would otherwise resolve to the host install instead
            # of the task's virtualenv. _resolve_windows_launch_args resolves
            # names against the child PATH before the bootstrap.
            bootstrap = (
                "import subprocess,sys; gate=sys.stdin.buffer.read(1); "
                "raise SystemExit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL) "
                "if gate == bytes([0]) else 1)"
            )
            launch_args = [sys.executable, "-c", bootstrap, *launch_args]
        launch = asyncio.create_task(
            asyncio.to_thread(
                subprocess.Popen,
                launch_args,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE if os.name == "nt" else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **kwargs,
            )
        )
        try:
            process = await asyncio.shield(launch)
        except asyncio.CancelledError:
            process = await asyncio.shield(launch)
            from machina.core.background_tasks import await_joined

            await await_joined(self._retire_unadmitted(process, cleanup))
            raise
        finally:
            self.starting -= 1
        if self.closing:
            await self._retire_unadmitted(process, cleanup)
            raise RuntimeError("background commands are shutting down")
        job = ShellJob(
            uuid4().hex[:12],
            session_id,
            command or " ".join(args),
            process,
            cleanup=cleanup,
            foreground=foreground,
            deadline=time.time() + timeout if timeout else None,
        )
        if os.name == "nt":
            from machina.core.windows_job import create_script_job

            try:
                job.windows_handle = create_script_job(process.pid)
                process.stdin.write(b"\x00")
                process.stdin.flush()
                process.stdin.close()
            except BaseException:
                if job.windows_handle is not None:
                    from machina.core.windows_job import close_script_job

                    close_script_job(job.windows_handle)
                await self._retire_unadmitted(process, cleanup)
                raise
        self.jobs[job.id] = job
        loop = asyncio.get_running_loop()

        def read():
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            try:
                while data := process.stdout.read1(4096):
                    text = decoder.decode(data)
                    combined = job.output + text
                    job.truncated |= len(combined) > MAX_TAIL
                    job.output = combined[-MAX_TAIL:]
                    if job.foreground and job.truncated:
                        loop.call_soon_threadsafe(job.output_limit.set)
                job.output = (job.output + decoder.decode(b"", final=True))[-MAX_TAIL:]
                process.wait()
                if os.name == "nt":
                    from machina.core.windows_job import active_script_processes

                    while job.windows_handle is not None:
                        try:
                            if active_script_processes(job.windows_handle) == 0:
                                break
                        except OSError:
                            if job.windows_handle is None or job.status in {"stopping", "stopped"}:
                                break
                            raise
                        time.sleep(0.05)
                else:
                    while child_process.group_members(process.pid, process):
                        time.sleep(0.05)
            finally:
                process.stdout.close()

        finished = asyncio.Event()
        loop = asyncio.get_running_loop()
        read_errors = []

        def reader():
            try:
                read()
            except Exception as error:
                read_errors.append(error)
            finally:
                try:
                    loop.call_soon_threadsafe(finished.set)
                except RuntimeError:
                    pass

        threading.Thread(target=reader, name=f"shell-{job.id}", daemon=True).start()

        async def watch():
            try:
                await finished.wait()
                if read_errors:
                    raise read_errors[0]
                if job.status == "running":
                    job.status = "exited" if process.returncode == 0 else "failed"
            except Exception as error:
                await asyncio.to_thread(
                    child_process.kill_tree, process, None if os.name == "nt" else process.pid
                )
                job.status = "failed"
                job.output = (job.output + f"\n{type(error).__name__}")[-MAX_TAIL:]
            finally:
                if job.windows_handle is not None:
                    from machina.core.windows_job import close_script_job

                    close_script_job(job.windows_handle)
                    job.windows_handle = None
                self.notify(job)

        job.task = asyncio.create_task(watch())
        if timeout:

            async def enforce_limits():
                limit = asyncio.create_task(job.output_limit.wait())
                try:
                    done, _ = await asyncio.wait(
                        {job.task, limit}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                    )
                    if job.task not in done and job.status == "running":
                        job.timed_out = limit not in done
                        await self.stop(job.session_id, job.id)
                finally:
                    limit.cancel()
                    await asyncio.gather(limit, return_exceptions=True)

            job.guard = asyncio.create_task(enforce_limits())
        self.notify(job)
        return job

    def detach(self, session_id: UUID, job_id: str) -> ShellJob:
        job = self.get(session_id, job_id)
        if job.status == "running" and job.foreground:
            # User releases the waiter only; the original deadline still applies.
            job.foreground = False
            job.detached.set()
            self.notify(job)
        return job

    async def wait_foreground(self, session_id: UUID, job_id: str) -> dict:
        job = self.get(session_id, job_id)
        detached = asyncio.create_task(job.detached.wait())
        try:
            await asyncio.wait({job.task, detached}, return_when=asyncio.FIRST_COMPLETED)
            if job.detached.is_set():
                return job.snapshot()
            if job.guard:
                await asyncio.shield(job.guard)
            return job.snapshot(include_output=True)
        except asyncio.CancelledError:
            from machina.core.background_tasks import await_joined

            if job.foreground:
                await await_joined(self.stop(session_id, job_id))
            raise
        finally:
            detached.cancel()
            await asyncio.gather(detached, return_exceptions=True)

    async def _retire_unadmitted(self, process, cleanup) -> None:
        await asyncio.to_thread(
            child_process.kill_tree, process, None if os.name == "nt" else process.pid
        )
        try:
            await asyncio.to_thread(process.wait, timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=5)
        finally:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if cleanup:
                await asyncio.to_thread(cleanup)

    def get(self, session_id: UUID, job_id: str) -> ShellJob:
        job = self.jobs[job_id]
        if job.session_id != session_id:
            raise KeyError("background command does not belong to this session")
        return job

    def list(self, session_id: UUID) -> list[dict]:
        return [job.snapshot() for job in self.jobs.values() if job.session_id == session_id]

    def transfer(self, child_id: UUID, parent_id: UUID) -> None:
        for job in self.jobs.values():
            if job.session_id == child_id:
                job.session_id = parent_id
                self.notify(job)
        self.updates.pop(child_id, None)

    def drain_updates(self, session_id: UUID) -> list[dict]:
        return self.updates.pop(session_id, [])

    async def stop(self, session_id: UUID, job_id: str) -> ShellJob:
        job = self.get(session_id, job_id)
        if job.status in {"running", "stopping"}:
            job.status = "stopping"
            if job.windows_handle is not None:
                from machina.core.windows_job import close_script_job

                close_script_job(job.windows_handle)
                job.windows_handle = None
            else:
                await asyncio.to_thread(
                    child_process.kill_tree,
                    job.process,
                    None if os.name == "nt" else job.process.pid,
                )
            if job.cleanup:
                await asyncio.to_thread(job.cleanup)
            await asyncio.wait_for(asyncio.shield(job.task), 10)
            if job.process.poll() is None:
                raise RuntimeError("background command is still stopping")
            job.status = "failed" if job.timed_out else "stopped"
            self.notify(job)
        return job

    async def close(self) -> None:
        self.closing = True
        if self.admissions:
            await asyncio.gather(*list(self.admissions))
        results = await asyncio.gather(
            *(
                self.stop(job.session_id, job.id)
                for job in list(self.jobs.values())
                if job.status in {"running", "stopping"}
            ),
            return_exceptions=True,
        )
        from machina.observability.diagnostics import record_failure

        for result in results:
            if isinstance(result, Exception):
                record_failure("shell_jobs.close", result)
        await asyncio.gather(
            *(job.guard for job in self.jobs.values() if job.guard), return_exceptions=True
        )

    def close_sync(self) -> None:
        """Process-exit fallback for callers without an async shutdown hook."""
        for job in list(self.jobs.values()):
            try:
                if job.windows_handle is not None:
                    from machina.core.windows_job import close_script_job

                    close_script_job(job.windows_handle)
                    job.windows_handle = None
                elif job.status in {"running", "stopping"}:
                    child_process.kill_tree(
                        job.process, None if os.name == "nt" else job.process.pid
                    )
                if job.status in {"running", "stopping"} and job.cleanup:
                    job.cleanup()
            except Exception:
                pass


shell_jobs = ShellJobs()
atexit.register(shell_jobs.close_sync)


def apply_job_updates(session_id: UUID, messages: list) -> None:
    updates = shell_jobs.drain_updates(session_id)
    if updates:
        # Only trusted exit metadata enters the prompt here. Command output
        # stays tool data, fetched explicitly through terminal_job.
        lines = [
            f"{item['id']}: {item['status']}, exit code {item['returncode']}" for item in updates
        ]
        messages.append(
            {
                "role": "user",
                "content": "[Harness background command status]\n"
                + "\n".join(lines)
                + "\nInspect relevant output with terminal_job before reporting results.",
            }
        )
