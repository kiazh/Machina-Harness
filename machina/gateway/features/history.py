"""Read-only, bounded transcript pages, including retained archived originals.

Cursors are signed keyset positions in the transcript's existing
``(created_at DESC NULLS LAST, event_seq DESC NULLS LAST, id DESC)`` order.
They are reusable for the same session and literal case-insensitive query,
and expire when the gateway process restarts. A bounded legacy search scan
can return an empty page with a continuation cursor. A null cursor means
the scan reached the end; ``unsearchedCount`` reports consumed candidates
whose unavailable full text could still contain a search match.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import msgpack

from machina.db.lazy import LazyDb
from machina.gateway.errors import INVALID_PARAMS, RpcError
from machina.gateway.features._common import _int, _uuid
from machina.memory.redaction import StreamingSecretRedactor, redact_secrets

if TYPE_CHECKING:
    from machina.gateway.server import Gateway

db = LazyDb()

MAX_SCAN_ROWS = 500
MAX_BATCH_ROWS = 16
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_CONTENT_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_CURSOR_CHARS = 2048
_CURSOR_KEY = secrets.token_bytes(32)
_ROLES = {
    "user_message": "user",
    "assistant_message": "assistant",
    "tool_call": "tool",
    "compression_summary": "system",
}

# Dedupe only metadata before LIMIT. Payloads (and never embeddings) are
# fetched for at most MAX_BATCH_ROWS + 1 candidates, each capped in SQL.
# Archive priority/saved_at/source_id mirror ContextManager.get_transcript_chunks.
# search_text is historically NULL and is capped at 20,000 characters by
# _search_text_for: both legacy and saturated rows need bounded payload filtering.
_PAGE_SQL = """
    WITH transcript AS (
        SELECT id, chunk_type, created_at, event_seq,
               1 AS source_priority, created_at AS saved_at, id AS source_id
        FROM context_chunks WHERE session_id = $1
          AND chunk_type IN ('user_message', 'assistant_message', 'tool_call', 'compression_summary')
        UNION ALL
        SELECT chunk_id, chunk_type,
               CASE WHEN original_order_known THEN original_created_at
                    ELSE COALESCE(original_created_at, archived_at) END,
               original_event_seq, 0, archived_at, id
        FROM context_archive WHERE session_id = $1
          AND chunk_type IN ('user_message', 'assistant_message', 'tool_call', 'compression_summary')
    ), deduplicated AS (
        SELECT DISTINCT ON (id) * FROM transcript
        ORDER BY id, source_priority, saved_at ASC NULLS LAST, source_id ASC
    ), candidates AS MATERIALIZED (
        SELECT d.id, d.chunk_type, d.created_at, d.event_seq, d.source_priority, d.source_id,
               (s.search_text IS NULL OR length(s.search_text) >= 20000) AS legacy_search,
               left(s.search_text, 20000) AS search_preview
        FROM deduplicated d
        CROSS JOIN LATERAL (
            SELECT search_text FROM context_chunks
            WHERE d.source_priority = 1 AND id = d.source_id AND session_id = $1
            UNION ALL
            SELECT search_text FROM context_archive
            WHERE d.source_priority = 0 AND id = d.source_id AND session_id = $1
        ) s
        WHERE ($5::uuid IS NULL OR
            (created_at IS NOT NULL, COALESCE(created_at, '-infinity'::timestamptz),
             event_seq IS NOT NULL, COALESCE(event_seq, 0), d.id)
            < ($3::timestamptz IS NOT NULL, COALESCE($3::timestamptz, '-infinity'::timestamptz),
               $4::bigint IS NOT NULL, COALESCE($4::bigint, 0), $5::uuid))
          AND ($2::text = '' OR search_text IS NULL OR length(search_text) >= 20000
               OR strpos(lower(search_text), lower($2::text)) > 0)
        ORDER BY created_at DESC NULLS LAST, event_seq DESC NULLS LAST, d.id DESC
        LIMIT $6
    )
    SELECT c.id, c.chunk_type, c.created_at, c.event_seq, c.legacy_search, c.search_preview,
           CASE WHEN octet_length(p.payload_msgpack) <= $7
                THEN p.payload_msgpack ELSE NULL END AS payload_msgpack,
           octet_length(p.payload_msgpack) > $7 AS oversized
    FROM candidates c
    CROSS JOIN LATERAL (
        SELECT payload_msgpack FROM context_chunks
        WHERE c.source_priority = 1 AND id = c.source_id AND session_id = $1
        UNION ALL
        SELECT payload_msgpack FROM context_archive
        WHERE c.source_priority = 0 AND id = c.source_id AND session_id = $1
    ) p
    ORDER BY c.created_at DESC NULLS LAST, c.event_seq DESC NULLS LAST, c.id DESC
"""


def _query(params: dict[str, Any]) -> str:
    value = params.get("query", "")
    if not isinstance(value, str) or len(value) > 256:
        raise RpcError(INVALID_PARAMS, "query must be a string of at most 256 characters")
    try:
        value.encode("utf-8")
        if "\x00" in value:
            raise ValueError
    except (UnicodeError, ValueError):
        raise RpcError(INVALID_PARAMS, "query must contain valid text") from None
    # Spaces, percent signs, and underscores remain literal search text.
    return value.lower()


def _cursor(row: Any, session_id: uuid.UUID, query: str) -> str:
    payload = json.dumps(
        [
            1,
            str(session_id),
            hashlib.sha256(query.encode()).hexdigest(),
            row["created_at"].isoformat() if row["created_at"] else None,
            row["event_seq"],
            str(row["id"]),
        ],
        separators=(",", ":"),
    ).encode()
    signature = hmac.digest(_CURSOR_KEY, payload, "sha256")
    return base64.urlsafe_b64encode(signature + payload).decode().rstrip("=")


def _position(value: Any, session_id: uuid.UUID, query: str) -> tuple[Any, Any, Any]:
    if value is None:
        return None, None, None
    try:
        if not isinstance(value, str) or not value or len(value) > MAX_CURSOR_CHARS:
            raise ValueError
        encoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        signature, payload = encoded[:32], encoded[32:]
        if not hmac.compare_digest(signature, hmac.digest(_CURSOR_KEY, payload, "sha256")):
            raise ValueError
        version, sid, query_hash, timestamp, seq, chunk_id = json.loads(payload)
        if (
            version != 1
            or sid != str(session_id)
            or query_hash != hashlib.sha256(query.encode()).hexdigest()
        ):
            raise ValueError
        when = datetime.fromisoformat(timestamp) if timestamp is not None else None
        if when is not None and when.tzinfo is None:
            raise ValueError
        if seq is not None and (type(seq) is not int or not -(2**63) <= seq < 2**63):
            raise ValueError
        return when, seq, uuid.UUID(chunk_id)
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise RpcError(INVALID_PARAMS, "cursor is invalid for this session and query") from None


def _entry(row: Any) -> tuple[dict[str, Any], str, bool]:
    """Return redacted display text, bounded search text, and search completeness."""
    truncated = bool(row["oversized"])
    payload: dict[str, Any] = {}
    if not truncated:
        try:
            decoded = msgpack.unpackb(row["payload_msgpack"], raw=False)
            if not isinstance(decoded, dict):
                raise ValueError
            payload = decoded
        except (ValueError, TypeError, msgpack.UnpackException):
            truncated = True
    kind = row["chunk_type"]
    content = str(
        payload.get("result_preview", "") if kind == "tool_call" else payload.get("content", "")
    )
    if kind == "assistant_message" and payload.get("interrupted"):
        content += "\n[Interrupted reply]"
    tool = str(payload.get("tool", "")) if kind == "tool_call" else None
    search = "\n".join(part for part in (tool, content) if part).lower()
    search_complete = not truncated
    if truncated:
        preview = row["search_preview"]
        if isinstance(preview, str) and preview.strip():
            search = preview.lower()
            # The index itself can end inside a credential at its 20k cap.
            # Withhold a potentially split trailing secret instead of flushing it.
            if len(preview) >= 20000:
                preview = StreamingSecretRedactor().feed(preview)
            content = "[Indexed text preview; full message unavailable]\n" + preview
        else:
            content = (
                "[Message too large to display]" if row["oversized"] else "[Message unavailable]"
            )
    content = redact_secrets(content).text
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_CONTENT_BYTES:
        content = encoded[:MAX_CONTENT_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    entry: dict[str, Any] = {
        "id": str(row["id"]),
        "role": _ROLES[kind],
        "content": content,
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
    }
    if tool is not None:
        entry["tool"] = redact_secrets(tool).text[:256]
    if truncated:
        entry["truncated"] = True
    return entry, search, search_complete


def _serialized_size(value: Any) -> int:
    # ensure_ascii=True is conservative for transports emitting native UTF-8.
    return len(json.dumps(value, ensure_ascii=True).encode("utf-8"))


async def session_history(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Read a newest-first transcript page, without touching active context."""
    gw.require_db()
    session_id = _uuid(params, "sessionId")
    query = _query(params)
    limit = _int(params, "limit", 100, 1, 100)
    if "cursor" in params and params["cursor"] is None:
        raise RpcError(INVALID_PARAMS, "cursor must be a string")
    position = _position(params.get("cursor"), session_id, query)
    session = await gw.get_session(params)
    entries: list[dict[str, Any]] = []
    # A later nonmatching row can produce a longer final cursor than the
    # last accepted entry. Reserve the full cursor/notice envelope once;
    # default JSON list separators contribute two bytes between entries.
    envelope: dict[str, Any] = {"entries": [], "nextCursor": "x" * MAX_CURSOR_CHARS}
    if query:
        envelope["unsearchedCount"] = MAX_SCAN_ROWS
    response_size = _serialized_size(envelope)
    last_scanned = None
    scanned = 0
    unsearched_count = 0
    has_more = False
    while scanned < MAX_SCAN_ROWS:
        batch_size = min(MAX_BATCH_ROWS, MAX_SCAN_ROWS - scanned)
        rows = await db.fetch(
            _PAGE_SQL, session.id, query, *position, batch_size + 1, MAX_PAYLOAD_BYTES
        )
        has_more = len(rows) > batch_size
        stopped = False
        for index, row in enumerate(rows[:batch_size]):
            entry, search, search_complete = _entry(row)
            matches = not query or query in search
            if matches:
                entry_size = _serialized_size(entry) + (2 if entries else 0)
                if len(entries) == limit or response_size + entry_size > MAX_RESPONSE_BYTES:
                    has_more = True
                    stopped = True
                    break
                entries.append(entry)
                response_size += entry_size
            elif not search_complete and row["legacy_search"]:
                unsearched_count += 1
            last_scanned = row
            scanned += 1
            if len(entries) == limit:
                has_more = has_more or index + 1 < len(rows)
                stopped = True
                break
        if stopped or not has_more:
            break
        position = (last_scanned["created_at"], last_scanned["event_seq"], last_scanned["id"])
    result = {
        "entries": entries,
        "nextCursor": _cursor(last_scanned, session_id, query)
        if has_more and last_scanned
        else None,
    }
    if unsearched_count:
        result["unsearchedCount"] = unsearched_count
    return result
