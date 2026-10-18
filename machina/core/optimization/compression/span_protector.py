"""Span-level protection for prompt compression.

Protection is message-granular and conservative. Code, diffs, URLs, paths,
line references, recent turns, system instructions, and structured tool
output are never compressed. The canonical conversation is never mutated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CompressionSpan",
    "compressible_spans",
    "message_text",
    "protected_reasons",
]

_PROTECTED_ROLES = frozenset({"system", "developer"})
_ELIGIBLE_ROLES = frozenset({"assistant", "tool"})
_MIN_COMPRESSIBLE_CHARS = 400

_CODE_FENCE_RE = re.compile(r"```|~~~")
_DIFF_RE = re.compile(r"^(?:diff --git|@@|\+\+\+|---) ", re.MULTILINE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:^|[\s(\"'`])(?:\.{0,2}/|~/|[A-Za-z]:\\)[\w./\\-]+")
_LINE_REF_RE = re.compile(r"\b[\w./\\-]+:\d+(?::\d+)?\b")
_STRUCTURED_RE = re.compile(r"^\s*[\[{]", re.DOTALL)
_ERROR_RE = re.compile(r"\b(?:traceback|exception|error|failed)\b", re.IGNORECASE)


@dataclass(frozen=True)
class CompressionSpan:
    message_index: int
    role: str
    text: str


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def protected_reasons(message: dict[str, Any], *, index: int, total: int) -> tuple[str, ...]:
    """Return every reason this message must stay byte-identical."""
    reasons: list[str] = []
    role = str(message.get("role") or "")
    raw_content = message.get("content")
    text = message_text(raw_content)
    if not isinstance(raw_content, str):
        # Block-form content can carry images/tool blocks; replacing it with a
        # string would discard structure, so it is never a compression span.
        reasons.append("non_text")
    if role in _PROTECTED_ROLES:
        reasons.append("system")
    if role not in _ELIGIBLE_ROLES:
        reasons.append("role")
    if index >= total - 2:
        reasons.append("recent")
    if len(text) < _MIN_COMPRESSIBLE_CHARS:
        reasons.append("short")
    if _CODE_FENCE_RE.search(text):
        reasons.append("code")
    if _DIFF_RE.search(text):
        reasons.append("diff")
    if _URL_RE.search(text):
        reasons.append("url")
    if _PATH_RE.search(text):
        reasons.append("path")
    if _LINE_REF_RE.search(text):
        reasons.append("line_reference")
    if _STRUCTURED_RE.search(text):
        reasons.append("structured")
    if _ERROR_RE.search(text):
        reasons.append("error_text")
    return tuple(reasons)


def compressible_spans(messages: list[dict[str, Any]]) -> list[CompressionSpan]:
    """Select old, long, plain assistant/tool messages with no protected text."""
    spans: list[CompressionSpan] = []
    total = len(messages)
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        if protected_reasons(message, index=index, total=total):
            continue
        text = message_text(message.get("content"))
        spans.append(CompressionSpan(index, str(message.get("role") or ""), text))
    return spans
