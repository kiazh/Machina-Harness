"""Gateway child observation streams progress without changing other callers."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from machina.core import orchestrator as module
from machina.core.agent_def import AgentDef
from machina.core.models import AgentResponse, Session, StreamEvent
from machina.core.orchestrator import Orchestrator, child_observer


@pytest.mark.asyncio
@pytest.mark.parametrize("pause", [False, True])
async def test_gateway_observer_receives_live_child_and_settlement(monkeypatch, pause):
    child = Session(id=uuid4(), agent_id="coder")
    monkeypatch.setattr(
        module.agent_registry, "get", AsyncMock(return_value=AgentDef(name="coder"))
    )
    monkeypatch.setattr(module.session_manager, "create", AsyncMock(return_value=child))
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(module.session_manager, "delete", delete)
    monkeypatch.setattr(
        "machina.core.session_mode.resolve_effective_mode", AsyncMock(return_value="ask")
    )
    monkeypatch.setattr(module.context_manager, "add_chunk", AsyncMock())
    monkeypatch.setattr(
        "machina.core.usage.usage_store.summary",
        AsyncMock(return_value={"session": {"chargedTokens": 4}}),
    )

    class ChildAgent:
        async def run(self, *_args, **_kwargs):
            raise AssertionError("observed child must use the streaming path")

        async def run_stream(self, *_args, **_kwargs):
            yield StreamEvent(type="text", content="Found a file.")
            yield StreamEvent(
                type="done",
                response=AgentResponse(
                    content="Found a file.",
                    tokens_used=4,
                    needs_approval=[{"reason": "review"}] if pause else [],
                ),
            )

    orchestration = Orchestrator(agent_factory=lambda _: ChildAgent())
    monkeypatch.setattr(orchestration, "_record_start", AsyncMock(return_value=uuid4()))
    monkeypatch.setattr(orchestration, "_record_end", AsyncMock())
    seen = []
    token = child_observer.set(
        lambda session_id, phase, value: seen.append((session_id, phase, value))
    )
    try:
        result = await orchestration.delegate("coder", "find the file")
    finally:
        child_observer.reset(token)
    assert result.status == ("waiting_approval" if pause else "complete")
    assert [phase for _, phase, _ in seen] == [
        "start",
        "text",
        "done",
        "paused" if pause else "settled",
    ]
    assert all(session_id == child.id for session_id, _, _ in seen)
    assert seen[1][2] == "Found a file."
    if pause:
        delete.assert_not_awaited()
    else:
        delete.assert_awaited_once_with(child.id)
