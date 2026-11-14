"""Follow-ups reuse only a child owned by the requesting main session."""

import uuid
from types import SimpleNamespace

import pytest

from machina.core.agent_def import BUILTIN_AGENTS, agent_registry
from machina.core.config import config
from machina.core.models import Session
from machina.core.orchestrator import Orchestrator
from machina.core.session import session_manager


@pytest.mark.asyncio
async def test_followup_reuses_child_session_and_rejects_another_parent(monkeypatch):
    parent_id = uuid.uuid4()
    child = Session(
        id=uuid.uuid4(),
        agent_id="researcher",
        parent_session_id=parent_id,
        delegation_authority={"max_mode": "ask"},
    )

    async def get_agent(_name):
        return BUILTIN_AGENTS["researcher"]

    async def get_session(_session_id):
        return child

    async def mode(_session_id):
        return "ask"

    async def no_op(*_args, **_kwargs):
        return None

    class FakeAgent:
        async def run(self, session_id, _task, **_kwargs):
            assert session_id == child.id
            return SimpleNamespace(
                content="Found the answer",
                tokens_used=9,
                iterations=1,
                needs_approval=[],
                outcome="completed",
            )

    from machina.core import orchestrator as module
    from machina.core import session_mode

    monkeypatch.setattr(agent_registry, "get", get_agent)
    monkeypatch.setattr(session_manager, "get_fresh", get_session)
    monkeypatch.setattr(session_manager, "create", no_op)
    monkeypatch.setattr(session_mode, "resolve_effective_mode", mode)
    monkeypatch.setattr(module, "join_agent_background_tasks", no_op)
    monkeypatch.setattr(module.context_manager, "add_chunk", no_op)

    orchestrator = Orchestrator(agent_factory=lambda _definition: FakeAgent())
    monkeypatch.setattr(orchestrator, "_record_start", lambda *_args, **_kwargs: no_op())
    monkeypatch.setattr(orchestrator, "_record_status", no_op)
    monkeypatch.setattr(orchestrator, "_record_end", no_op)

    async def charged(*_args):
        return 9

    monkeypatch.setattr(orchestrator, "_settle_child_tokens", charged)
    result = await orchestrator.delegate(
        "researcher",
        "Check one more source",
        parent_session_id=parent_id,
        resume_session_id=child.id,
    )
    assert result.session_id == child.id
    assert result.response == "Found the answer"

    with pytest.raises(ValueError, match="not this parent's delegated agent"):
        await orchestrator.delegate(
            "researcher",
            "Unauthorized follow-up",
            parent_session_id=uuid.uuid4(),
            resume_session_id=child.id,
        )


@pytest.mark.asyncio
async def test_fresh_child_inherits_the_parent_provider_route(monkeypatch):
    parent_id = uuid.uuid4()
    parent = Session(
        id=parent_id,
        agent_id="harness",
        provider="kiro",
        model="claude-opus-4.7",
    )
    created: dict = {}

    async def get_agent(_name):
        return BUILTIN_AGENTS["researcher"]

    async def get_session(session_id):
        return parent if session_id == parent_id else None

    async def create(**kwargs):
        created.update(kwargs)
        return Session(
            id=uuid.uuid4(),
            agent_id=kwargs["agent_id"],
            provider=kwargs["provider"],
            model=kwargs["model"],
            parent_session_id=kwargs["parent_session_id"],
            delegation_authority=kwargs["delegation_authority"],
        )

    async def mode(_session_id):
        return "ask"

    async def no_op(*_args, **_kwargs):
        return None

    class FakeAgent:
        async def run(self, _session_id, _task, **_kwargs):
            return SimpleNamespace(
                content="Done",
                tokens_used=1,
                iterations=1,
                needs_approval=[],
                outcome="completed",
            )

    from machina.core import orchestrator as module
    from machina.core import session_mode

    monkeypatch.setitem(config._session_overrides, "provider", "openrouter")
    monkeypatch.setitem(config._session_overrides, "model", "openrouter/free")
    monkeypatch.setattr(agent_registry, "get", get_agent)
    monkeypatch.setattr(session_manager, "get_fresh", get_session)
    monkeypatch.setattr(session_manager, "create", create)
    monkeypatch.setattr(session_mode, "resolve_effective_mode", mode)
    monkeypatch.setattr(module, "join_agent_background_tasks", no_op)
    monkeypatch.setattr(module.context_manager, "add_chunk", no_op)

    orchestrator = Orchestrator(agent_factory=lambda _definition: FakeAgent())
    monkeypatch.setattr(orchestrator, "_parent_context", lambda *_args: no_op())
    monkeypatch.setattr(orchestrator, "_record_start", lambda *_args, **_kwargs: no_op())
    monkeypatch.setattr(orchestrator, "_record_status", no_op)
    monkeypatch.setattr(orchestrator, "_record_end", no_op)

    async def charged(*_args):
        return 1

    monkeypatch.setattr(orchestrator, "_settle_child_tokens", charged)
    result = await orchestrator.delegate(
        "researcher",
        "Check one source",
        parent_session_id=parent_id,
    )

    assert result.response == "Done"
    assert created["provider"] == "kiro"
    assert created["model"] == "claude-opus-4.7"
