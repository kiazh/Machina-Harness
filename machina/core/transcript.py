"""Canonical projection of stored transcript chunks for display surfaces.

Durable storage and raw reads belong to ``machina.core.context``; this module
owns only the policy that turns one stored chunk into a visible turn. History
paging, cursors, search completeness, SQL byte caps, and Markdown layout stay
with their transport-specific callers.

The same policy answers every surface's shared questions:

* which role a stored chunk type has;
* how an interrupted assistant reply is marked inline;
* which tool name, preview, and result a tool chunk displays;
* whether display text is passed through the shared secret redactor.

Callers that expose raw history (session resume and the HTTP history route,
which applies its own outward redaction) opt out of redaction; the Markdown
export opts in.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import msgpack

from machina.memory.redaction import redact_secrets

if TYPE_CHECKING:
    from machina.core.models import ContextChunk

__all__ = [
    "INTERRUPTED_SUFFIX",
    "ROLE_BY_CHUNK_TYPE",
    "ToolDetail",
    "ToolProjection",
    "TranscriptProjector",
    "TranscriptTurn",
    "decode_payload",
    "role_for",
    "truncate_utf8",
]

ROLE_BY_CHUNK_TYPE: dict[str, str] = {
    "user_message": "user",
    "assistant_message": "assistant",
    "tool_call": "tool",
    "compression_summary": "system",
}

INTERRUPTED_SUFFIX = "\n[Interrupted reply]"


def role_for(chunk_type: str) -> str:
    """Map one stored chunk type to its public transcript role."""
    return ROLE_BY_CHUNK_TYPE[chunk_type]


def decode_payload(raw: Any) -> dict[str, Any] | None:
    """Decode one stored MessagePack payload, or None when it is unreadable."""
    try:
        decoded = msgpack.unpackb(raw, raw=False)
    except (ValueError, TypeError, msgpack.UnpackException):
        return None
    return decoded if isinstance(decoded, dict) else None


def truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """Cap *text* at *max_bytes* of UTF-8 without splitting a code point."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


@dataclass(frozen=True)
class ToolProjection:
    """Common tool fields shared by every transcript surface.

    ``preview`` is always the bounded ``result_preview`` text; ``preview_only``
    records whether a full result was ever stored, and ``truncated`` whether
    storage capped it. Full-result selection lives in :class:`ToolDetail`, so
    paged history never stringifies a large result it will not display.
    """

    name: str
    preview: str
    preview_only: bool
    truncated: bool


@dataclass(frozen=True)
class ToolDetail:
    """Full-result tool fields rendered only by the Markdown export.

    ``result`` is the selected result text (``result`` falling back to
    ``result_preview``); ``has_result`` preserves the raw value's truthiness so
    an empty stored result is not rendered.
    """

    args: str
    result: str
    has_result: bool


@dataclass(frozen=True)
class TranscriptTurn:
    """One projected transcript chunk, before transport-specific rendering."""

    role: str
    content: str
    tool: ToolProjection | None = None
    interrupted: bool = False

    def render_content(self) -> str:
        """Inline content, including the shared interrupted-reply suffix."""
        if self.interrupted:
            return self.content + INTERRUPTED_SUFFIX
        return self.content


class TranscriptProjector:
    """Project stored chunks under one redaction and tool-default policy."""

    def __init__(self, *, redact: bool = False, tool_name_default: str = "") -> None:
        self.redact = redact
        self.tool_name_default = tool_name_default

    def display(self, value: Any) -> str:
        """Apply this projector's display policy to one value."""
        text = str(value)
        return self.redacted(text) if self.redact else text

    @staticmethod
    def redacted(value: Any) -> str:
        """Apply the shared redaction policy to one display value."""
        return redact_secrets(str(value)).text

    def chunk(self, chunk: ContextChunk) -> TranscriptTurn | None:
        """Project a stored chunk, or None for a non-transcript chunk type."""
        if chunk.chunk_type not in ROLE_BY_CHUNK_TYPE:
            return None
        return self.turn(chunk.chunk_type, chunk.payload)

    def turn(self, chunk_type: str, payload: Mapping[str, Any]) -> TranscriptTurn:
        """Project one decoded payload into a role/content/tool turn."""
        tool = self.tool(payload) if chunk_type == "tool_call" else None
        content = tool.preview if tool is not None else self.display(payload.get("content", ""))
        interrupted = chunk_type == "assistant_message" and bool(payload.get("interrupted"))
        return TranscriptTurn(
            role=role_for(chunk_type),
            content=content,
            tool=tool,
            interrupted=interrupted,
        )

    def tool(self, payload: Mapping[str, Any]) -> ToolProjection:
        """Select the tool fields every surface displays."""
        return ToolProjection(
            name=self.display(payload.get("tool", self.tool_name_default)),
            preview=self.display(payload.get("result_preview", "")),
            preview_only="result" not in payload,
            truncated=bool(payload.get("result_truncated")),
        )

    def tool_detail(self, payload: Mapping[str, Any]) -> ToolDetail:
        """Select the full tool fields rendered by the Markdown export."""
        raw_result = payload.get("result", payload.get("result_preview", ""))
        return ToolDetail(
            args=self.display(payload.get("args", {})),
            result=self.display(raw_result),
            has_result=bool(raw_result),
        )
