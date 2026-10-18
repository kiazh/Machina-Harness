"""Resume continuity loading and compaction-boundary persistence.

Kept outside ``agent.py`` so the turn orchestrator stays within its ratified
god-file ceiling. The helper owns the ordering rule that matters for provider
prefix caches: load durable user/assistant turns before appending the live user
message, fold only when the high watermark is crossed, and persist the new
boundary plus summary atomically.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from machina.core.assembler import PromptAssembler, truncate_to_tokens
from machina.core.prompt import (
    _request_context_tokens,
    assemble_turn_prompt,
    build_native_turn_messages,
)
from machina.observability.diagnostics import record_failure


async def prepare_continuity(
    session_id: uuid.UUID,
    session: Any,
    *,
    context: Any,
    sessions: Any,
) -> tuple[list[dict[str, str]], str | None, list[dict[str, Any]]]:
    """Return native history, the stable summary, and the recent context.

    The recent window is newest-first and is used only to seed a summary when
    upgrading an older session and as a fallback if the durable continuity read
    fails. The caller must invoke this before appending the current user
    message; returning the window avoids a second read.
    """
    try:
        recent = await context.get_recent_context(session_id, limit=10)
    except Exception as error:
        record_failure("agent._prepare_context.recent", error)
        recent = []
    session_state = getattr(session, "state", None)
    if not isinstance(session_state, dict):
        session_state = {}
    summary = session_state.get("compaction_summary")
    if not isinstance(summary, str) or not summary.strip():
        # Seed the anchor from an existing compressor summary so upgrades from
        # the old Markdown window do not lose pre-compaction state.
        summary = None
        for entry in recent:
            if not isinstance(entry, dict) or entry.get("type") != "compression_summary":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            candidate = payload.get("content")
            if not isinstance(candidate, str) or not candidate.strip():
                checkpoint = payload.get("checkpoint")
                candidate = json.dumps(checkpoint, ensure_ascii=False) if checkpoint else None
            if isinstance(candidate, str) and candidate.strip():
                summary = candidate[-8000:] if len(candidate) > 8000 else candidate
                break

    try:
        continuity = await context.get_continuity_history(
            session_id,
            context_budget=session.context_budget,
            boundary_id=session_state.get("compaction_boundary_msg_id"),
            summary=summary,
        )
    except Exception as error:
        # Prompt continuity is additive; a context read failure must not abort
        # the live turn. The durable transcript remains intact and the next
        # turn retries the boundary read.
        record_failure("agent._prepare_context.history", error)
        continuity = None

    if continuity is not None and continuity.compacted:
        fields = {
            "compaction_boundary_msg_id": continuity.boundary_id,
            "compaction_summary": continuity.summary,
        }
        try:
            await sessions.update_state_fields(session_id, fields)
            session_state.update(fields)
        except Exception as error:
            # Keep serving the already-folded window. Retrying the durable
            # boundary update next turn is safe because the transcript is
            # authoritative.
            record_failure("agent._prepare_context.compaction_state", error)

    if continuity is not None:
        summary = continuity.summary or summary
    history_messages: list[dict[str, str]] = continuity.messages if continuity is not None else []
    if not history_messages:
        # Fallback for a failed/empty continuity read: recent context is
        # newest-first and may still carry the last conversation turns.
        for entry in reversed(recent):
            if not isinstance(entry, dict):
                continue
            chunk_type = entry.get("type")
            if chunk_type not in (
                "user_message",
                "assistant_message",
                "user",
                "assistant",
            ):
                continue
            payload_entry = entry.get("payload")
            content = payload_entry.get("content") if isinstance(payload_entry, dict) else None
            if isinstance(content, str) and content.strip():
                history_messages.append(
                    {
                        "role": ("user" if chunk_type in ("user_message", "user") else "assistant"),
                        "content": content,
                    }
                )
    return history_messages, summary, recent


def make_dynamic_assembler(
    session: Any, tool_defs: list[Any], provider: Any, visual_tokens: int
) -> PromptAssembler:
    """Build the bounded per-step assembler for tool activity and retrieval."""
    budget = max(
        1500,
        min(
            8000,
            (session.context_budget // 4)
            - _request_context_tokens([], tool_defs, provider)
            - visual_tokens,
        ),
    )
    return PromptAssembler(budget)


def build_context_messages(
    system_prompt: str,
    summary: str | None,
    history: list[dict[str, str]],
    assembler: PromptAssembler,
    goal: str | None,
    recent: list[dict[str, Any]],
    retrieved_chunks: list[tuple],
    query: str,
    omitted_evidence: int,
    images: list[dict[str, str]],
    context_budget: int,
) -> list[dict[str, Any]]:
    """Build the native message list with a bounded dynamic tail."""
    system_prompt = truncate_to_tokens(system_prompt, max(1024, min(8000, context_budget // 3)))
    if summary:
        summary = truncate_to_tokens(summary, min(1024, max(64, context_budget // 8)))
    if (
        query.strip()
        and history
        and history[-1].get("role") == "user"
        and str(history[-1].get("content", "")).strip() == query.strip()
    ):
        # A crashed prior attempt may have persisted the live user message
        # before the provider call. Keep the tail as the single live copy.
        history = history[:-1]
    # Strict alternating-role providers require a clean user start. A bounded
    # recent fallback can begin mid-turn; drop leading assistant rows.
    while history and history[0].get("role") != "user":
        history = history[1:]
    recent_for_tail = [
        entry
        for entry in recent
        if isinstance(entry, dict) and entry.get("type") != "compression_summary"
    ]
    dynamic_tail = assemble_turn_prompt(
        assembler,
        "",
        goal,
        recent_for_tail,
        retrieved_chunks,
        query,
        omitted_evidence=omitted_evidence,
        skip_conversation=True,
    )
    return build_native_turn_messages(system_prompt, summary, history, dynamic_tail, images)


async def finish_usage(
    reservation: uuid.UUID | None,
    usage: dict[str, Any] | None = None,
    *,
    failed: bool = False,
    model: str | None = None,
) -> None:
    """Settle one reservation; accounting failures surface as DatabaseError.

    Deferred imports keep the module's leaf position (``usage`` is not
    otherwise a dependency here).
    """
    from machina.core.exceptions import DatabaseError
    from machina.core.usage import usage_store

    try:
        await usage_store.finish(reservation, usage, failed=failed, model=model)
    except Exception as error:
        raise DatabaseError("usage accounting unavailable") from error
