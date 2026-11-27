"""Post-turn maintenance: memory consolidation, cursor, and learning review.

These tasks are deliberately off the critical path: the turn terminal is
already decided when they run. The module owns the watermarked cursor and the
process-wide single-flight registry so repeated turns stay idempotent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from machina.core.agent_support import notify_growth
from machina.core.background_tasks import (
    consolidation_inflight,
    pending_learning_reviews,
)
from machina.core.config import config
from machina.core.context import context_manager
from machina.core.models import AgentResponse
from machina.core.provider import LLMProvider
from machina.core.session import session_manager
from machina.memory.consolidator import MemoryConsolidator
from machina.observability.diagnostics import record_failure

logger = logging.getLogger(__name__)

MAX_PENDING_LEARNING_REVIEWS = 16

__all__ = ["PostTurn", "MAX_PENDING_LEARNING_REVIEWS"]


class PostTurn:
    """Bound to one agent identity and its task sets."""

    def __init__(
        self,
        *,
        agent_id: str,
        consolidator: MemoryConsolidator | None,
        provider: LLMProvider,
        delegated: bool,
        consolidation_tasks: set[asyncio.Task],
        learning_tasks: set[asyncio.Task],
    ) -> None:
        self.agent_id = agent_id
        self.memory_consolidator = consolidator
        self.provider = provider
        self.delegated = delegated
        self._consolidation_tasks = consolidation_tasks
        self._learning_tasks = learning_tasks

    def schedule_consolidation(self, session_id: uuid.UUID) -> None:
        """Schedule memory consolidation as a bounded background task.

        Bounded and shared: one consolidation per SESSION across all agent
        instances (LP-14); skipped when memory or consolidation is disabled.
        The watermark cursor keeps repeated runs idempotent.
        """
        if not self.memory_consolidator:
            return
        try:
            if not config.get("memory_consolidation_enabled"):
                return
        except Exception as _boundary_error:
            record_failure("agent._schedule_memory_consolidation", _boundary_error)
        key = str(session_id)
        running = consolidation_inflight.get(key)
        if running is not None and not running.done():
            logger.debug("consolidation already running for session, skipping duplicate")
            return
        task = asyncio.create_task(self.consolidate(session_id))
        consolidation_inflight[key] = task
        self._consolidation_tasks.add(task)
        task.add_done_callback(self._consolidation_tasks.discard)

        def _drop(_t: asyncio.Task) -> None:
            if consolidation_inflight.get(key) is _t:
                consolidation_inflight.pop(key, None)

        task.add_done_callback(_drop)

    def parse_cursor(self, cursor: dict | None) -> tuple | None:
        """Parse a stored ``mem_consolidated_up_to`` dict into a cursor tuple."""
        if not (cursor and cursor.get("id")):
            return None
        try:
            since_time = cursor.get("at")
            if isinstance(since_time, str):
                text = since_time[:-1] + "+00:00" if since_time.endswith("Z") else since_time
                since_time = datetime.fromisoformat(text)
                if since_time.tzinfo is None:
                    since_time = since_time.replace(tzinfo=UTC)
            since_id = uuid.UUID(str(cursor["id"]))
            since_seq = cursor.get("seq")
            if since_seq is None:
                return (since_time, since_id)
            return (since_time, int(since_seq), since_id)
        except Exception as exc:
            record_failure("agent.consolidation_cursor", exc)
            return None

    async def read_cursor(self, session_id: uuid.UUID) -> tuple | None:
        """Resolve the durable watermark, preferring a fresh session read."""
        getter = getattr(session_manager, "get_fresh", None) or session_manager.get
        session = await getter(session_id)
        cursor = (session.state or {}).get("mem_consolidated_up_to") if session else None
        return self.parse_cursor(cursor)

    async def persist_cursor(self, session_id: uuid.UUID, checkpoint: dict) -> None:
        """Durably advance the watermark from a fully-processed checkpoint."""
        try:
            stored_cursor: dict = {
                "id": str(checkpoint["id"]),
                "at": checkpoint["at"],
            }
            if checkpoint.get("seq") is not None:
                stored_cursor["seq"] = int(checkpoint["seq"])
            await session_manager.update_state_fields(
                session_id,
                {"mem_consolidated_up_to": stored_cursor},
            )
        except Exception as exc:
            logger.debug("consolidation cursor persist failed: %s", exc)

    async def consolidate(self, session_id: uuid.UUID) -> None:
        """Consolidate new session context into long-term memories."""
        consolidator = self.memory_consolidator
        if consolidator is None:
            return
        try:
            if isinstance(consolidator, MemoryConsolidator):
                result = await consolidator.consolidate_session(
                    session_id=session_id,
                    agent_id=self.agent_id,
                    cursor_reader=lambda: self.read_cursor(session_id),
                    checkpoint_writer=lambda checkpoint: self.persist_cursor(
                        session_id, checkpoint
                    ),
                )
            else:
                session = await session_manager.get(session_id)
                cursor = (session.state or {}).get("mem_consolidated_up_to") if session else None
                since = self.parse_cursor(cursor)
                try:
                    newest = await context_manager.get_chunks(
                        session_id, limit=1, include_embedding=False
                    )
                except Exception as exc:
                    record_failure("agent.consolidation_cursor", exc)
                    newest = []
                if since is not None:
                    try:
                        pending_kwargs: dict = {
                            "since_time": since[0],
                            "since_id": since[-1],
                            "limit": 1,
                        }
                        if len(since) >= 3 and since[1] is not None:
                            pending_kwargs["since_seq"] = since[1]
                        pending = await context_manager.get_chunks_since(
                            session_id,
                            **pending_kwargs,
                        )
                        if not pending:
                            logger.debug("consolidation cursor unchanged, skipping")
                            return
                    except Exception as _boundary_error:
                        record_failure("agent._consolidate_memories", _boundary_error)
                elif cursor and newest and str(newest[0].id) == str(cursor.get("id")):
                    logger.debug("consolidation cursor unchanged, skipping")
                    return
                result = await consolidator.consolidate_session(
                    session_id=session_id,
                    agent_id=self.agent_id,
                    since=since,
                )
                checkpoint = getattr(result, "checkpoint", None)
                if checkpoint:
                    await self.persist_cursor(session_id, checkpoint)
            new_memories = result.entries if hasattr(result, "entries") else (result or [])
            if new_memories:
                logger.info(
                    "Consolidated %d new memories for session %s",
                    len(new_memories),
                    session_id,
                )
                notify_growth(session_id, "consolidation_done", f"{len(new_memories)} new memories")
        except Exception as exc:
            logger.warning("Memory consolidation failed for session %s: %s", session_id, exc)

    def schedule_learning_review(
        self, session_id: uuid.UUID, user_message: str, response: AgentResponse
    ) -> None:
        """Review successful turns in the background when explicitly enabled."""
        if self.delegated:
            return
        if not config.get("learning_review_enabled"):
            return
        if len(pending_learning_reviews) >= MAX_PENDING_LEARNING_REVIEWS:
            logger.warning("Skipping learning review: process-wide review limit reached")
            return
        task = asyncio.create_task(self.review_learning(session_id, user_message, response))
        self._learning_tasks.add(task)
        pending_learning_reviews.add(task)
        task.add_done_callback(self._learning_tasks.discard)
        task.add_done_callback(pending_learning_reviews.discard)

    async def review_learning(
        self, session_id: uuid.UUID, user_message: str, response: AgentResponse
    ) -> None:
        from machina.skills.learning import learning_reviewer

        try:
            staged = await learning_reviewer.review_turn(
                session_id,
                self.agent_id,
                user_message,
                response.content,
                self.provider,
                tool_calls=response.tool_calls,
            )
            if staged:
                if staged.get("autoApplied") and staged.get("status") == "approved":
                    notify_growth(
                        session_id, "review_done", f"applied learned skill {staged.get('name')}"
                    )
                else:
                    notify_growth(session_id, "review_done", "suggested a learning skill")
        except Exception as exc:
            logger.warning("Learning review failed for session %s: %s", session_id, exc)
