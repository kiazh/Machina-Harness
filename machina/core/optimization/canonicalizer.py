"""Canonical request text, hashes, and scope fingerprints.

Canonicalization never changes what is sent to a provider. It produces a
stable representation for cache lookup and telemetry only. Volatile provider
metadata (cache markers, request ids, timestamps) is excluded; inline
time-sensitive text is rejected by the eligibility policy instead of being
silently stripped.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from machina.core.optimization.cache_context import CacheContext

__all__ = [
    "CanonicalRequest",
    "canonicalize",
    "has_freshness_markers",
    "scope_fingerprint",
    "tool_schema_hash",
]

_VOLATILE_KEYS = frozenset(
    {
        "cache_control",
        "created_at",
        "id",
        "request_id",
        "timestamp",
        "updated_at",
    }
)
_FRESHNESS_RE = re.compile(
    r"(?:\b(?:today|tomorrow|yesterday|now|currently|latest|breaking)\b"
    r"|\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?\b"
    r"|\b(?:current|live)\s+(?:time|date|price|weather|news)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CanonicalRequest:
    """Stable request representation used for hashing and embedding."""

    text: str
    request_hash: str
    char_count: int
    message_count: int

    @property
    def embedding_text(self) -> str:
        # Shared embedders truncate to this width. Eligibility rejects longer
        # canonical requests, so this is a defensive bound, not silent loss.
        return self.text[:2000]


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_volatile(item)
            for key, item in value.items()
            if str(key).lower() not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


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
                elif str(block.get("type", "")).startswith("image") or "image_url" in block:
                    parts.append("[image]")
                elif block.get("type") == "tool_use":
                    parts.append(f"[tool_use:{block.get('name', '')}]")
                elif block.get("type") == "tool_result":
                    parts.append("[tool_result]")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    # Keep code indentation and blank lines inside code; only trim the
    # request edges and collapse long runs of blank lines.
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run > 2:
                continue
        else:
            blank_run = 0
        out.append(line)
    return "\n".join(out).strip()


def tool_schema_hash(tools: list[Any] | None) -> str:
    """Stable hash of tool names/schemas, excluding wire-only cache markers."""
    normalized = []
    for tool in tools or []:
        if hasattr(tool, "model_dump"):
            raw = tool.model_dump()
        elif hasattr(tool, "__dict__") and not isinstance(tool, dict):
            raw = {
                "name": getattr(tool, "name", ""),
                "description": getattr(tool, "description", ""),
                "parameters": getattr(tool, "parameters", {}),
            }
        else:
            raw = tool
        normalized.append(_strip_volatile(raw))
    payload = json.dumps(normalized, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonicalize(
    messages: list[dict[str, Any]] | None,
    tools: list[Any] | None = None,
) -> CanonicalRequest:
    """Build a stable text/hash for *messages* without mutating them."""
    parts: list[str] = []
    count = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        content = _normalize_text(_content_text(message.get("content")))
        parts.append(f"{role}:\n{content}")
        count += 1
    schema = tool_schema_hash(tools)
    if tools:
        parts.append(f"tools:{schema}")
    text = "\n\n".join(parts)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return CanonicalRequest(
        text=text,
        request_hash=digest,
        char_count=len(text),
        message_count=count,
    )


def has_freshness_markers(text: str) -> bool:
    return bool(_FRESHNESS_RE.search(text or ""))


def scope_fingerprint(
    context: CacheContext,
    *,
    embedding_space: str,
    schema_hash: str,
    policy_version: str = "semantic-cache-v1",
) -> str:
    """Hash every guard fact; similarity is only evaluated within this scope."""
    session = str(context.session_id) if context.session_id else ""
    # Cross-session auxiliary reuse is explicit and stateless. Interactive
    # turns always keep their session in the fingerprint.
    if context.auxiliary and not session:
        session = "stateless-aux"
    payload = {
        "policy_version": policy_version,
        "session_id": session,
        "agent_id": context.agent_id,
        "provider": context.provider,
        "model": context.model,
        "purpose": context.purpose,
        "task_class": context.task_class,
        "operation_scope": context.operation_scope,
        "workspace_root": context.workspace_root,
        "tool_schema_hash": schema_hash,
        "temperature": round(float(context.temperature), 4),
        "max_tokens": int(context.max_tokens),
        "compressor_revision": context.compressor_revision,
        "embedding_space": embedding_space,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
