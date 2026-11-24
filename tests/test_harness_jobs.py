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
        lambda platform, task, timeout, cwd=None: SimpleNamespace(
            ok=True,
            exit_code=0,
            command=["hermes", "--oneshot=hi"],
            stdout="HARNESS_OK",
            stderr="",
            unsupported_reason="",
        ),
    )

    job = harness_jobs.start(session_id="session-a", platform="hermes", task="hi", timeout=30)
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
async def test_harness_delegate_background_requires_and_uses_the_session(monkeypatch):
    from machina.tools import agents as agent_tools

    started: dict = {}
    monkeypatch.setattr(
        harness_jobs,
        "start",
        lambda **kwargs: started.update(kwargs) or {"id": "job-1"},
    )
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
            {"id": job_id, "session_id": "session-a", "status": "complete"}
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
