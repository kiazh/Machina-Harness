"""Bounded, redacted records of real harness boundary traffic.

MCP servers and the desktop gateway are separate processes. A small SQLite
store under the installation's state root lets either append without a live
gateway or PostgreSQL connection. This is observation only: write failures
must never alter a tool result or write to the stdio protocol.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

MAX_EVENTS = 1_000
MAX_BYTES = 4 * 1024 * 1024
PAYLOAD_MAX_CHARS = 12_000
_SENSITIVE_KEY = re.compile(
    r"token|password|passwd|secret|authorization|credential|cookie|api.?key|"
    r"headers|(?:^|_)env(?:ironment)?$|raw.?body",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"(?i)(?<![\w.-])([\w.-]{0,100}(?:token|password|passwd|secret|authorization|credential|cookie|"
    r"api[_-]?key)[\w.-]{0,100}[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
_AUTH_HEADER = re.compile(
    r"(?im)^(\s*(?:authorization|proxy-authorization|cookie|set-cookie)\s*:).*$"
)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def store_path() -> Path:
    """Use the same state-root override as Machina's local auth stores."""
    home = os.environ.get("MACHINA_HOME", "").strip()
    if not home:
        try:
            from machina.core.config import config

            home = str(config.get("machina_home") or "").strip()
        except Exception:
            home = ""
    root = Path(home).expanduser() if home else Path.home() / ".machina"
    return root / "communications.sqlite3"


def _safe_text(text: str) -> str:
    try:
        from machina.memory.redaction import redact_diagnostic

        # Whole values are redacted before any prefix/tail is selected.
        text = _ANSI.sub("", text)
        text = _AUTH_HEADER.sub(r"\1 [REDACTED]", text)
        text = _ASSIGNMENT.sub(r"\1[REDACTED]", text)
        return redact_diagnostic(text)
    except Exception:
        return "[redaction unavailable]"


def _safe_value(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[nested content omitted]"
    if isinstance(value, dict):
        return {
            _safe_text(str(key))[:120]: (
                "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else _safe_value(item, depth + 1)
            )
            for key, item in list(value.items())[:60]
        }
    if isinstance(value, (tuple, list)):
        values = [_safe_value(item, depth + 1) for item in value[:30]]
        if len(value) > 30:
            values.append("[additional items omitted]")
        return values
    if isinstance(value, str):
        # Public MCP text often contains a JSON result. Decode it to apply
        # sensitive-key filtering rather than persisting embedded raw bodies.
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                return _safe_value(parsed, depth + 1)
        return _safe_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[non-text content omitted]"


def _bounded(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    marker = "\n[truncated: middle omitted]\n"
    remaining = max(0, limit - len(marker))
    head = (remaining * 2) // 3
    tail = remaining - head
    return text[:head] + marker + (text[-tail:] if tail else ""), True


def canonical_session(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value))) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


def public_result(value: Any, depth: int = 0) -> tuple[str, str | None]:
    """Derive honest settlement/session metadata from a public tool result."""
    if depth > 8:
        return "completed", None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return "completed", None
    if not isinstance(value, dict):
        return "completed", None
    session = canonical_session(value.get("session_id") or value.get("sessionId"))
    status = value.get("status")
    if status in ("waiting_approval", "paused_for_approval"):
        return "waiting_approval", session
    if status in ("cancelled", "canceled"):
        return "cancelled", session
    if status in ("failed", "error", "timeout", "invalid_request", "partial"):
        return "failed", session
    if value.get("ok") is False or value.get("isError") is True:
        return "failed", session
    # An MCP response can contain JSON as a text block (and often also
    # structuredContent). Read only public text, never the request context.
    content = value.get("content")
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            inner_status, inner_session = public_result(block.get("text", ""), depth + 1)
            if inner_status != "completed" or inner_session:
                return inner_status, inner_session or session
    return "completed", session


class CommunicationsStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        max_events: int = MAX_EVENTS,
        max_bytes: int = MAX_BYTES,
        payload_max_chars: int = PAYLOAD_MAX_CHARS,
    ) -> None:
        self.path = path if path is not None else store_path()
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.payload_max_chars = payload_max_chars

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if not readonly:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro" if readonly else self.path,
            uri=readonly,
            timeout=0.025,
        )
        try:
            connection.row_factory = sqlite3.Row
            if readonly:
                return connection
            if os.name != "nt":
                self.path.chmod(0o600)
            connection.execute("PRAGMA auto_vacuum = FULL")
            connection.execute("PRAGMA secure_delete = ON")
            # Bound the main DB physically as well as its retained payloads.
            connection.execute("PRAGMA max_page_count = 1280")
            connection.execute("""CREATE TABLE IF NOT EXISTS communications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_json TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                session_id TEXT, harness TEXT
            )""")
            return connection
        except BaseException:
            connection.close()
            raise

    def append(
        self,
        *,
        correlation_id: str,
        direction: str,
        phase: str,
        method: str,
        transport: str,
        status: str,
        payload: Any,
        harness: str | None = None,
        session_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        safe = _safe_value(payload)
        text = safe if isinstance(safe, str) else json.dumps(safe, ensure_ascii=False, indent=2)
        text, truncated = _bounded(text, self.payload_max_chars)
        safe_harness = _safe_text(harness)[:100] if harness else None
        event = {
            "correlationId": str(uuid.UUID(correlation_id)),
            "direction": direction,
            "phase": phase,
            "transport": transport,
            "method": _safe_text(method)[:100],
            "harness": safe_harness,
            "sessionId": canonical_session(session_id),
            "status": status,
            "summary": " ".join(text.split())[:180],
            "payload": text,
            "truncated": truncated,
            "createdAt": datetime.now(UTC).isoformat(),
            "elapsedMs": elapsed_ms,
        }
        encoded = json.dumps(event, ensure_ascii=False)
        size = len(encoded.encode("utf-8")) + 128
        if size > self.max_bytes:
            return
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO communications (event_json, size_bytes, session_id, harness) VALUES (?, ?, ?, ?)",
                (encoded, size, event["sessionId"], safe_harness),
            )
            retained_bytes = 0
            for index, row in enumerate(
                connection.execute(
                    "SELECT id, size_bytes FROM communications ORDER BY id DESC"
                ).fetchall()
            ):
                retained_bytes += row["size_bytes"]
                if index >= self.max_events or retained_bytes > self.max_bytes:
                    connection.execute("DELETE FROM communications WHERE id <= ?", (row["id"],))
                    break
            connection.commit()
        finally:
            connection.close()

    def list(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        session_id: str | None = None,
        harness: str | None = None,
    ) -> dict[str, Any]:
        where = []
        values: list[Any] = []
        if cursor is not None:
            where.append("id > ?")
            values.append(int(cursor))
        if session_id is not None:
            where.append("session_id = ?")
            values.append(session_id)
        if harness is not None:
            where.append("harness = ?")
            values.append(harness)
        clause = " WHERE " + " AND ".join(where) if where else ""
        order = "ASC" if cursor is not None else "DESC"
        # An empty read must not create an on-disk store or mutate state.
        if self.path.exists():
            connection = self._connect(readonly=True)
            try:
                rows = connection.execute(
                    f"SELECT id, event_json FROM communications{clause} ORDER BY id {order} LIMIT ?",
                    (*values, limit + 1),
                ).fetchall()
            finally:
                connection.close()
        else:
            rows = []
        has_more = len(rows) > limit
        rows = rows[:limit]
        if cursor is None:
            rows.reverse()
        events = [dict(json.loads(row["event_json"]), id=str(row["id"])) for row in rows]
        return {
            "events": events,
            "nextCursor": events[-1]["id"] if events else (cursor or "0"),
            "hasMore": has_more,
            "retention": {
                "maxEvents": self.max_events,
                "maxBytes": self.max_bytes,
                "payloadMaxChars": self.payload_max_chars,
            },
        }


def record_event(**kwargs: Any) -> None:
    """Best-effort observation, silent even if storage/redaction is broken."""
    try:
        CommunicationsStore().append(**kwargs)
    except Exception:
        pass


def start_exchange(
    *,
    method: str,
    transport: str,
    direction: str,
    payload: Any,
    harness: str | None = None,
    session_id: str | None = None,
) -> tuple[str, float]:
    correlation = str(uuid.uuid4())
    started = time.monotonic()
    record_event(
        correlation_id=correlation,
        method=method,
        transport=transport,
        direction=direction,
        phase="request",
        status="running",
        payload=payload,
        harness=harness,
        session_id=session_id,
    )
    return correlation, started


def finish_exchange(
    exchange: tuple[str, float],
    *,
    method: str,
    transport: str,
    direction: str,
    payload: Any,
    status: str | None = None,
    harness: str | None = None,
    session_id: str | None = None,
) -> None:
    inferred_status, result_session = public_result(payload)
    record_event(
        correlation_id=exchange[0],
        method=method,
        transport=transport,
        direction=direction,
        phase="response",
        status=status or inferred_status,
        payload=payload,
        harness=harness,
        session_id=result_session or session_id,
        elapsed_ms=max(0, round((time.monotonic() - exchange[1]) * 1000)),
    )


async def async_start_exchange(**kwargs: Any) -> tuple[str, float]:
    """Keep disk/redaction off the protocol loop and settle a cancelled start."""
    from machina.core.background_tasks import CompletedOnCancellation, await_joined

    try:
        worker = asyncio.get_running_loop().run_in_executor(None, partial(start_exchange, **kwargs))
        return await await_joined(worker, capture_result=True)
    except CompletedOnCancellation as exc:
        await async_finish_exchange(
            exc.result,
            method=kwargs["method"],
            transport=kwargs["transport"],
            direction="outgoing" if kwargs["direction"] == "incoming" else "incoming",
            payload={"status": "cancelled"},
            status="cancelled",
            harness=kwargs.get("harness"),
            session_id=kwargs.get("session_id"),
        )
        raise asyncio.CancelledError from None


async def async_finish_exchange(exchange: tuple[str, float], **kwargs: Any) -> None:
    """Join the small best-effort write even under repeated caller cancellation."""
    from machina.core.background_tasks import await_joined

    worker = asyncio.get_running_loop().run_in_executor(
        None, partial(finish_exchange, exchange, **kwargs)
    )
    await await_joined(worker)
