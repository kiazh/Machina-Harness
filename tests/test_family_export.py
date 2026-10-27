"""Exports include delegated work, even when requested from a child view."""

import uuid

import pytest

from machina import services
from machina.core.models import ContextChunk, Session


@pytest.mark.asyncio
async def test_export_includes_main_and_nested_subagents(monkeypatch):
    main = Session(id=uuid.uuid4(), title="Main")
    child = Session(
        id=uuid.uuid4(), title="Research", agent_id="researcher", parent_session_id=main.id
    )
    grandchild = Session(
        id=uuid.uuid4(), title="Follow-up", agent_id="coder", parent_session_id=child.id
    )
    family = [main, child, grandchild]

    async def get(session_id):
        return {member.id: member for member in family}.get(session_id)

    async def list_tree(root_id):
        assert root_id == main.id
        return family

    async def chunks(session_id, **_kwargs):
        return [
            ContextChunk(
                id=uuid.uuid4(),
                session_id=session_id,
                agent_id="harness",
                chunk_type="assistant_message",
                payload={"content": f"work from {session_id}"},
            )
        ]

    monkeypatch.setattr(services.session_manager, "get", get)
    monkeypatch.setattr(services.session_manager, "list_tree", list_tree)
    monkeypatch.setattr(services.context_manager, "get_transcript_chunks", chunks)
    report = await services.export_markdown(grandchild)
    assert report.count("# Session:") == 3
    assert report.index("# Session: Main") < report.index("# Session: Research")
    assert report.index("# Session: Research") < report.index("# Session: Follow-up")
    for member in family:
        assert f"work from {member.id}" in report
