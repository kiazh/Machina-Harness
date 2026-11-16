"""Configured RAG construction failures are reported; disabled RAG stays quiet."""

from __future__ import annotations

import pytest

from machina.core import agent_factory
from machina.tools import rag as rag_tools


@pytest.mark.asyncio
async def test_configured_rag_construction_failure_is_recorded(monkeypatch) -> None:
    recorded: list[tuple[str, BaseException]] = []

    async def fail():
        raise RuntimeError("pipeline construction failed")

    monkeypatch.setattr(agent_factory, "_rag_enabled", lambda: True)
    monkeypatch.setattr(rag_tools, "acquire_rag_pipeline", fail)
    monkeypatch.setattr(
        agent_factory,
        "record_failure",
        lambda boundary, error: recorded.append((boundary, error)),
    )

    assert await agent_factory._shared_rag_pipeline() is None
    assert len(recorded) == 1
    boundary, error = recorded[0]
    assert boundary == "agent_factory.rag_pipeline"
    assert isinstance(error, RuntimeError)


@pytest.mark.asyncio
async def test_disabled_rag_does_not_construct_or_report(monkeypatch) -> None:
    events: list[str] = []

    async def unexpected():
        events.append("acquire")
        raise AssertionError("disabled RAG must not construct a pipeline")

    monkeypatch.setattr(agent_factory, "_rag_enabled", lambda: False)
    monkeypatch.setattr(rag_tools, "acquire_rag_pipeline", unexpected)
    monkeypatch.setattr(
        agent_factory,
        "record_failure",
        lambda boundary, error: events.append(f"record:{boundary}"),
    )

    assert await agent_factory._shared_rag_pipeline() is None
    assert events == []
