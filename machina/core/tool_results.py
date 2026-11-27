"""Record tool results, artifact views, and approval retirement."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from machina.core.agent_support import tool_outcome as _tool_outcome
from machina.core.assembler import get_token_count
from machina.core.context import context_manager
from machina.core.models import LLMResponse
from machina.core.prompt import append_tool_messages
from machina.core.tool_views import ensure_item_artifact_view
from machina.observability.diagnostics import record_failure

logger = logging.getLogger(__name__)

# Retained evidence is separate from short previews assembled into prompts.
_STORED_TOOL_RESULT_CHARS = 65536

__all__ = ["ToolResultRecorder"]


class ToolResultRecorder:
    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def append_tool_messages(
        self,
        messages: list[dict[str, Any]],
        response: LLMResponse,
        tc: dict[str, Any],
        content: str,
    ) -> None:
        """Append one assistant batch followed by each of its tool results.

        Thin wrapper over :func:`machina.core.prompt.append_tool_messages`
        (the moved implementation) so the method seam stays on the agent.
        """
        append_tool_messages(messages, response, tc, content)

    def tool_entry(self, item: dict[str, Any]) -> dict[str, Any]:
        """Build the ``tool_calls_made`` entry for one finished tool action."""
        result_str = item["result"]
        entry: dict[str, Any] = {
            "tool": item["name"],
            "args": item["args"],
            "result_preview": result_str[:200],
            "outcome": _tool_outcome(item),
        }
        returncode = getattr(result_str, "returncode", None)
        if isinstance(returncode, int):
            entry["returncode"] = returncode
        # AH-AUDIT-022: typed pause marker travels with the entry so
        # schedulers/transports never parse prose to detect pauses.
        if item.get("_needs_approval"):
            entry["needs_approval"] = item["_needs_approval"]
        if item.get("_needs_approvals"):
            entry["needs_approvals"] = item["_needs_approvals"]
            entry["needs_approval"] = item["_needs_approvals"][0]
        if item.get("_delegated_tokens"):
            entry["delegated_tokens"] = item["_delegated_tokens"]
        return entry

    async def record_action(
        self,
        item: dict[str, Any],
        session_id: uuid.UUID,
        pending_chunks: list[dict[str, Any]] | None,
        tool_calls_made: list[dict[str, Any]],
    ) -> None:
        """Durably record one finished action exactly once (F02).

        Writes the ``tool_call`` context chunk, retires the action's approval
        (claimed → completed/failed), and appends its ``tool_calls_made`` entry.
        Called from Phase D in provider order on the normal path and from
        ``_finalize_completed_tools`` for completed-but-unrecorded items when a
        batch is cancelled. Provider-message assembly stays with the caller
        (order-sensitive). Idempotent via ``item['_recorded']``.
        """
        from machina.core.background_tasks import await_joined

        await await_joined(
            self.record_action_joined(item, session_id, pending_chunks, tool_calls_made)
        )

    async def ensure_artifact_view(self, item: dict[str, Any], session_id: uuid.UUID) -> str:
        """Retain the original once; cache view + handle before recording (MCH-010)."""
        return await ensure_item_artifact_view(
            item, session_id, self.agent.agent_id, status=_tool_outcome(item)
        )

    async def record_action_joined(self, item, session_id, pending_chunks, tool_calls_made) -> None:
        result_str = item["result"]
        outcome = (
            "failed" if item.get("_failed") or result_str.startswith("Error:") else "completed"
        )
        await self.retire_approval(item, outcome)
        if item.get("_recorded"):
            return
        # MCH-010: retention/handle must exist BEFORE the payload is published.
        # Cancellation finalization and Phase D share this single decision, so
        # a completed oversized execution never persists a handle-less record.
        if item.get("_artifact_view") is None and "elapsed_ms" in item:
            await self.ensure_artifact_view(item, session_id)
        payload = {
            "tool": item["name"],
            "args": item["args"],
            "result_preview": result_str[:500],
            "result": result_str[:_STORED_TOOL_RESULT_CHARS],
            "result_truncated": len(result_str) > _STORED_TOOL_RESULT_CHARS,
        }
        # When the full result was retained as an artifact, the durable chunk
        # points at it so later sessions/turns can recover the exact original
        # beyond the chunk copy's 65k cap.
        if item.get("_artifact_handle"):
            payload["artifact_handle"] = item["_artifact_handle"]
        if pending_chunks is not None:
            pending_chunks.append(
                {
                    "id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "agent_id": self.agent.agent_id,
                    "chunk_type": "tool_call",
                    "payload": payload,
                    "token_count": get_token_count(result_str),
                }
            )
        else:
            await context_manager.add_chunk(
                session_id=session_id,
                agent_id=self.agent.agent_id,
                chunk_type="tool_call",
                payload=payload,
                token_count=get_token_count(result_str),
            )
        tool_calls_made.append(self.tool_entry(item))
        item["_recorded"] = True

    async def retire_approval(self, item, outcome: str) -> None:
        approval_id = item.get("_approval_id", "")
        if not approval_id or item.get("_approval_done"):
            return
        try:
            from machina.permissions.broker import permission_broker

            if await permission_broker.complete(approval_id, outcome):
                item["_approval_done"] = True
            else:
                logger.warning("Tool approval retirement was not confirmed")
        except Exception as error:
            record_failure("agent._retire_tool_approval", error)

    async def finalize_completed(
        self,
        planned: list[dict[str, Any]],
        session_id: uuid.UUID,
        pending_chunks: list[dict[str, Any]] | None,
        tool_calls_made: list[dict[str, Any]],
    ) -> None:
        """Record completed actions and retry unfinished approval retirements."""
        from machina.core.background_tasks import await_joined

        await await_joined(
            self.finalize_completed_joined(planned, session_id, pending_chunks, tool_calls_made)
        )

    async def finalize_completed_joined(
        self, planned, session_id, pending_chunks, tool_calls_made
    ) -> None:
        for item in planned:
            if item["name"] is None:
                continue
            if item.get("_recorded") and (
                not item.get("_approval_id") or item.get("_approval_done")
            ):
                continue
            try:
                if item.get("_cancelled"):
                    await self.retire_approval(item, "cancelled")
                elif item.get("_denied") or "elapsed_ms" in item:
                    # Denied items were announced to the client in Phase B but
                    # only recorded by Phase D's shared tail; a batch cancelled
                    # before Phase D reached them must still land their chunk
                    # and tool_calls_made entry.
                    await self.agent._record_tool_action(
                        item, session_id, pending_chunks, tool_calls_made
                    )
            except Exception as exc:
                logger.warning(
                    "cancellation finalize failed for %s: %r", item["name"], exc, exc_info=True
                )
