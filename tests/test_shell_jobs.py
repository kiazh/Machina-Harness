import asyncio
import json
import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from machina.core.shell_jobs import ShellJobs


@pytest.mark.asyncio
async def test_foreground_detach_keeps_process_and_approved_deadline(tmp_path):
    jobs = ShellJobs()
    session = uuid4()
    job = await jobs.start(
        session,
        [sys.executable, "-c", "import time; print('working', flush=True); time.sleep(60)"],
        str(tmp_path),
        {},
        foreground=True,
        timeout=1,
    )
    try:
        pid = job.process.pid
        deadline = job.snapshot()["deadline"]
        assert job.snapshot()["foreground"] is True
        with pytest.raises(KeyError):
            jobs.detach(uuid4(), job.id)
        waiting = asyncio.create_task(jobs.wait_foreground(session, job.id))
        jobs.detach(session, job.id)
        result = await asyncio.wait_for(waiting, 2)
        assert result["id"] == job.id
        assert job.process.pid == pid
        assert job.snapshot()["deadline"] == deadline
        assert job.snapshot()["foreground"] is False
        await asyncio.wait_for(asyncio.shield(job.task), 10)
        assert job.process.poll() is not None
        assert job.timed_out
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_foreground_wait_returns_output_and_cancellation_stops_process(tmp_path):
    jobs = ShellJobs()
    session = uuid4()
    try:
        job = await jobs.start(
            session,
            [sys.executable, "-c", "print('done')"],
            str(tmp_path),
            {},
            foreground=True,
            timeout=10,
        )
        result = await jobs.wait_foreground(session, job.id)
        assert "done" in result["output"]
        assert result["returncode"] == 0
        assert not jobs.drain_updates(session), (
            "foreground completions already return a tool result"
        )
        job = await jobs.start(
            session,
            [sys.executable, "-c", "import time; time.sleep(60)"],
            str(tmp_path),
            {},
            foreground=True,
            timeout=10,
        )
        waiting = asyncio.create_task(jobs.wait_foreground(session, job.id))
        await asyncio.sleep(0.01)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert job.process.poll() is not None
    finally:
        await jobs.close()


def test_terminal_capability_exposes_job_controls_without_granting_new_launch_tools():
    from machina.core.agent import ReActAgent

    terminal_agent = ReActAgent(provider=SimpleNamespace(), allowed_tools=["terminal"])
    names = {definition.name for definition in terminal_agent._tool_defs()}
    assert names == {"terminal", "terminal_job"}
    reader = ReActAgent(provider=SimpleNamespace(), allowed_tools=["read_file"])
    assert "terminal_job" not in {definition.name for definition in reader._tool_defs()}


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object descendant accounting")
async def test_windows_job_waits_for_descendants_after_launcher_exits(tmp_path):
    marker = tmp_path / "child-finished"
    release = tmp_path / "release-child"
    child = tmp_path / "child.py"
    child.write_text(
        f"import time\nfrom pathlib import Path\nwhile not Path({str(release)!r}).exists(): time.sleep(.02)\nPath({str(marker)!r}).write_text('done')"
    )
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        f"import subprocess,sys; subprocess.Popen([sys._base_executable, {str(child)!r}], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)"
    )
    jobs = ShellJobs()
    job = await jobs.start(uuid4(), [sys.executable, str(launcher)], str(tmp_path), {})
    try:
        for _ in range(500):
            if job.process.poll() is not None:
                break
            await asyncio.sleep(0.02)
        assert job.process.poll() is not None
        assert not marker.exists(), "the descendant must still be working when the launcher exits"
        assert job.status == "running"
        release.touch()
        await asyncio.wait_for(asyncio.shield(job.task), 15)
        assert marker.exists()
        assert job.status == "exited"
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_background_job_returns_before_exit_and_retains_bounded_output(tmp_path):
    jobs = ShellJobs()
    session = uuid4()
    job = await jobs.start(
        session,
        [
            sys.executable,
            "-c",
            "import time; print('start', flush=True); time.sleep(.2); print('done')",
        ],
        str(tmp_path),
        {},
    )
    assert jobs.get(session, job.id).status == "running"
    with pytest.raises(KeyError):
        jobs.get(uuid4(), job.id)
    parent = uuid4()
    jobs.transfer(session, parent)
    assert jobs.get(parent, job.id) is job
    await asyncio.wait_for(asyncio.shield(job.task), 15)
    assert job.status == "exited"
    assert "done" in job.output
    assert jobs.drain_updates(parent)[0]["returncode"] == 0
    assert jobs.drain_updates(parent) == []
    await jobs.close()


@pytest.mark.asyncio
async def test_noisy_background_command_keeps_running_with_only_a_tail(tmp_path):
    jobs = ShellJobs()
    session = uuid4()
    job = await jobs.start(
        session, [sys.executable, "-c", "print('x' * 100000); print('end')"], str(tmp_path), {}
    )
    await asyncio.wait_for(asyncio.shield(job.task), 15)
    assert job.status == "exited"
    assert job.truncated
    assert len(job.output) <= 64_000
    assert job.output.rstrip().endswith("end")
    await jobs.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap admission gate")
async def test_cancel_during_background_spawn_cannot_release_the_command(tmp_path, monkeypatch):
    from machina.core import shell_jobs as module

    original = module.subprocess.Popen
    spawned = []
    started, release = threading.Event(), threading.Event()

    def delayed(*args, **kwargs):
        process = original(*args, **kwargs)
        spawned.append(process)
        started.set()
        release.wait(3)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", delayed)
    marker = tmp_path / "should-not-exist"
    script = tmp_path / "command.py"
    script.write_text(f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')")
    jobs = ShellJobs()
    task = asyncio.create_task(
        jobs.start(uuid4(), [sys.executable, str(script)], str(tmp_path), {})
    )
    await asyncio.to_thread(started.wait, 3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 20)
    assert spawned[0].poll() is not None
    assert not marker.exists()
    assert not jobs.jobs


@pytest.mark.asyncio
async def test_shutdown_joins_a_command_still_being_spawned(tmp_path, monkeypatch):
    from machina.core import shell_jobs as module

    original = module.subprocess.Popen
    spawned = []
    started, release = threading.Event(), threading.Event()

    def delayed(*args, **kwargs):
        process = original(*args, **kwargs)
        spawned.append(process)
        started.set()
        release.wait(3)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", delayed)
    jobs = ShellJobs()
    launch = asyncio.create_task(
        jobs.start(
            uuid4(), [sys.executable, "-c", "import time; time.sleep(60)"], str(tmp_path), {}
        )
    )
    await asyncio.to_thread(started.wait, 3)
    closing = asyncio.create_task(jobs.close())
    await asyncio.sleep(0)
    assert jobs.closing
    release.set()
    with pytest.raises(RuntimeError, match="shutting down"):
        await asyncio.wait_for(launch, 20)
    await asyncio.wait_for(closing, 20)
    assert spawned[0].poll() is not None
    assert not jobs.jobs


@pytest.mark.asyncio
async def test_background_job_can_be_stopped(tmp_path):
    jobs = ShellJobs()
    session = uuid4()
    job = await jobs.start(
        session, [sys.executable, "-c", "import time; time.sleep(60)"], str(tmp_path), {}
    )
    await jobs.stop(session, job.id)
    assert job.status == "stopped"
    assert job.process.poll() is not None
    await jobs.close()


@pytest.mark.asyncio
async def test_terminal_background_and_observation_share_the_session(tmp_path, monkeypatch):
    from machina.core.shell_jobs import shell_jobs

    shell_jobs.reopen()
    from machina.tools import terminal as module
    from machina.tools.agents import current_session_id

    monkeypatch.setattr(module, "_call_mode", lambda: "full")
    monkeypatch.setattr(
        module,
        "_approved_snapshot",
        lambda: {
            "operation": "process.exec",
            "argv": [sys.executable, "-c", "print(123)"],
            "shell_payload": "",
            "cwd": str(tmp_path),
            "backend": "local",
            "background": True,
            "timeout": 0,
        },
    )
    monkeypatch.setattr(module, "_validate_workdir", lambda _: None)
    monkeypatch.setattr(
        "machina.core.execution_context.execution_context",
        lambda _: SimpleNamespace(workspace=tmp_path, mode="full"),
    )
    token = current_session_id.set(uuid4())
    try:
        receipt = json.loads(
            await module.terminal(
                f'"{sys.executable}" -c "print(123)"', workdir=str(tmp_path), background=True
            )
        )
        job = shell_jobs.get(current_session_id.get(), receipt["id"])
        await asyncio.wait_for(asyncio.shield(job.task), 15)
        result = json.loads(await module.terminal_job("status", job.id))
        assert result["returncode"] == 0
        assert "123" in result["output"]
    finally:
        current_session_id.reset(token)
        await shell_jobs.close()


def test_background_mode_has_a_distinct_approval_identity(tmp_path):
    from machina.permissions.policy import build_request
    from machina.permissions.tools import action_for_tool

    foreground = action_for_tool("terminal", {"command": "git status", "workdir": str(tmp_path)})
    background = action_for_tool(
        "terminal", {"command": "git status", "workdir": str(tmp_path), "background": True}
    )
    fg = build_request(**foreground, mode="full", tool="terminal")
    bg = build_request(**background, mode="full", tool="terminal")
    assert fg.digest != bg.digest
    assert bg.timeout == 0
    assert "background-process" in bg.capabilities


@pytest.mark.asyncio
async def test_foreground_approval_cannot_launch_an_unlimited_background_command(monkeypatch):
    from machina.core.exceptions import ValidationError
    from machina.tools import terminal as module

    monkeypatch.setattr(module, "_call_mode", lambda: "full")
    monkeypatch.setattr(
        module,
        "_approved_snapshot",
        lambda: {
            "operation": "process.exec",
            "argv": ["git", "status"],
            "shell_payload": "",
            "background": False,
            "timeout": 60,
        },
    )
    with pytest.raises(ValidationError, match="background mode"):
        await module.terminal("git status", background=True)


@pytest.mark.asyncio
async def test_background_admission_can_take_longer_than_one_second(monkeypatch, tmp_path):
    from machina.core import agent as module
    from machina.core.models import LLMResponse

    async def slow_launch(*args, **kwargs):
        await asyncio.sleep(1.2)
        return "background job admitted"

    monkeypatch.setattr(module.registry, "execute", AsyncMock(side_effect=slow_launch))
    monkeypatch.setattr(module.plugin_registry, "dispatch", AsyncMock())
    monkeypatch.setattr("machina.permissions.broker.permission_broker.guard", AsyncMock())
    monkeypatch.setattr(
        "machina.core.execution_context.resolve_execution_context",
        AsyncMock(return_value=SimpleNamespace(mode="full", backend="host", workspace=tmp_path)),
    )
    agent = SimpleNamespace(
        agent_id="harness",
        allowed_tools=["terminal"],
        _ensure_artifact_view=AsyncMock(return_value=""),
        _record_tool_action=AsyncMock(),
        _append_tool_messages=lambda *args: None,
        _finalize_completed_tools=AsyncMock(),
    )
    response = LLMResponse(
        model="test",
        content="",
        tool_calls=[
            {
                "id": "bg",
                "function": {
                    "name": "terminal",
                    "arguments": '{"command":"echo test","background":true}',
                },
            }
        ],
    )
    events = [
        event
        async for event in module.ReActAgent._execute_tool_calls_stream(
            agent, response, [], [], uuid4(), []
        )
    ]
    assert any(
        event.tool_result == "background job admitted"
        for event in events
        if event.type == "tool_result"
    )
