"""Answer, repair-nudge, completion, and cancellation phases of a turn.

These decide how a turn ends: nudging an unfinished repair, recovering an
empty final response, persisting the assistant message, and flushing streamed
text on cancellation. Moving them out keeps the agent class as wiring while
preserving the exact order the loop depends on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from machina.core.agent_support import assistant_chunk as _assistant_chunk
from machina.core.assembler import get_token_count
from machina.core.context import context_manager
from machina.core.models import AgentResponse, LLMResponse
from machina.core.provider import audit_log
from machina.core.repair_gate import (
    REPAIR_NUDGE,
    VERIFICATION_NUDGE,
    should_nudge_repair,
    should_nudge_verification,
)
from machina.core.session import session_manager
from machina.core.tool_views import mark_turn_accepted
from machina.core.usage import usage_store

logger = logging.getLogger(__name__)

__all__ = ["TurnPhases"]


class TurnPhases:
    def __init__(self, agent: Any) -> None:
        self.agent = agent

    async def append_repair_nudge(
        self,
        *,
        session_id: uuid.UUID,
        content: str,
        text: str,
        messages: list[dict[str, Any]],
        pending_chunks: list[dict[str, Any]],
        event: str,
    ) -> None:
        """Persist a repaired-turn continuation before exposing it to the model."""
        await self.agent._append_assistant_content(session_id, content, pending_chunks)
        pending_chunks.append(
            {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "agent_id": self.agent.agent_id,
                "chunk_type": "user_message",
                "payload": {"content": text},
                "token_count": get_token_count(text),
            }
        )
        await self.agent._flush_pending_chunks(pending_chunks)
        messages.append({"role": "user", "content": text})
        audit_log(event, session_id=str(session_id), agent_id=self.agent.agent_id)

    async def append_task_run_nudge(
        self,
        *,
        session_id: uuid.UUID,
        text: str,
        messages: list[dict[str, Any]],
        pending_chunks: list[dict[str, Any]],
        event: str = "agent_task_run_nudge",
    ) -> None:
        """Persist one controller nudge before the next model call."""
        pending_chunks.append(
            {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "agent_id": self.agent.agent_id,
                "chunk_type": "user_message",
                "payload": {"content": text},
                "token_count": get_token_count(text),
            }
        )
        await self.agent._flush_pending_chunks(pending_chunks)
        messages.append({"role": "user", "content": text})
        audit_log(event, session_id=str(session_id), agent_id=self.agent.agent_id)

    async def maybe_continue_unchanged_repair(
        self,
        *,
        session_id: uuid.UUID,
        user_message: str,
        content: str,
        messages: list[dict[str, Any]],
        pending_chunks: list[dict[str, Any]],
        nudges: int,
    ) -> bool:
        """Give one continuation when a repair task finishes with a clean tree."""
        from machina.core.execution_context import resolve_execution_context

        try:
            workspace = (await resolve_execution_context(str(session_id), "agent")).workspace
        except Exception:
            workspace = None
        if not await asyncio.to_thread(
            should_nudge_repair,
            user_message,
            workspace=workspace,
            nudges=nudges,
        ):
            return False
        await self.append_repair_nudge(
            session_id=session_id,
            content=content,
            text=REPAIR_NUDGE,
            messages=messages,
            pending_chunks=pending_chunks,
            event="agent_repair_nudge",
        )
        return True

    async def maybe_continue_unverified_repair(
        self,
        *,
        session_id: uuid.UUID,
        user_message: str,
        content: str,
        messages: list[dict[str, Any]],
        pending_chunks: list[dict[str, Any]],
        tool_calls_made: list[dict[str, Any]],
        nudges: int,
    ) -> bool:
        """Give one continuation when a changed tree has no verification evidence."""
        if not await asyncio.to_thread(
            should_nudge_verification,
            user_message,
            tool_calls_made,
            nudges=nudges,
        ):
            return False
        await self.append_repair_nudge(
            session_id=session_id,
            content=content,
            text=VERIFICATION_NUDGE,
            messages=messages,
            pending_chunks=pending_chunks,
            event="agent_verification_nudge",
        )
        return True

    @staticmethod
    def fallback_final_content(tool_calls_made: list[dict[str, Any]]) -> str:
        names = []
        for entry in tool_calls_made:
            name = str(entry.get("tool") or "")
            if name and name not in names:
                names.append(name)
        label = ", ".join(names[:6]) or "tool"
        return (
            f"Completed {len(tool_calls_made)} tool call(s) using {label}. "
            "Review the tool results above for the exact output."
        )

    async def continue_after_empty_tool_turn(
        self,
        session_id: uuid.UUID,
        user_message: str,
        tool_calls_made: list[dict[str, Any]],
        tools: list[Any],
    ) -> tuple[LLMResponse | None, list[dict[str, Any]], int]:
        """Restart a provider's empty post-tool turn with a fresh continuation.

        The model can often continue from a compact task + tool-result summary
        even when replaying the full tool transcript produces an empty message.
        Keep the tool schema available so the model can make the next call;
        the caller decides whether to execute it or accept final content.
        """
        result_lines = []
        for entry in tool_calls_made[-8:]:
            tool = str(entry.get("tool") or "tool")
            args = entry.get("args") or {}
            try:
                args_text = json.dumps(args, ensure_ascii=False, default=str)[:240]
            except Exception:
                args_text = "{}"
            preview = " ".join(str(entry.get("result_preview") or "").split())[:400]
            result_lines.append(f"- {tool}({args_text}): {preview or '(completed)'}")
        recovery_messages = [
            {
                "role": "system",
                "content": (
                    "Continue the task after completed tool work. Use the provided "
                    "tools if more work remains; otherwise write the final response. "
                    "Do not repeat a tool action that already succeeded. Do not page "
                    "or search an artifact for a result already shown; act on its "
                    "failure details. If web verification failed, fix the failed "
                    "checks and rerun web_verify."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"The task was:\n{user_message[:4000]}\n\n"
                    "Tool results so far:\n" + "\n".join(result_lines) + "\n\nContinue the task."
                ),
            },
        ]
        try:
            response = await self.agent._call_llm_with_retry(
                recovery_messages, tools=tools, session_id=session_id
            )
            tokens = int((response.usage or {}).get("total_tokens", 0) or 0)
            return response, recovery_messages, tokens
        except Exception as exc:
            logger.warning("Empty-tool continuation recovery failed: %s", exc)
            return None, recovery_messages, 0

    async def recover_empty_final(
        self,
        session_id: uuid.UUID,
        user_message: str,
        tool_calls_made: list[dict[str, Any]],
    ) -> tuple[str, int]:
        """Recover a provider's empty post-tool completion with a fresh summary.

        Some OpenAI-compatible model routes return an empty assistant message
        after a tool result even when the tool ran successfully. Replaying the
        full tool transcript can reproduce the empty response, so ask for one
        bounded, tool-free summary from a fresh context and fall back to a
        deterministic report if the route still returns nothing.
        """
        result_lines = []
        for entry in tool_calls_made[-8:]:
            tool = str(entry.get("tool") or "tool")
            preview = " ".join(str(entry.get("result_preview") or "").split())[:300]
            result_lines.append(f"- {tool}: {preview or '(completed)'}")
        if tool_calls_made:
            recovery_messages = [
                {
                    "role": "system",
                    "content": (
                        "Write the final response after completed tool work. "
                        "Do not call tools. Be concise and factual."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"The task was:\n{user_message[:4000]}\n\n"
                        "The tool results were:\n"
                        + "\n".join(result_lines)
                        + "\n\nWrite a concise final response summarizing what was "
                        "completed, any files changed, and verification. "
                        "Do not call tools."
                    ),
                },
            ]
        else:
            recovery_messages = [
                {
                    "role": "system",
                    "content": (
                        "The previous model completion was empty. Answer the user's "
                        "task directly. Do not call tools."
                    ),
                },
                {
                    "role": "user",
                    "content": user_message[:8000],
                },
            ]
        recovered = ""
        tokens = 0
        try:
            response = await self.agent._call_llm_with_retry(
                recovery_messages, tools=[], session_id=session_id
            )
            recovered = (response.content or "").strip()
            tokens = int((response.usage or {}).get("total_tokens", 0) or 0)
        except Exception as exc:
            logger.warning("Empty-final summary recovery failed: %s", exc)
        used_fallback = not recovered
        if used_fallback and tool_calls_made:
            recovered = self.fallback_final_content(tool_calls_made)
        audit_log(
            "agent_empty_final_recovery",
            session_id=str(session_id),
            recovered=bool(recovered and not used_fallback),
            fallback=used_fallback,
            tokens=tokens,
        )
        return recovered, tokens

    async def finish_turn_completion(
        self,
        session_id: uuid.UUID,
        user_message: str,
        content: str,
        tool_calls_made: list[dict[str, Any]],
        total_tokens: int,
        iterations: int,
        pending_chunks: list[dict[str, Any]],
        *,
        outcome: str = "completed",
    ) -> AgentResponse:
        """Persist and attribute one accepted, tool-free completion.

        ``run`` and ``run_stream`` shared this whole block; only the terminal
        event shape differs at the call sites.
        """
        pending_chunks.append(_assistant_chunk(session_id, self.agent.agent_id, content))
        await self.agent._flush_pending_chunks(pending_chunks)
        await session_manager.update_activity(session_id)
        audit_log(
            "agent_run_complete",
            session_id=str(session_id),
            iterations=iterations,
            total_tokens=total_tokens,
            tool_calls_count=len(tool_calls_made),
        )
        self.agent._schedule_memory_consolidation(session_id)
        completed = AgentResponse(
            content=content,
            tool_calls=tool_calls_made,
            tokens_used=total_tokens,
            iterations=iterations,
            outcome=outcome,
        )
        self.agent._schedule_learning_review(session_id, user_message, completed)
        if outcome == "completed":
            # T0: attribute the accepted completion for cost views. A paused
            # turn is not accepted until the approval is resolved.
            await mark_turn_accepted(session_id, self.agent.agent_id, usage_store)
        return completed

    async def append_assistant_content(
        self,
        session_id: uuid.UUID,
        content: str,
        pending_chunks: list[dict[str, Any]],
    ) -> None:
        """Queue one non-empty assistant message and flush it safely."""
        if not content:
            return
        pending_chunks.append(_assistant_chunk(session_id, self.agent.agent_id, content))
        try:
            await self.agent._flush_pending_chunks(pending_chunks)
        except asyncio.CancelledError:
            await self.flush_pending_cancellation_safe(pending_chunks, session_id)
            raise

    async def flush_pending_cancellation_safe(
        self,
        pending_chunks: list[dict[str, Any]],
        session_id: uuid.UUID,
        note: str = "interrupted",
    ) -> None:
        """Flush pending history on cancellation without losing writes (AH-015).

        Uses a shielded, bounded flush so CancelledError during a turn does
        not drop the user message or completed tool records. The pending list
        is cleared only on a CONFIRMED flush: a failed flush keeps the chunks
        retained instead of clearing a batch it never persisted (code_review
        #6). Appends an explicit interruption marker instead of misrepresenting
        partial effects as complete.
        """
        from machina.core.background_tasks import await_joined

        await await_joined(self.flush_pending_cancellation_joined(pending_chunks, session_id, note))

    async def flush_pending_cancellation_joined(self, pending_chunks, session_id, note) -> None:
        if pending_chunks:
            try:
                flushed = await asyncio.wait_for(
                    self.agent._flush_pending_chunks(pending_chunks), timeout=10.0
                )
                if not flushed:
                    logger.warning(
                        "Cancellation-safe flush did not persist %d pending chunk(s); "
                        "chunks retained",
                        len(pending_chunks),
                    )
            except Exception as e:
                logger.warning("Cancellation-safe flush failed: %s", e)
        try:
            await asyncio.wait_for(
                context_manager.add_chunk(
                    session_id=session_id,
                    agent_id=self.agent.agent_id,
                    chunk_type="assistant_message",
                    payload={
                        "content": f"[{note}] Turn interrupted; partial effects above are retained."
                    },
                    token_count=20,
                ),
                timeout=10.0,
            )
        except Exception as e:
            logger.warning("Could not record interruption marker: %s", e)
