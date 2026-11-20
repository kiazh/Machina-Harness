"""Deterministic temporal/entity extraction and graph expansion contracts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from machina.memory.graph import (
    Entity,
    MemoryGraph,
    extract_entities,
    extract_relations,
    has_temporal_cue,
)
from machina.memory.models import MemoryEntry


def test_extract_prefers_explicit_code_and_quoted_names() -> None:
    entities = extract_entities(
        'The `vector_store` service uses "PostgreSQL" for Alice. It stores FastAPI routes.'
    )
    assert [(entity.display, entity.kind) for entity in entities] == [
        ("vector_store", "code"),
        ("PostgreSQL", "quoted"),
        ("Alice", "term"),
        ("FastAPI", "term"),
    ]


def test_entity_extraction_is_bounded_and_deduplicated() -> None:
    content = " ".join(f"`thing{i}`" for i in range(40))
    entities = extract_entities(content)
    assert len(entities) == 12
    assert len({entity.normalized for entity in entities}) == len(entities)


def test_relations_follow_sentence_order_and_require_a_verb() -> None:
    entities = [
        Entity("Alice", "alice"),
        Entity("PostgreSQL", "postgresql"),
        Entity("Redis", "redis"),
    ]
    relations = extract_relations("Alice uses PostgreSQL. Redis caches results.", entities)
    assert relations == [(Entity("Alice", "alice"), Entity("PostgreSQL", "postgresql"), "uses")]


def test_temporal_cues_are_explicit_and_bounded() -> None:
    assert has_temporal_cue("What did Alice use before joining?") is True
    assert has_temporal_cue("Show the project timeline") is True
    assert has_temporal_cue("Which database do we use?") is False


class _FakeDb:
    def __init__(self) -> None:
        self.sql = ""
        self.params = ()

    async def fetch(self, sql, *params):
        self.sql = sql
        self.params = params
        return [
            {
                "id": uuid.uuid4(),
                "session_id": None,
                "agent_id": "harness",
                "content": "Alice chose PostgreSQL in 2024.",
                "category": "decision",
                "importance": 0.8,
                "created_at": datetime(2024, 1, 1, tzinfo=UTC),
                "last_accessed": None,
                "access_count": 0,
                "explicitly_important": True,
                "base_strength": 1.0,
                "quarantined": False,
                "shared": 2,
            }
        ]


@pytest.mark.asyncio
async def test_expand_filters_and_boosts_shared_entities() -> None:
    database = _FakeDb()
    graph = MemoryGraph(database=database)
    start = datetime(2023, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 1, tzinfo=UTC)
    results = await graph.expand(
        [uuid.uuid4()],
        query="What did Alice use before the migration?",
        agent_id="harness",
        category="decision",
        date_range=(start, end),
        limit=5,
    )
    assert len(results) == 1
    assert results[0][1] == pytest.approx(0.14)
    assert "m.agent_id = $3" in database.sql
    assert "m.category = $4" in database.sql
    assert "ABS(EXTRACT(EPOCH" in database.sql
    assert database.params[-1] == 5
    assert database.params[2:6] == ("harness", "decision", start, end)


@pytest.mark.asyncio
async def test_expand_without_seeds_or_entities_skips_the_database() -> None:
    database = _FakeDb()
    graph = MemoryGraph(database=database)
    assert await graph.expand([], query="") == []
    assert database.sql == ""


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self.entities = 0

    def transaction(self):
        return _FakeTransaction()

    async def fetchval(self, sql, *params):
        self.entities += 1
        return uuid.uuid4()

    async def execute(self, sql, *params):
        self.statements.append((sql, params))


class _Acquire:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *exc):
        return False


class _IngestDb:
    def __init__(self) -> None:
        self.connection = _FakeConnection()

    def acquire(self):
        return _Acquire(self.connection)


@pytest.mark.asyncio
async def test_ingest_writes_mentions_and_an_explicit_edge() -> None:
    database = _IngestDb()
    graph = MemoryGraph(database=database)
    memory = MemoryEntry(
        id=uuid.uuid4(),
        session_id=None,
        agent_id="harness",
        content="Alice uses PostgreSQL for the project.",
        category="fact",
    )
    await graph.ingest(memory)
    assert database.connection.entities == 2
    statements = [sql for sql, _ in database.connection.statements]
    assert sum("memory_entity_mentions" in sql for sql in statements) == 2
    assert sum("memory_edges" in sql for sql in statements) == 1


@pytest.mark.asyncio
async def test_quarantined_memory_is_not_indexed() -> None:
    database = _IngestDb()
    graph = MemoryGraph(database=database)
    memory = MemoryEntry(
        id=uuid.uuid4(),
        session_id=None,
        agent_id="harness",
        content="Alice uses PostgreSQL.",
        category="fact",
        quarantined=True,
    )
    await graph.ingest(memory)
    assert database.connection.entities == 0
