"""Provider-agnostic prompt-cache marker helpers.

Kept outside ``machina/core/provider`` so the provider package stays within its
ratified line ceiling. These helpers only copy message dicts and attach an
Anthropic-style ephemeral marker; they never import a provider or perform I/O.
"""

from __future__ import annotations

from typing import Any


def _ephemeral_cache_control() -> dict[str, str]:
    """Fresh Anthropic-style ephemeral breakpoint marker."""
    return {"type": "ephemeral"}


def _mark_stable_history_breakpoint(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Mark the last stable history message before the live user tail.

    The final message changes every step (dynamic activity/RAG/query), so a
    breakpoint there only helps within a tool-continuation. On a fresh user
    turn the penultimate message is the stable history tail; marking it lets
    the next turn reuse the append-only history prefix. Skipped when there is
    no penultimate non-system message or the live tail is not a user turn.
    """
    if len(messages) < 2 or messages[-1].get("role") != "user":
        return None
    message = messages[-2]
    if message.get("role") not in ("user", "assistant"):
        return None
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return None
        marked: Any = [
            {"type": "text", "text": content, "cache_control": _ephemeral_cache_control()}
        ]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        marked = [
            *content[:-1],
            {**content[-1], "cache_control": _ephemeral_cache_control()},
        ]
    else:
        return None
    out = list(messages)
    out[-2] = {**message, "content": marked}
    return out


def _mark_message_tail_breakpoint(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Copy *messages* with an ephemeral breakpoint on the final content block.

    The conversation tail is what makes within-turn reuse work: each
    tool-continuation request shares every earlier message byte-for-byte, so a
    breakpoint at the last block lets the provider read the growing prefix
    from cache instead of re-processing it. Returns None when there is nothing
    to mark (empty list, empty text, or unsupported content shape); the
    caller's list and dicts are never mutated.
    """
    if not messages:
        return None
    message = messages[-1]
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return None
        marked: Any = [
            {"type": "text", "text": content, "cache_control": _ephemeral_cache_control()}
        ]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        marked = [
            *content[:-1],
            {**content[-1], "cache_control": _ephemeral_cache_control()},
        ]
    else:
        return None
    out = list(messages)
    out[-1] = {**message, "content": marked}
    return out
