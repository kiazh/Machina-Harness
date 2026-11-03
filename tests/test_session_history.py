"""Paged history remains bounded and never restores or modifies context."""

import importlib
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import msgpack
import pytest

from machina.gateway.errors import INVALID_PARAMS, SESSION_NOT_FOUND, RpcError

SID = uuid.UUID("10000000-0000-0000-0000-000000000001")
OTHER_SID = uuid.UUID("10000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 11, 2, tzinfo=UTC)


def module():
    return importlib.import_module("machina.gateway.features.history")


def row(number, content, *, kind="assistant_message", seq=None, created=NOW, legacy=False):
    payload = {"content": content}
    if kind == "tool_call":
        payload = {"tool": "shell", "result_preview": content}
    return {
        "id": uuid.UUID(int=number),
        "chunk_type": kind,
        "created_at": created,
        "event_seq": seq,
        "payload_msgpack": msgpack.packb(payload, use_bin_type=True),
        "legacy_search": legacy,
        "search_preview": None if legacy else content[:20000],
        "oversized": False,
    }


class Gateway:
    """The real get_session boundary resolves scope; reads must leave it untouched."""

    def __init__(self):
        self.session = SimpleNamespace(id=SID, state={"keep": 1}, last_activity=NOW)
        self.lookups = []

    def require_db(self):
        pass

    async def get_session(self, params):
        self.lookups.append(dict(params))
        if uuid.UUID(params["sessionId"]) != SID:
            raise RpcError(SESSION_NOT_FOUND, "missing")
        return self.session


class RowsDb:
    """External database boundary with ordered, already canonical candidate rows."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, sql, sid, query, before_time, before_seq, before_id, limit, payload_cap):
        assert sid == SID
        self.calls.append((sql, limit, payload_cap))
        start = 0
        if before_id is not None:
            start = next(i + 1 for i, item in enumerate(self.rows) if item["id"] == before_id)
        return self.rows[start : start + limit]


@pytest.mark.asyncio
async def test_registry_exposes_the_readonly_history_method():
    from machina.gateway.features import METHODS

    assert "session.history" in METHODS
    gateway = Gateway()
    db = RowsDb([])
    history = module()
    original = history.db
    try:
        history.db = db
        assert await METHODS["session.history"](gateway, {"sessionId": str(SID)}) == {
            "entries": [],
            "nextCursor": None,
        }
    finally:
        history.db = original


@pytest.mark.asyncio
async def test_pages_are_newest_first_and_cursor_is_bound_to_session_and_literal_query(monkeypatch):
    history = module()
    db = RowsDb([row(3, "100% MATCH_ newer"), row(2, "100% match_ older"), row(1, "oldest")])
    monkeypatch.setattr(history, "db", db)
    gateway = Gateway()
    params = {"sessionId": str(SID), "query": "100% match_", "limit": 1}
    first = await history.session_history(gateway, params)
    assert [entry["content"] for entry in first["entries"]] == ["100% MATCH_ newer"]
    assert isinstance(first["nextCursor"], str)
    second = await history.session_history(gateway, {**params, "cursor": first["nextCursor"]})
    assert [entry["id"] for entry in second["entries"]] == [str(uuid.UUID(int=2))]
    for changed in ({"sessionId": str(OTHER_SID)}, {"query": "other"}):
        with pytest.raises(RpcError) as error:
            await history.session_history(
                gateway, {**params, **changed, "cursor": first["nextCursor"]}
            )
        assert error.value.code == INVALID_PARAMS
    assert gateway.session.state == {"keep": 1}
    assert gateway.session.last_activity == NOW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"sessionId": "bad"},
        {"sessionId": None},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"query": "x" * 257},
        {"query": 42},
        {"query": None},
        {"query": "\ud800"},
        {"query": "\x00"},
        {"cursor": "garbage"},
        {"cursor": ""},
        {"cursor": 1},
        {"cursor": None},
        {"cursor": "x" * 4096},
    ],
)
async def test_invalid_inputs_are_rejected_before_history_reads(monkeypatch, invalid):
    history = module()
    db = RowsDb([])
    monkeypatch.setattr(history, "db", db)
    params = {"sessionId": str(SID), **invalid} if invalid else {}
    with pytest.raises(RpcError) as error:
        await history.session_history(Gateway(), params)
    assert error.value.code == INVALID_PARAMS
    assert db.calls == []


@pytest.mark.asyncio
async def test_missing_session_is_not_read_from_the_history_store(monkeypatch):
    history = module()
    db = RowsDb([])
    monkeypatch.setattr(history, "db", db)
    with pytest.raises(RpcError) as error:
        await history.session_history(Gateway(), {"sessionId": str(OTHER_SID)})
    assert error.value.code == SESSION_NOT_FOUND
    assert db.calls == []


@pytest.mark.asyncio
async def test_legacy_search_checks_decoded_content_and_can_advance_an_empty_scan_page(monkeypatch):
    history = module()
    monkeypatch.setattr(history, "MAX_SCAN_ROWS", 2)
    db = RowsDb(
        [
            row(4, "no", legacy=True),
            row(3, "no", legacy=True),
            row(2, "Legacy NEEDLE", legacy=True),
            row(1, "no", legacy=True),
        ]
    )
    monkeypatch.setattr(history, "db", db)
    params = {"sessionId": str(SID), "query": "needle"}
    first = await history.session_history(Gateway(), params)
    assert first["entries"] == []
    assert first["nextCursor"]
    second = await history.session_history(Gateway(), {**params, "cursor": first["nextCursor"]})
    assert [entry["content"] for entry in second["entries"]] == ["Legacy NEEDLE"]
    assert second["nextCursor"] is None


@pytest.mark.asyncio
async def test_redaction_and_utf8_limits_do_not_skip_the_next_message(monkeypatch):
    history = module()
    secret = "sk-" + "A1b2C3d4" * 5
    db = RowsDb([row(i, secret + "\n" + '😀"\\\n' * 20000) for i in range(100, 0, -1)])
    monkeypatch.setattr(history, "db", db)
    gateway = Gateway()
    result = await history.session_history(gateway, {"sessionId": str(SID)})
    encoded = json.dumps(result, ensure_ascii=True).encode("utf-8")
    assert len(encoded) <= 512 * 1024
    assert 0 < len(result["entries"]) < 100
    assert secret not in encoded.decode()
    assert "REDACTED" in result["entries"][0]["content"]
    assert all(entry["truncated"] for entry in result["entries"])
    assert all(len(entry["content"].encode()) <= 32 * 1024 for entry in result["entries"])
    following = await history.session_history(
        gateway, {"sessionId": str(SID), "cursor": result["nextCursor"], "limit": 1}
    )
    assert uuid.UUID(following["entries"][0]["id"]).int == 100 - len(result["entries"])


@pytest.mark.asyncio
async def test_corrupt_and_oversized_payloads_have_bounded_placeholders(monkeypatch):
    history = module()
    corrupt = {**row(2, ""), "payload_msgpack": b"\xc1"}
    oversized = {**row(1, ""), "payload_msgpack": None, "oversized": True}
    monkeypatch.setattr(history, "db", RowsDb([corrupt, oversized]))
    result = await history.session_history(Gateway(), {"sessionId": str(SID)})
    assert len(result["entries"]) == 2
    assert all(entry["truncated"] and entry["content"] for entry in result["entries"])


@pytest.mark.asyncio
async def test_oversized_image_caption_is_shown_as_a_redacted_indexed_preview(monkeypatch):
    history = module()
    oversized = {
        **row(1, "A picture of a chair. password=caption-secret", kind="user_message"),
        "payload_msgpack": None,
        "oversized": True,
    }
    monkeypatch.setattr(history, "db", RowsDb([oversized]))
    result = await history.session_history(Gateway(), {"sessionId": str(SID)})
    entry = result["entries"][0]
    assert "A picture of a chair." in entry["content"]
    assert "Indexed text preview" in entry["content"]
    assert "caption-secret" not in entry["content"]
    assert "REDACTED" in entry["content"]
    assert entry["role"] == "user"
    assert entry["truncated"] is True
    found = await history.session_history(Gateway(), {"sessionId": str(SID), "query": "CHAIR"})
    assert [item["id"] for item in found["entries"]] == [str(uuid.UUID(int=1))]
    assert "unsearchedCount" not in found


@pytest.mark.asyncio
@pytest.mark.parametrize("unreadable", ["oversized", "malformed", "saturated"])
async def test_query_reports_unsearchable_candidates_instead_of_a_complete_no_match(
    monkeypatch, unreadable
):
    history = module()
    item = {**row(1, "", legacy=True), "payload_msgpack": None, "oversized": True}
    if unreadable == "malformed":
        item.update(payload_msgpack=b"\xc1", oversized=False)
    elif unreadable == "saturated":
        item["search_preview"] = "x" * 20000
    monkeypatch.setattr(history, "db", RowsDb([item]))
    result = await history.session_history(Gateway(), {"sessionId": str(SID), "query": "needle"})
    assert result == {"entries": [], "nextCursor": None, "unsearchedCount": 1}


@pytest.mark.asyncio
async def test_search_response_limit_includes_unsearched_notice_without_losing_its_cursor(
    monkeypatch,
):
    history = module()
    unknown = {**row(200, "", legacy=True), "payload_msgpack": b"\xc1"}
    matches = [row(i, "needle " + '"\\\n' * 20000) for i in range(100, 0, -1)]
    monkeypatch.setattr(history, "db", RowsDb([unknown, *matches]))
    first = await history.session_history(Gateway(), {"sessionId": str(SID), "query": "needle"})
    assert first["unsearchedCount"] == 1
    assert len(json.dumps(first, ensure_ascii=True).encode("utf-8")) <= 512 * 1024
    second = await history.session_history(
        Gateway(),
        {"sessionId": str(SID), "query": "needle", "cursor": first["nextCursor"], "limit": 1},
    )
    assert uuid.UUID(second["entries"][0]["id"]).int == 100 - len(first["entries"])
    assert "unsearchedCount" not in second


@pytest.mark.asyncio
@pytest.mark.parametrize("padding", [32614, 32500])
async def test_response_cap_reserves_the_final_longer_scan_cursor_and_uncertainty(
    monkeypatch, padding
):
    history = module()
    matches = [row(i, "needle " + "x" * padding, seq=0) for i in range(501, 485, -1)]
    unreadable = [
        {
            **row(i, "", legacy=True, seq=-(2**63), created=NOW - timedelta(microseconds=1)),
            "payload_msgpack": b"\xc1",
        }
        for i in range(485, 0, -1)
    ]
    monkeypatch.setattr(history, "db", RowsDb([*matches, *unreadable]))
    result = await history.session_history(Gateway(), {"sessionId": str(SID), "query": "needle"})
    assert len(json.dumps(result, ensure_ascii=True).encode("utf-8")) <= 512 * 1024
    if padding == 32500:
        assert len(result["entries"]) == 16
        assert result["unsearchedCount"] == 484
        assert result["nextCursor"]


@pytest.mark.asyncio
async def test_a_full_legacy_scan_reports_five_hundred_unsearchable_rows_with_a_cursor(monkeypatch):
    history = module()
    unreadable = [
        {
            **row(i, "", legacy=True, seq=-(2**63), created=NOW - timedelta(microseconds=1)),
            "payload_msgpack": b"\xc1",
        }
        for i in range(501, 0, -1)
    ]
    monkeypatch.setattr(history, "db", RowsDb(unreadable))
    result = await history.session_history(Gateway(), {"sessionId": str(SID), "query": "needle"})
    assert result["entries"] == []
    assert result["unsearchedCount"] == 500
    assert result["nextCursor"]
    assert len(json.dumps(result, ensure_ascii=True).encode("utf-8")) <= 512 * 1024
    last = await history.session_history(
        Gateway(), {"sessionId": str(SID), "query": "needle", "cursor": result["nextCursor"]}
    )
    assert last == {"entries": [], "nextCursor": None, "unsearchedCount": 1}


@pytest.mark.asyncio
async def test_response_budget_encodes_each_entry_once_instead_of_every_prior_prefix(monkeypatch):
    history = module()
    monkeypatch.setattr(history, "db", RowsDb([row(i, "z" * 4096) for i in range(100, 0, -1)]))
    dumps = json.dumps
    encoded_bytes = 0

    def measured_dumps(*args, **kwargs):
        nonlocal encoded_bytes
        result = dumps(*args, **kwargs)
        encoded_bytes += len(result.encode("utf-8"))
        return result

    with monkeypatch.context() as sizing:
        sizing.setattr(history.json, "dumps", measured_dumps)
        result = await history.session_history(Gateway(), {"sessionId": str(SID)})
    assert len(result["entries"]) == 100
    assert encoded_bytes < 2 * len(json.dumps(result, ensure_ascii=True).encode("utf-8"))


@pytest.mark.asyncio
async def test_an_index_cut_inside_a_secret_does_not_expose_its_prefix(monkeypatch):
    history = module()
    prefix = "A caption. " + "-" * 19970
    preview = (prefix + " sk-" + "A1b2C3d4" * 10)[:20000]
    item = {
        **row(1, "", legacy=True),
        "payload_msgpack": None,
        "oversized": True,
        "search_preview": preview,
    }
    monkeypatch.setattr(history, "db", RowsDb([item]))
    result = await history.session_history(Gateway(), {"sessionId": str(SID)})
    assert "A caption." in result["entries"][0]["content"]
    assert "sk-A1b2" not in result["entries"][0]["content"]


@pytest.mark.asyncio
async def test_default_page_reaches_one_hundred_across_batches_without_duplicates(monkeypatch):
    history = module()
    monkeypatch.setattr(history, "db", RowsDb([row(i, f"message {i}") for i in range(101, 0, -1)]))
    first = await history.session_history(Gateway(), {"sessionId": str(SID)})
    assert len(first["entries"]) == 100
    assert first["entries"][0]["content"] == "message 101"
    assert first["entries"][-1]["content"] == "message 2"
    second = await history.session_history(
        Gateway(), {"sessionId": str(SID), "cursor": first["nextCursor"]}
    )
    assert [entry["content"] for entry in second["entries"]] == ["message 1"]
    assert second["nextCursor"] is None


@pytest.mark.asyncio
async def test_cursor_cannot_be_modified_and_query_spaces_remain_literal(monkeypatch):
    history = module()
    monkeypatch.setattr(history, "db", RowsDb([row(2, " spaced "), row(1, "spaced")]))
    first = await history.session_history(
        Gateway(), {"sessionId": str(SID), "query": " spaced ", "limit": 1}
    )
    assert first["entries"][0]["content"] == " spaced "
    cursor = first["nextCursor"]
    tampered = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    with pytest.raises(RpcError) as error:
        await history.session_history(Gateway(), {"sessionId": str(SID), "cursor": tampered})
    assert error.value.code == INVALID_PARAMS
    remaining = await history.session_history(
        Gateway(), {"sessionId": str(SID), "query": " spaced ", "cursor": cursor}
    )
    assert remaining == {"entries": [], "nextCursor": None}


@pytest.mark.asyncio
async def test_cursor_survives_old_writer_archival_of_returned_and_null_timestamp_rows(monkeypatch):
    dsn = os.environ.get("MACHINA_TEST_DATABASE_URL", "")
    parts = urlsplit(dsn)
    if not dsn or os.environ.get("MACHINA_HISTORY_SQL_TEST") != "1":
        pytest.skip("requires the disposable local PostgreSQL acceptance harness")
    if parts.hostname not in {"localhost", "127.0.0.1", "::1"} or not re.fullmatch(
        r"/machina_desktop_test_[0-9a-f]{32}", parts.path
    ):
        pytest.fail("history SQL test requires a disposable localhost UUID database")
    import asyncpg

    from machina.db.connection import _CONTEXT_ARCHIVE_EVENT_ORDER_MIGRATION

    history = module()
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("""
            CREATE TEMP TABLE context_chunks (
                id uuid PRIMARY KEY, session_id uuid, chunk_type text, payload_msgpack bytea,
                created_at timestamptz, event_seq bigint, search_text text);
            CREATE TEMP TABLE context_archive (
                id uuid PRIMARY KEY, chunk_id uuid, session_id uuid, chunk_type text,
                payload_msgpack bytea, original_created_at timestamptz,
                archived_at timestamptz, search_text text);
        """)
        await connection.execute(_CONTEXT_ARCHIVE_EVENT_ORDER_MIGRATION)
        for number, seq, timestamp in [(1, 3, NOW), (2, 2, NOW), (3, 1, None)]:
            await connection.execute(
                "INSERT INTO context_chunks VALUES ($1,$2,$3,$4,$5,$6,$7)",
                uuid.UUID(int=number),
                SID,
                "assistant_message",
                msgpack.packb({"content": f"event {number}"}),
                timestamp,
                seq,
                f"event {number}",
            )
        monkeypatch.setattr(history, "db", connection)
        first = await history.session_history(Gateway(), {"sessionId": str(SID), "limit": 1})
        assert [entry["id"] for entry in first["entries"]] == [str(uuid.UUID(int=1))]
        async with connection.transaction():
            # Intentionally omit new ordering columns exactly like an older
            # compactor. The database trigger must capture them before DELETE.
            await connection.execute(
                """
                INSERT INTO context_archive
                    (id, chunk_id, session_id, chunk_type, payload_msgpack,
                     original_created_at, archived_at, search_text)
                SELECT CASE WHEN id = $2 THEN $3::uuid ELSE $4::uuid END,
                       id, session_id, chunk_type, payload_msgpack, created_at,
                       $5, search_text
                FROM context_chunks WHERE session_id = $1 AND id = ANY($6::uuid[])
                """,
                SID,
                uuid.UUID(int=1),
                uuid.UUID(int=101),
                uuid.UUID(int=103),
                NOW + timedelta(hours=1),
                [uuid.UUID(int=1), uuid.UUID(int=3)],
            )
            await connection.execute(
                "DELETE FROM context_chunks WHERE id = ANY($1::uuid[])",
                [uuid.UUID(int=1), uuid.UUID(int=3)],
            )
        remaining = await history.session_history(
            Gateway(), {"sessionId": str(SID), "cursor": first["nextCursor"]}
        )
        assert [entry["id"] for entry in remaining["entries"]] == [
            str(uuid.UUID(int=2)),
            str(uuid.UUID(int=3)),
        ]
        assert remaining["entries"][-1]["createdAt"] is None
        assert remaining["nextCursor"] is None
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_actual_sql_prefers_earliest_archive_and_pages_tied_and_null_keys(monkeypatch):
    """Only the harness-created UUID database is accepted; no user database writes."""
    dsn = os.environ.get("MACHINA_TEST_DATABASE_URL", "")
    parts = urlsplit(dsn)
    if not dsn or os.environ.get("MACHINA_HISTORY_SQL_TEST") != "1":
        pytest.skip("requires the disposable local PostgreSQL acceptance harness")
    if parts.hostname not in {"localhost", "127.0.0.1", "::1"} or not re.fullmatch(
        r"/machina_desktop_test_[0-9a-f]{32}", parts.path
    ):
        pytest.fail("history SQL test requires a disposable localhost UUID database")
    import asyncpg

    history = module()
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("""
            CREATE TEMP TABLE context_chunks (
                id uuid PRIMARY KEY, session_id uuid, chunk_type text, payload_msgpack bytea,
                created_at timestamptz, event_seq bigint, search_text text);
            CREATE TEMP TABLE context_archive (
                id uuid PRIMARY KEY, chunk_id uuid, session_id uuid, chunk_type text,
                payload_msgpack bytea, original_created_at timestamptz,
                archived_at timestamptz, search_text text,
                original_event_seq bigint, original_order_known boolean NOT NULL DEFAULT false);
        """)
        for number, text, seq, created, kind in [
            (1, "compacted copy", 99, NOW, "assistant_message"),
            (2, "second event", 2, NOW, "assistant_message"),
            (3, "first event", 1, NOW, "user_message"),
            (4, "null ordinal", None, NOW, "tool_call"),
            (5, "null timestamp", None, None, "compression_summary"),
            (6, "document must stay hidden", 100, NOW, "document"),
        ]:
            item = row(number, text, kind=kind, seq=seq, created=created)
            await connection.execute(
                "INSERT INTO context_chunks VALUES ($1,$2,$3,$4,$5,$6,$7)",
                item["id"],
                SID,
                kind,
                item["payload_msgpack"],
                created,
                seq,
                text,
            )
        for number, text, saved in [
            (101, "Original NEEDLE 100%_", NOW),
            (102, "later archive copy", NOW + timedelta(seconds=1)),
        ]:
            await connection.execute(
                "INSERT INTO context_archive VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                uuid.UUID(int=number),
                uuid.UUID(int=1),
                SID,
                "assistant_message",
                msgpack.packb({"content": text}),
                NOW - timedelta(days=1),
                saved,
                None,
            )
        monkeypatch.setattr(history, "db", connection)
        seen = []
        cursor = None
        for _ in range(8):
            params = {"sessionId": str(SID), "limit": 1}
            if cursor:
                params["cursor"] = cursor
            page = await history.session_history(Gateway(), params)
            seen.extend(page["entries"])
            cursor = page["nextCursor"]
            if cursor is None:
                break
        assert [uuid.UUID(entry["id"]).int for entry in seen] == [2, 3, 4, 1, 5]
        assert [entry["role"] for entry in seen] == [
            "assistant",
            "user",
            "tool",
            "assistant",
            "system",
        ]
        assert seen[2]["tool"] == "shell"
        assert seen[3]["content"] == "Original NEEDLE 100%_"
        match = await history.session_history(
            Gateway(), {"sessionId": str(SID), "query": "needle 100%_"}
        )
        assert [entry["id"] for entry in match["entries"]] == [str(uuid.UUID(int=1))]
        hidden_copy = await history.session_history(
            Gateway(), {"sessionId": str(SID), "query": "later archive copy"}
        )
        assert hidden_copy == {"entries": [], "nextCursor": None}
        assert await connection.fetchval("SELECT count(*) FROM context_chunks") == 6
        assert await connection.fetchval("SELECT count(*) FROM context_archive") == 2

        # A saturated index contains just the first 20k characters, so it
        # cannot exclude a literal match later in a bounded decoded payload.
        text = "x" * 21000 + " saturation marker"
        await connection.execute(
            "INSERT INTO context_chunks VALUES ($1,$2,$3,$4,$5,$6,$7)",
            uuid.UUID(int=7),
            SID,
            "assistant_message",
            msgpack.packb({"content": text}),
            NOW,
            7,
            text[:20000],
        )
        saturation = await history.session_history(
            Gateway(), {"sessionId": str(SID), "query": "SATURATION MARKER"}
        )
        assert [entry["id"] for entry in saturation["entries"]] == [str(uuid.UUID(int=7))]

        huge = "x" * (300 * 1024)
        await connection.execute(
            "INSERT INTO context_chunks VALUES ($1,$2,$3,$4,$5,$6,$7)",
            uuid.UUID(int=8),
            SID,
            "assistant_message",
            msgpack.packb({"content": huge}),
            NOW,
            8,
            "huge message",
        )
        # Exercise the SQL bytea cap itself rather than faking a large flag.
        bounded = await connection.fetch(
            history._PAGE_SQL, SID, "", None, None, None, 1, 256 * 1024
        )
        assert bounded[0]["payload_msgpack"] is None
        assert bounded[0]["oversized"] is True
        page = await history.session_history(Gateway(), {"sessionId": str(SID), "limit": 1})
        assert page["entries"][0]["truncated"] is True

        caption = "Please identify this flower. password=image-caption-secret"
        await connection.execute(
            "INSERT INTO context_chunks VALUES ($1,$2,$3,$4,$5,$6,$7)",
            uuid.UUID(int=9),
            SID,
            "user_message",
            msgpack.packb({"content": caption, "images": [{"data": b"x" * (300 * 1024)}]}),
            NOW,
            9,
            caption,
        )
        image_page = await history.session_history(
            Gateway(), {"sessionId": str(SID), "query": "FLOWER"}
        )
        assert [entry["id"] for entry in image_page["entries"]] == [str(uuid.UUID(int=9))]
        assert "Please identify this flower." in image_page["entries"][0]["content"]
        assert "image-caption-secret" not in image_page["entries"][0]["content"]
        assert image_page["entries"][0]["truncated"] is True
        assert "unsearchedCount" not in image_page

        # A malformed legacy original cannot be claimed to be a ruled-out
        # match even after the SQL cursor reaches the end of this session.
        await connection.execute(
            "INSERT INTO context_chunks VALUES ($1,$2,$3,$4,$5,$6,$7)",
            uuid.UUID(int=10),
            SID,
            "assistant_message",
            b"\xc1",
            NOW,
            10,
            None,
        )
        uncertain = await history.session_history(
            Gateway(), {"sessionId": str(SID), "query": "never present"}
        )
        assert uncertain == {"entries": [], "nextCursor": None, "unsearchedCount": 1}
    finally:
        await connection.close()
