"""One transcript projection feeds resume history, paged history, and export."""

import uuid
from datetime import UTC, datetime

import msgpack
import pytest

from machina import services
from machina.core.models import ContextChunk, Session
from machina.core.transcript import INTERRUPTED_SUFFIX, truncate_utf8
from machina.gateway.features.history import _entry
from machina.gateway.serializers import history_from_chunks

SID = uuid.UUID("10000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 11, 2, tzinfo=UTC)


def chunk(number, kind, payload):
    return ContextChunk(
        id=uuid.UUID(int=number),
        session_id=SID,
        agent_id="harness",
        chunk_type=kind,
        payload=payload,
    )


def history_row(number, kind, payload, *, oversized=False, raw=None, preview=None):
    return {
        "id": uuid.UUID(int=number),
        "chunk_type": kind,
        "created_at": NOW,
        "event_seq": None,
        "payload_msgpack": (
            raw
            if raw is not None
            else None
            if oversized
            else msgpack.packb(payload, use_bin_type=True)
        ),
        "legacy_search": False,
        "search_preview": preview,
        "oversized": oversized,
    }


def test_serializer_projects_roles_interruption_tool_previews_and_summaries():
    chunks = [
        chunk(5, "compression_summary", {"content": "summary text"}),
        chunk(
            4,
            "tool_call",
            {"tool": "shell", "result_preview": "preview only", "result": "full output"},
        ),
        chunk(3, "assistant_message", {"content": "cut off", "interrupted": True}),
        chunk(2, "user_message", {"content": "hello"}),
        chunk(1, "document", {"content": "not a transcript row"}),
    ]

    assert history_from_chunks(chunks) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "cut off" + INTERRUPTED_SUFFIX},
        {"role": "tool", "tool": "shell", "content": "preview only"},
        {"role": "system", "content": "summary text"},
    ]


def test_serializer_remains_raw_for_the_outward_redaction_layer():
    secret = "sk-" + "A1b2C3d4" * 5
    assert history_from_chunks([chunk(1, "user_message", {"content": secret})]) == [
        {"role": "user", "content": secret}
    ]


@pytest.mark.asyncio
async def test_export_redacts_and_renders_interrupted_tool_and_summary_sections(monkeypatch):
    session = Session(
        id=uuid.UUID(int=100),
        title="Export password=title-secret",
        agent_id="harness",
        goal="Goal password=goal-secret",
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        last_activity=datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC),
    )
    chunks = [
        chunk(14, "compression_summary", {"content": "Summary password=summary-secret"}),
        chunk(
            13,
            "tool_call",
            {
                "tool": "shell",
                "args": {"cmd": "echo password=arg-secret"},
                "result": "result password=result-secret",
                "result_truncated": True,
            },
        ),
        chunk(
            12,
            "assistant_message",
            {"content": "assistant password=assistant-secret", "interrupted": True},
        ),
        chunk(11, "user_message", {"content": "user password=user-secret"}),
    ]

    async def transcript_chunks(session_id, **_kwargs):
        assert session_id == session.id
        return chunks

    monkeypatch.setattr(services.context_manager, "get_transcript_chunks", transcript_chunks)
    report = await services._export_one_session(session)

    assert report == (
        "# Session: Export password=[REDACTED_PASSWORD]\n"
        "\n"
        "**ID:** 00000000-0000-0000-0000-000000000064\n"
        "**Status:** active\n"
        "**Agent:** harness\n"
        "**Goal:** Goal password=[REDACTED_PASSWORD]\n"
        "**Created:** 2026-01-02 03:04:05\n"
        "**Last Activity:** 2026-01-02 03:04:06\n"
        "\n"
        "## Conversation\n"
        "\n"
        "### User\n"
        "\n"
        "user password=[REDACTED_PASSWORD]\n"
        "\n"
        "### Assistant (interrupted)\n"
        "\n"
        "assistant password=[REDACTED_PASSWORD]\n"
        "\n"
        "**Tool:** `shell({'cmd': 'echo password=[REDACTED_PASSWORD]})`\n"
        "**Result:** result password=[REDACTED_PASSWORD]\n"
        "[Tool output truncated at the storage limit of 65,536 characters.]\n"
        "\n"
        "### Summary of earlier context\n"
        "\n"
        "Summary password=[REDACTED_PASSWORD]\n"
    )
    assert "title-secret" not in report
    assert "goal-secret" not in report
    assert "user-secret" not in report
    assert "assistant-secret" not in report
    assert "arg-secret" not in report
    assert "result-secret" not in report
    assert "summary-secret" not in report
    # The export marks interruption in the heading; the inline suffix belongs
    # to resume/paged transcript rendering only.
    assert INTERRUPTED_SUFFIX not in report


@pytest.mark.asyncio
async def test_export_marks_preview_only_tool_output(monkeypatch):
    session = Session(id=uuid.UUID(int=101), title="Tools")
    chunks = [
        chunk(2, "tool_call", {"tool": "read", "result_preview": "stored preview"}),
        chunk(1, "tool_call", {"tool": "write", "result": "full result"}),
    ]

    async def transcript_chunks(session_id, **_kwargs):
        return chunks

    monkeypatch.setattr(services.context_manager, "get_transcript_chunks", transcript_chunks)
    report = await services._export_one_session(session)

    assert "**Tool:** `read({})`" in report
    assert "**Result:** stored preview" in report
    assert "[Historical tool output: only a preview was stored.]" in report
    assert "**Tool:** `write({})`" in report
    assert "**Result:** full result" in report
    assert report.count("[Historical tool output: only a preview was stored.]") == 1


def test_history_projects_interrupted_assistant_tool_preview_and_summary():
    interrupted, search, complete = _entry(
        history_row(3, "assistant_message", {"content": "cut off", "interrupted": True})
    )
    assert interrupted == {
        "id": str(uuid.UUID(int=3)),
        "role": "assistant",
        "content": "cut off" + INTERRUPTED_SUFFIX,
        "createdAt": NOW.isoformat(),
    }
    assert "interrupted reply" in search
    assert complete is True

    tool, search, complete = _entry(
        history_row(
            2,
            "tool_call",
            {"tool": "shell", "result_preview": "preview only", "result": "full output"},
        )
    )
    assert tool == {
        "id": str(uuid.UUID(int=2)),
        "role": "tool",
        "content": "preview only",
        "createdAt": NOW.isoformat(),
        "tool": "shell",
    }
    assert search == "shell\npreview only"
    assert complete is True

    summary, search, complete = _entry(
        history_row(1, "compression_summary", {"content": "summary text"})
    )
    assert summary == {
        "id": str(uuid.UUID(int=1)),
        "role": "system",
        "content": "summary text",
        "createdAt": NOW.isoformat(),
    }
    assert search == "summary text"
    assert complete is True


def test_history_redacts_content_and_tool_names_but_keeps_raw_search_text():
    secret = "sk-" + "A1b2C3d4" * 5
    entry, search, complete = _entry(history_row(2, "user_message", {"content": "key " + secret}))
    assert secret not in entry["content"]
    assert "REDACTED" in entry["content"]
    assert search == ("key " + secret).lower()
    assert complete is True

    entry, search, complete = _entry(
        history_row(1, "tool_call", {"tool": secret, "result_preview": "ok"})
    )
    assert entry["tool"] == "[REDACTED_OPENAI_KEY]"
    assert secret not in entry["tool"]
    assert search == (secret + "\nok").lower()
    assert complete is True


def test_history_oversized_rows_use_preview_placeholder_and_truncation_marker():
    entry, search, complete = _entry(
        history_row(
            1,
            "user_message",
            {},
            oversized=True,
            preview="A picture of a chair. password=caption-secret",
        )
    )
    assert entry["content"].startswith("[Indexed text preview; full message unavailable]\n")
    assert "A picture of a chair." in entry["content"]
    assert "caption-secret" not in entry["content"]
    assert "REDACTED" in entry["content"]
    assert entry["role"] == "user"
    assert entry["truncated"] is True
    assert search == "a picture of a chair. password=caption-secret"
    assert complete is False

    entry, search, complete = _entry(history_row(2, "assistant_message", {}, oversized=True))
    assert entry["content"] == "[Message too large to display]"
    assert entry["truncated"] is True
    assert search == ""
    assert complete is False

    entry, search, complete = _entry(history_row(3, "assistant_message", {}, raw=b"\xc1"))
    assert entry["content"] == "[Message unavailable]"
    assert entry["truncated"] is True
    assert search == ""
    assert complete is False


def test_history_caps_oversized_content_on_a_utf8_byte_boundary():
    entry, _search, complete = _entry(
        history_row(1, "assistant_message", {"content": "😀" * 20000})
    )
    assert len(entry["content"].encode("utf-8")) <= 32 * 1024
    assert "\ufffd" not in entry["content"]
    assert entry["truncated"] is True
    assert complete is True

    text, capped = truncate_utf8("😀" * 3, 5)
    assert text == "😀"
    assert capped is True


def test_serializer_and_history_share_role_content_and_tool_projection():
    chunks = [
        chunk(4, "compression_summary", {"content": "summary text"}),
        chunk(3, "tool_call", {"tool": "shell", "result_preview": "preview only"}),
        chunk(2, "assistant_message", {"content": "cut off", "interrupted": True}),
        chunk(1, "user_message", {"content": "hello"}),
    ]
    wire = history_from_chunks(chunks)
    projected = [
        _entry(history_row(number, item.chunk_type, item.payload))[0]
        for number, item in enumerate(reversed(chunks), start=1)
    ]

    assert [entry["role"] for entry in projected] == [entry["role"] for entry in wire]
    assert [entry["content"] for entry in projected] == [entry["content"] for entry in wire]
    assert [entry.get("tool") for entry in projected] == [entry.get("tool") for entry in wire]
