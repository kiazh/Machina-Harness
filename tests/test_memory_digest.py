"""Deterministic query-conditioned memory digest tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from machina.memory.digest import build_memory_digest, select_diverse
from machina.memory.models import MemoryEntry, RetrievedMemory
from machina.memory.retriever import MemoryRetriever


def _memory(
    content: str,
    *,
    score: float,
    category: str = "fact",
    importance: float = 0.5,
) -> RetrievedMemory:
    return RetrievedMemory(
        memory=MemoryEntry(
            id=uuid.uuid5(uuid.NAMESPACE_URL, content),
            session_id=None,
            agent_id="agent",
            content=content,
            category=category,
            importance=importance,
            created_at=datetime.now(UTC),
        ),
        score=score,
    )


def test_digest_collapses_duplicates_and_keeps_complementary_facts() -> None:
    memories = [
        _memory("User likes a high-protein breakfast.", score=0.95, category="preference"),
        _memory("User likes a high-protein breakfast.", score=0.94, category="preference"),
        _memory("User has a family history of type 2 diabetes.", score=0.82),
        _memory("User prefers Greek yogurt with chia seeds.", score=0.78, category="preference"),
        _memory("The weather is warm today.", score=0.30),
    ]

    digest = build_memory_digest(
        memories,
        "What breakfast fits my health history and preferences?",
        max_items=3,
        max_chars=700,
    )

    text = digest.text.lower()
    assert "diabetes" in text
    assert "greek yogurt" in text
    assert text.count("high-protein breakfast") == 1
    assert len(digest.selected) == 3
    assert digest.omitted >= 1
    assert digest.char_count == len(digest.text)


def test_digest_respects_character_budget_and_is_deterministic() -> None:
    memories = [
        _memory(f"Fact number {index}: " + ("detail " * 80), score=1.0 - index * 0.01)
        for index in range(10)
    ]

    first = build_memory_digest(memories, "facts", max_items=8, max_chars=500)
    second = build_memory_digest(memories, "facts", max_items=8, max_chars=500)

    assert first.text == second.text
    assert first.char_count <= 500
    assert len(first.selected) >= 1


def test_empty_or_disabled_digest_returns_empty_view() -> None:
    digest = build_memory_digest([], "anything")
    assert digest.text == ""
    assert digest.selected == ()
    assert digest.omitted == 0

    memory = _memory("A fact", score=1.0)
    tiny = build_memory_digest([memory], "anything", max_chars=100)
    assert tiny.text
    assert len(tiny.text) <= 100


def test_select_diverse_prefers_a_new_fact_over_a_near_duplicate() -> None:
    memories = [
        _memory("The deployment uses PostgreSQL with pgvector.", score=1.0),
        _memory("The database deployment uses PostgreSQL with pgvector.", score=0.99),
        _memory("The API is written in Python and FastAPI.", score=0.70),
    ]

    selected = select_diverse(
        memories,
        "Which database and API stack do we use?",
        max_items=2,
        max_chars=500,
    )

    contents = " ".join(item.memory.content.lower() for item in selected)
    assert "pgvector" in contents
    assert "fastapi" in contents


@pytest.mark.asyncio
async def test_retriever_digest_overfetches_then_builds_digest(monkeypatch) -> None:
    retriever = MemoryRetriever()
    candidates = [
        _memory("The project uses PostgreSQL.", score=0.9),
        _memory("The project uses Redis for caching.", score=0.7),
    ]
    seen: dict[str, object] = {}

    async def fake_retrieve(**kwargs):
        seen.update(kwargs)
        return candidates

    monkeypatch.setattr(retriever, "retrieve", fake_retrieve)

    digest = await retriever.retrieve_digest(
        "What database and cache does the project use?",
        max_items=2,
        max_chars=500,
        candidate_limit=12,
    )

    assert seen["limit"] == 12  # explicit candidate_limit wins over top_k=5
    assert "PostgreSQL" in digest.text
    assert "Redis" in digest.text
