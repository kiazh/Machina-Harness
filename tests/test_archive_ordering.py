"""Archive writes preserve the transcript position, including older writers."""

import asyncio
import json
import os
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import msgpack
import pytest
import pytest_asyncio

from machina.core.context import ContextManager
from machina.db.connection import Database

NOW = datetime(2026, 11, 2, tzinfo=UTC)


@pytest_asyncio.fixture
async def archive_db():
    """Only the acceptance harness's local disposable database may be written."""
    dsn = os.environ.get("MACHINA_TEST_DATABASE_URL", "")
    parts = urlsplit(dsn)
    if not dsn or os.environ.get("MACHINA_HISTORY_SQL_TEST") != "1":
        pytest.skip("requires the disposable local PostgreSQL acceptance harness")
    if parts.hostname not in {"localhost", "127.0.0.1", "::1"} or not re.fullmatch(
        r"/machina_desktop_test_[0-9a-f]{32}", parts.path
    ):
        pytest.fail("archive SQL test requires a disposable localhost UUID database")
    import asyncpg

    schema = "archive_order_" + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    database = Database(dsn)
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await admin.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        database._pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=2, server_settings={"search_path": schema + ",public"}
        )
        await database.initialize_schema()
        yield database
    finally:
        await database.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def add_session(database):
    sid = uuid.uuid4()
    await database.execute("INSERT INTO sessions (id) VALUES ($1)", sid)
    return sid


async def add_chunk(database, sid, number, seq, created=NOW, content=None):
    await database.execute(
        """INSERT INTO context_chunks
           (id, session_id, agent_id, chunk_type, payload_msgpack, token_count,
            created_at, event_seq)
           VALUES ($1, $2, 'harness', 'assistant_message', $3, 1, $4, $5)""",
        uuid.UUID(int=number),
        sid,
        msgpack.packb({"content": str(number) if content is None else content}),
        created,
        seq,
    )


async def archive_old_style(database, sid):
    """Model already-running CLI/gateway code that does not know the new fields."""
    async with database.acquire() as connection, connection.transaction():
        await connection.execute(
            """INSERT INTO context_archive
               (session_id, chunk_id, agent_id, chunk_type, payload_msgpack,
                token_count, original_created_at)
               SELECT session_id, id, agent_id, chunk_type, payload_msgpack,
                      token_count, created_at
               FROM context_chunks WHERE session_id = $1""",
            sid,
        )
        await connection.execute("DELETE FROM context_chunks WHERE session_id = $1", sid)


@pytest.mark.asyncio
async def test_fresh_schema_preserves_order_for_an_old_archive_writer(archive_db):
    sid = await add_session(archive_db)
    await add_chunk(archive_db, sid, 1, 42, created=None)
    await archive_old_style(archive_db, sid)
    saved = await archive_db.fetchval(
        "SELECT to_jsonb(a) FROM context_archive a WHERE session_id = $1", sid
    )
    saved = json.loads(saved)
    assert saved.get("original_event_seq") == 42
    assert saved.get("original_order_known") is True
    assert saved["original_created_at"] is None


@pytest.mark.asyncio
async def test_transcript_position_survives_archival_with_ties_and_nulls(archive_db, monkeypatch):
    import machina.core.context as context

    monkeypatch.setattr(context, "db", archive_db)
    sid = await add_session(archive_db)
    for number, seq, created in [(1, 2, NOW), (2, 1, NOW), (3, None, None), (4, None, NOW)]:
        await add_chunk(archive_db, sid, number, seq, created)
    manager = ContextManager()
    before = await manager.get_transcript_chunks(sid, include_embedding=False)
    assert [chunk.id.int for chunk in before] == [1, 2, 4, 3]
    await archive_old_style(archive_db, sid)
    after = await manager.get_transcript_chunks(sid, include_embedding=False)
    assert [chunk.id.int for chunk in after] == [1, 2, 4, 3]
    assert [chunk.event_seq for chunk in after] == [2, 1, None, None]
    assert after[-1].created_at is None


@pytest.mark.asyncio
async def test_runtime_upgrade_backfills_only_recoverable_order_once(archive_db):
    # Emulate a native database from before archive order metadata existed.
    await archive_db.execute(
        "DROP TRIGGER IF EXISTS context_archive_preserve_order ON context_archive"
    )
    await archive_db.execute("ALTER TABLE context_archive DROP COLUMN IF EXISTS original_event_seq")
    await archive_db.execute(
        "ALTER TABLE context_archive DROP COLUMN IF EXISTS original_order_known"
    )
    sid = await add_session(archive_db)
    await add_chunk(archive_db, sid, 1, 42, created=None)
    await add_chunk(archive_db, sid, 3, 100)
    original_time = datetime(2020, 1, 1, tzinfo=UTC)
    await archive_db.execute(
        """INSERT INTO context_archive
           (session_id, chunk_id, payload_msgpack, original_created_at)
           VALUES ($1, $2, $3, NULL), ($1, $4, $3, NULL), ($1, $5, $3, $6)""",
        sid,
        uuid.UUID(int=1),
        msgpack.packb({"content": "original"}),
        uuid.UUID(int=2),
        uuid.UUID(int=3),
        original_time,
    )
    await archive_db.initialize_runtime_schema()
    rows = await archive_db.fetch(
        "SELECT chunk_id, to_jsonb(a) AS data FROM context_archive a "
        "WHERE session_id = $1 ORDER BY chunk_id",
        sid,
    )
    recoverable, legacy, historical = [json.loads(row["data"]) for row in rows]
    assert recoverable.get("original_event_seq") == 42
    assert recoverable.get("original_order_known") is True
    assert recoverable["original_created_at"] is None
    assert legacy.get("original_event_seq") is None
    assert legacy.get("original_order_known") is False
    assert historical.get("original_event_seq") is None
    assert historical.get("original_order_known") is False
    assert datetime.fromisoformat(historical["original_created_at"]) == original_time

    # NULL on an old archive means unknown. A later matching live ID must not
    # relabel historical data on every startup or require an archive write lock.
    await add_chunk(archive_db, sid, 2, 99)
    async with archive_db.acquire() as reader, reader.transaction():
        await reader.execute("LOCK TABLE context_archive IN SHARE MODE")
        await asyncio.wait_for(archive_db.initialize_runtime_schema(), timeout=3)
    assert (
        await archive_db.fetchval(
            "SELECT original_event_seq FROM context_archive WHERE session_id = $1 AND chunk_id = $2",
            sid,
            uuid.UUID(int=2),
        )
        is None
    )
    await archive_old_style(archive_db, sid)
    assert (
        await archive_db.fetchval(
            "SELECT max(original_event_seq) FROM context_archive WHERE session_id = $1 AND chunk_id = $2",
            sid,
            uuid.UUID(int=2),
        )
        == 99
    )


@pytest.mark.asyncio
async def test_unrecoverable_legacy_archives_keep_the_timestamp_fallback(archive_db, monkeypatch):
    import machina.core.context as context

    monkeypatch.setattr(context, "db", archive_db)
    sid = await add_session(archive_db)
    original_time = datetime(2020, 1, 1, tzinfo=UTC)
    await archive_db.execute(
        """INSERT INTO context_archive
           (session_id, chunk_id, chunk_type, payload_msgpack, original_created_at, archived_at)
           VALUES ($1, $2, 'assistant_message', $3, NULL, $4),
                  ($1, $5, 'assistant_message', $3, $6, $4)""",
        sid,
        uuid.UUID(int=1),
        msgpack.packb({"content": "legacy"}),
        NOW,
        uuid.UUID(int=2),
        original_time,
    )
    chunks = await ContextManager().get_transcript_chunks(sid, include_embedding=False)
    assert [chunk.id.int for chunk in chunks] == [1, 2]
    assert [chunk.created_at for chunk in chunks] == [NOW, original_time]
    assert [chunk.event_seq for chunk in chunks] == [None, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["text", "dense", "resurrect"])
async def test_archive_recall_retains_known_and_legacy_order_metadata(
    archive_db, monkeypatch, reader
):
    import machina.core.context as context

    monkeypatch.setattr(context, "db", archive_db)
    sid = await add_session(archive_db)
    content = "archived zebra"
    embedding = [1.0] + [0.0] * 1535
    manager = ContextManager()
    await add_chunk(archive_db, sid, 1, 42, created=None, content=content)
    await manager.archive_chunk(
        sid,
        uuid.UUID(int=1),
        msgpack.packb({"content": content}),
        embedding=embedding,
        chunk_type="assistant_message",
    )
    await archive_db.execute("DELETE FROM context_chunks WHERE session_id = $1", sid)
    await archive_db.execute(
        """INSERT INTO context_archive
           (session_id, chunk_id, chunk_type, payload_msgpack, search_text, embedding, archived_at)
           VALUES ($1, $2, 'assistant_message', $3, 'legacy zebra', $4, $5)""",
        sid,
        uuid.UUID(int=2),
        msgpack.packb({"content": "legacy zebra"}),
        "[" + ",".join(str(value) for value in embedding) + "]",
        NOW,
    )
    if reader == "text":
        recalled = await manager.search_archive_text(sid, "zebra")
    elif reader == "dense":
        recalled = await manager.search_archive_dense(sid, embedding)
    else:
        recalled = await manager.resurrect_context(sid, embedding)
    chunks = {chunk.id.int: chunk for chunk, _ in recalled}
    assert set(chunks) == {1, 2}
    assert chunks[1].event_seq == 42
    assert chunks[1].created_at is None
    assert chunks[2].event_seq is None
    assert chunks[2].created_at == NOW
