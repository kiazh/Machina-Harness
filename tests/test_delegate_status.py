import asyncio
import uuid

import pytest

from machina.core import orchestrator as orch
from machina.permissions import store as permission_store
from machina.tools import agents


def test_delegate_status_waits_once_for_a_live_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_id = uuid.uuid4()

    async def scenario() -> tuple[str, asyncio.Task]:
        async def active_scope():
            return "harness", uuid.uuid4()

        async def latest(_parent, _child):
            return {"status": "complete", "response": "finished the brief"}

        monkeypatch.setattr(agents, "active_agent_scope", active_scope)
        monkeypatch.setattr(orch.orchestrator, "latest_child_delegation", latest)
        gate = asyncio.Event()

        async def worker():
            await gate.wait()

        task = asyncio.create_task(worker())
        orch._live_delegations[child_id] = task

        async def release():
            await asyncio.sleep(0.01)
            gate.set()

        asyncio.create_task(release())
        text = await agents.delegate_status(str(child_id), wait_seconds=1)
        return str(text), task

    try:
        text, task = asyncio.run(scenario())
        assert task.done(), "the bounded wait finished the live worker"
        assert "Worker complete" in text
        assert "finished the brief" in text
    finally:
        orch._live_delegations.pop(child_id, None)


def test_delegate_status_surfaces_child_pending_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_id = uuid.uuid4()

    async def scenario():
        async def active_scope():
            return "harness", uuid.uuid4()

        async def latest(_parent, _child):
            return {"status": "waiting_approval", "response": ""}

        async def pending(session_id):
            assert session_id == str(child_id)
            return [
                {
                    "request_id": "req-1",
                    "session_id": str(child_id),
                    "display": "run the test suite",
                    "proposal": {},
                }
            ]

        monkeypatch.setattr(agents, "active_agent_scope", active_scope)
        monkeypatch.setattr(orch.orchestrator, "latest_child_delegation", latest)
        monkeypatch.setattr(permission_store, "list_pending", pending)
        return await agents.delegate_status(str(child_id))

    result = asyncio.run(scenario())
    assert "Pending approval requests: req-1" in str(result)
    assert result.needs_approval[0]["request_id"] == "req-1"
    assert result.delegation_status == "waiting_approval"
