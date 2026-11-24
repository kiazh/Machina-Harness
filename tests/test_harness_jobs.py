"""Background harness jobs: start, complete, and per-session isolation."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from machina.core import harness_jobs
from machina.tools import meta_harness


@pytest.fixture(autouse=True)
def _reset_jobs():
    harness_jobs.reset_for_tests()
    yield
    harness_jobs.reset_for_tests()


def test_question_like_harness_output_is_flagged_for_notification():
    assert harness_jobs._looks_like_question(
        "I need a bit more direction here. Research planes could mean a few things — which one?"
    )
    assert not harness_jobs._looks_like_question("Research complete. Three sources summarized.")


def test_harness_workspace_falls_back_when_the_session_has_no_pin(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from machina.gateway.features import interop as interop_feature

    monkeypatch.setattr(interop_feature, "configured_workspace", lambda: tmp_path)
    assert interop_feature._workspace_cwd(SimpleNamespace(state={})) == tmp_path.resolve()
    assert (
        interop_feature._workspace_cwd(
            SimpleNamespace(state={"desktop_workspace_root": str(tmp_path)})
        )
        == tmp_path.resolve()
    )


@pytest.mark.asyncio
async def test_background_job_records_bounded_result(monkeypatch):
    from machina import interop

    seen: list[dict] = []
    harness_jobs.listeners.add(lambda job: seen.append(job.snapshot()))
    monkeypatch.setattr(
        interop,
        "run_task",
        lambda platform, task, timeout, cwd=None, on_output=None, resume=None, on_session_id=None: (
            SimpleNamespace(
                ok=True,
                exit_code=0,
                command=["hermes", "--oneshot=hi"],
                stdout="HARNESS_OK",
                stderr="",
                unsupported_reason="",
            )
        ),
    )

    job = await harness_jobs.start(session_id="session-a", platform="hermes", task="hi", timeout=30)
    assert job["status"] == "running"

    for _ in range(100):
        current = harness_jobs.get(job["id"])
        if current and current["status"] != "running":
            break
        await asyncio.sleep(0.01)

    current = harness_jobs.get(job["id"])
    assert current is not None
    assert current["status"] == "complete"
    assert current["stdout"] == "HARNESS_OK"
    assert current["exit_code"] == 0
    assert [item["status"] for item in seen] == ["running", "complete"]
    assert harness_jobs.list_jobs("session-a")
    assert harness_jobs.list_jobs("session-b") == []


@pytest.mark.asyncio
async def test_background_job_retains_live_output_lines(monkeypatch):
    from machina import interop

    def fake_run(
        platform, task, timeout, cwd=None, on_output=None, resume=None, on_session_id=None
    ):
        if on_output is not None:
            on_output("stdout", "thinking...\nstep 1\n")
        return SimpleNamespace(
            ok=True,
            exit_code=0,
            command=["hermes"],
            stdout="done\n",
            stderr="",
            unsupported_reason="",
        )

    monkeypatch.setattr(interop, "run_task", fake_run)
    job = await harness_jobs.start(session_id="session-a", platform="hermes", task="hi", timeout=30)
    for _ in range(80):
        cur = harness_jobs.get(job["id"])
        if cur and cur["status"] != "running":
            break
        await asyncio.sleep(0.02)
    current = harness_jobs.get(job["id"])
    assert current is not None
    assert current["status"] == "complete"
    assert any("thinking" in line for line in current["progress"])


@pytest.mark.asyncio
async def test_background_job_forwards_the_resume_session(monkeypatch):
    from machina import interop

    seen: dict = {}

    def fake_run(
        platform, task, timeout, cwd=None, on_output=None, resume=None, on_session_id=None
    ):
        seen["resume"] = resume
        if on_session_id is not None:
            on_session_id("sess-new")
        return SimpleNamespace(
            ok=True,
            exit_code=0,
            command=["hermes"],
            stdout="ok",
            stderr="",
            unsupported_reason="",
        )

    monkeypatch.setattr(interop, "run_task", fake_run)
    job = await harness_jobs.start(
        session_id="session-a",
        platform="hermes",
        task="follow up",
        timeout=30,
        resume_session="sess-old",
    )
    for _ in range(80):
        current = harness_jobs.get(job["id"])
        if current and current["status"] != "running":
            break
        await asyncio.sleep(0.02)

    assert seen["resume"] == "sess-old"
    assert harness_jobs.get(job["id"])["status"] == "complete"


@pytest.mark.asyncio
async def test_harness_delegate_background_requires_and_uses_the_session(monkeypatch):
    from machina.tools import agents as agent_tools

    started: dict = {}

    async def _start(**kwargs):
        started.update(kwargs)
        return {"id": "job-1"}

    monkeypatch.setattr(harness_jobs, "start", _start)
    token = agent_tools.current_session_id.set("session-a")
    try:
        payload = json.loads(
            await meta_harness.harness_delegate("hi", platform="hermes", background=True)
        )
    finally:
        agent_tools.current_session_id.reset(token)

    assert payload["status"] == "started"
    assert payload["job_id"] == "job-1"
    assert started["session_id"] == "session-a"
    assert started["platform"] == "hermes"


@pytest.mark.asyncio
async def test_harness_job_reads_only_the_current_sessions_jobs(monkeypatch):
    from machina.tools import agents as agent_tools

    monkeypatch.setattr(
        harness_jobs,
        "list_jobs",
        lambda session: [{"id": "job-1", "session_id": session, "status": "complete"}],
    )
    monkeypatch.setattr(
        harness_jobs,
        "get",
        lambda job_id: (
            {
                "id": job_id,
                "owner_session_id": "session-a",
                "session_id": "child-a",
                "status": "complete",
            }
            if job_id == "job-1"
            else None
        ),
    )
    token = agent_tools.current_session_id.set("session-a")
    try:
        listed = json.loads(await meta_harness.harness_job("list"))
        status = json.loads(await meta_harness.harness_job("status", "job-1"))
        with pytest.raises(Exception):
            await meta_harness.harness_job("status", "missing")
    finally:
        agent_tools.current_session_id.reset(token)

    assert listed["jobs"][0]["id"] == "job-1"
    assert status["status"] == "complete"
