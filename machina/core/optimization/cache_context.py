"""Per-request context the optimization layer is allowed to see.

The context is deliberately small and contains no prompt bodies. Prompt text
is canonicalized separately, and only hashes/counts are persisted by shadow
telemetry. A context is built conservatively: caching a call requires an
explicit read-only classification, not merely the absence of an obvious
mutation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from typing import Any

#: Auxiliary purposes that are read-only by construction. Other auxiliary
#: purposes (learning/consolidation) are intentionally absent until reviewed.
AUXILIARY_READ_ONLY_PURPOSES = frozenset(
    {
        "compression_summary",
        "memory_rerank",
        "skill_metadata",
    }
)


@dataclass(frozen=True)
class CacheContext:
    """Facts that scope one cache/compression decision."""

    session_id: uuid.UUID | None
    agent_id: str
    provider: str
    model: str
    purpose: str = "agent"
    task_class: str = "unknown"
    operation_scope: str = "unknown"
    temperature: float = 0.0
    max_tokens: int = 4096
    tools_present: bool = False
    read_only: bool = False
    auxiliary: bool = False
    freshness_sensitive: bool = False
    attachments: bool = False
    workspace_root: str = ""
    tool_schema_hash: str = ""
    compressor_revision: str = "none"

    @property
    def is_auxiliary_read_only(self) -> bool:
        return (
            self.auxiliary
            and self.read_only
            and self.operation_scope == "read-only"
            and self.purpose in AUXILIARY_READ_ONLY_PURPOSES
        )

    @classmethod
    def for_auxiliary(
        cls,
        *,
        session_id: uuid.UUID | None,
        agent_id: str,
        provider: str,
        model: str,
        purpose: str,
        messages: list[dict[str, Any]] | None = None,
        tools: list[Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        compressor_revision: str = "none",
        workspace_root: str = "",
    ) -> CacheContext:
        """Build a conservative auxiliary context.

        Auxiliary calls are only eligible when their purpose is on the
        read-only allowlist. Everything else stays ineligible even when the
        semantic cache is wired, so a summary/rerank optimization cannot
        silently cache a learning or memory-write call.
        """
        text = _messages_text(messages or [])
        return cls(
            session_id=session_id,
            agent_id=agent_id,
            provider=provider,
            model=model,
            purpose=purpose,
            task_class="auxiliary",
            operation_scope="read-only",
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            tools_present=bool(tools),
            read_only=True,
            auxiliary=True,
            freshness_sensitive=_has_freshness_markers(text),
            attachments=_has_attachments(messages or []),
            workspace_root=workspace_root,
            tool_schema_hash=_tool_hash(tools),
            compressor_revision=compressor_revision,
        )

    def stateless(self) -> CacheContext:
        """Drop session scope for the explicit cross-session auxiliary mode."""
        return replace(self, session_id=None)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif block.get("type") == "image" or "image" in str(block.get("type", "")):
                    parts.append("[image]")
        return "\n".join(parts)
    return "" if content is None else str(content)


def _messages_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(
        _content_text(message.get("content")) for message in messages if isinstance(message, dict)
    )


def _tool_hash(tools: list[Any] | None) -> str:
    """Stable tool-schema identity without importing the canonicalizer."""
    normalized = []
    for tool in tools or []:
        if isinstance(tool, dict):
            normalized.append(tool)
        else:
            normalized.append(
                {
                    "name": getattr(tool, "name", ""),
                    "description": getattr(tool, "description", ""),
                    "parameters": getattr(tool, "parameters", {}),
                }
            )
    payload = json.dumps(normalized, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_FRESHNESS_MARKERS = (
    "current time",
    "current date",
    "today's date",
    "right now",
    "as of now",
    "latest news",
    "live price",
    "stock price",
    "weather",
)


def _has_freshness_markers(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _FRESHNESS_MARKERS)


def _has_attachments(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and str(block.get("type", "")).startswith("image"):
                return True
            if isinstance(block, dict) and "image_url" in block:
                return True
    return False
