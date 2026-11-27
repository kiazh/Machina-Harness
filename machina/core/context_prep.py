"""Turn context preparation: session, memory/RAG evidence, and prompt assembly."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol

from machina.core.assembler import get_token_count
from machina.core.config import config
from machina.core.context import context_manager
from machina.core.continuity import (
    build_context_messages,
    make_dynamic_assembler,
    prepare_continuity,
)
from machina.core.exceptions import SessionNotFoundError
from machina.core.images import IMAGE_TOKEN_RESERVE, validate_images
from machina.core.projection import project_retrieved
from machina.core.prompt import (
    _effective_turn_budget,
    _turn_model,
    build_project_instruction_section,
    build_system_prompt,
    swe_repair_protocol,
)
from machina.core.provider import LLMProvider, audit_log
from machina.core.rsi_context import growth_episode as _growth_episode
from machina.core.rsi_context import rsi_choice as _rsi_choice
from machina.core.rsi_context import rsi_skills as _rsi_skills
from machina.core.session import Session, session_manager
from machina.observability.diagnostics import record_failure
from machina.plugins.registry import plugin_registry
from machina.skills import query_expansion

logger = logging.getLogger(__name__)

__all__ = ["ContextHost", "ContextPreparer"]


class ContextHost(Protocol):
    """The narrow agent surface context preparation reads and updates."""

    agent_id: str
    provider: LLMProvider
    delegated: bool
    allowed_tools: list[str] | None
    soul_spec: Any
    system_prompt: str
    dynamic_tools: bool
    memory_retriever: Any
    rag_pipeline: Any
    _last_triage: str | None
    _last_meta_route: Any
    _last_rag_mode: str

    def _tool_defs(self) -> list[Any]: ...


class ContextPreparer:
    """Prepare one turn's session, evidence, and native message list."""

    def __init__(self, host: ContextHost) -> None:
        self.host = host

    async def prepare(
        self,
        session_id: uuid.UUID,
        user_message: str,
        include_memory: bool = True,
        pending_chunks: list[dict[str, Any]] | None = None,
        images: list[dict[str, str]] | None = None,
    ) -> tuple[Session, list[dict[str, Any]]]:
        """Prepare session, context, and prompt messages.

        Handles session retrieval, user message storage, recent context retrieval,
        memory retrieval (optional), RAG retrieval, and prompt assembly.

        Args:
            session_id: The session ID.
            user_message: The user's message.
            include_memory: Whether to include memory retrieval. Defaults to True.
            pending_chunks: If provided, chunks are accumulated here instead of
                being inserted immediately. The caller is responsible for flushing.

        Returns:
            A tuple of (Session, messages list).

        Raises:
            ValueError: If the session is not found.
        """
        if getattr(self, "dynamic_tools", False):
            from machina.core.tool_projection import reset_dynamic_tool_state

            reset_dynamic_tool_state()
        images = validate_images(images or [])
        submitted_visual_tokens = len(images) * IMAGE_TOKEN_RESERVE
        payload: dict[str, Any] = {"content": user_message}
        if images:
            payload["images"] = images
        session = await session_manager.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        if session.agent_id != self.host.agent_id:
            raise PermissionError("Session does not belong to the running agent")
        resolver = getattr(type(self.host.provider), "context_window", None)
        turn_model = _turn_model(session, self.host.provider)
        if resolver is not None:
            session.context_window = await resolver(self.host.provider, turn_model)
        else:
            session.context_window = _effective_turn_budget(session, self.host.provider)
        session.context_window_model = turn_model
        session.context_budget = session.context_window
        _growth_episode.set(None)
        _rsi_choice.set(None)
        _rsi_skills.set([])

        await plugin_registry.dispatch("pre_agent_run", session, user_message)

        if not images:
            images = await context_manager.get_latest_images(session_id, self.host.agent_id)
        visual_tokens = len(images) * IMAGE_TOKEN_RESERVE

        # The dynamic tail is bounded because active history is native; it
        # carries only hot tool activity, retrieved context, goal, and query.
        assembler = make_dynamic_assembler(
            session, self.host._tool_defs(), self.host.provider, visual_tokens
        )

        # Fetch continuity before appending the live user message, so the
        # current query is rendered once rather than duplicated in history.
        history_messages, summary, recent = await prepare_continuity(
            session_id,
            session,
            context=context_manager,
            sessions=session_manager,
        )
        if pending_chunks is not None:
            pending_chunks.append(
                {
                    "id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "agent_id": self.host.agent_id,
                    "chunk_type": "user_message",
                    "payload": payload,
                    "token_count": get_token_count(user_message) + submitted_visual_tokens,
                }
            )
        else:
            await context_manager.add_chunk(
                session_id=session_id,
                agent_id=self.host.agent_id,
                chunk_type="user_message",
                payload=payload,
                token_count=get_token_count(user_message) + submitted_visual_tokens,
            )
        retrieved_chunks: list[tuple] = []

        # Retrieve relevant long-term memories (optional). memory_enabled
        # gates auto-retrieval; explicit remember/recall tools stay available
        # when disabled.
        memory_enabled = True
        try:
            memory_enabled = bool(config.get("memory_enabled"))
        except Exception as _boundary_error:
            # Optional fallback preserves the primary outcome; report no payload.
            record_failure("agent._prepare_context", _boundary_error)
        growth_enabled = (
            not self.host.delegated
            and include_memory
            and memory_enabled
            and config.get("personality_growth_enabled")
        )
        growth_notes = None
        growth_state = "skipped"
        retrieve_memory = True
        if growth_enabled:
            growth_state = "unavailable"
            try:
                from machina.memory.growth import growth_store

                decision = await asyncio.wait_for(
                    growth_store.begin(
                        self.host.agent_id, session_id, user_message, session.context_budget
                    ),
                    2,
                )
                growth_notes = decision.get("notes")
                if growth_notes is None:
                    growth_notes = (
                        await asyncio.wait_for(growth_store.get(self.host.agent_id), 2)
                    )["notes"]
                _growth_episode.set(decision["id"])
                _rsi_choice.set(decision.get("rsi", {}))
                growth_state = "active"
                retrieve_memory = decision["action"] == "retrieve"
            except Exception as exc:
                record_failure("agent.growth.prepare", exc)
        if include_memory and memory_enabled and retrieve_memory:
            try:
                from machina.memory.embeddings import embed_query

                query_embedding = await embed_query(user_message)
                digest = None
                digest_enabled = False
                try:
                    digest_enabled = bool(config.get("memory_digest_enabled"))
                except Exception as _boundary_error:
                    record_failure("agent.memory_digest.config", _boundary_error)
                retrieve_digest = getattr(self.host.memory_retriever, "retrieve_digest", None)
                if digest_enabled and callable(retrieve_digest):
                    try:
                        digest = await retrieve_digest(
                            query=user_message,
                            agent_id=self.host.agent_id,
                            session_id=session_id,
                            emotion=None if self.host.delegated else session.state.get("emotion"),
                            persona_enabled=False if self.host.delegated else None,
                            query_embedding=query_embedding,
                            max_items=max(1, int(config.get("memory_digest_max_facts"))),
                            max_chars=max(200, int(config.get("memory_digest_max_chars"))),
                            candidate_limit=max(1, int(config.get("memory_digest_candidates"))),
                        )
                    except Exception as exc:
                        record_failure("agent.memory_digest", exc)
                        digest = None

                from machina.core.models import ContextChunk

                if digest is not None:
                    if digest.selected:
                        chunk = ContextChunk(
                            id=uuid.uuid4(),
                            session_id=session_id,
                            agent_id=self.host.agent_id,
                            chunk_type="memory",
                            payload={
                                "content": digest.text,
                                "importance": max(
                                    item.memory.importance for item in digest.selected
                                ),
                                "category": "digest",
                                "persona_interpretation": None,
                            },
                            token_count=get_token_count(digest.text),
                        )
                        retrieved_chunks.append(
                            (chunk, max(item.score for item in digest.selected))
                        )
                        audit_log(
                            "memory_digest",
                            session_id=str(session_id),
                            candidates=len(digest.selected) + digest.omitted,
                            selected=len(digest.selected),
                            omitted=digest.omitted,
                            chars=digest.char_count,
                        )
                else:
                    retrieved_memories = await self.host.memory_retriever.retrieve(
                        query=user_message,
                        agent_id=self.host.agent_id,
                        session_id=session_id,
                        emotion=None if self.host.delegated else session.state.get("emotion"),
                        persona_enabled=False if self.host.delegated else None,
                        query_embedding=query_embedding,
                    )
                    for rm in retrieved_memories:
                        m = rm.memory
                        chunk = ContextChunk(
                            id=m.id,
                            session_id=session_id,
                            agent_id=self.host.agent_id,
                            chunk_type="memory",
                            payload={
                                "content": m.content,
                                "importance": m.importance,
                                "category": m.category,
                                "persona_interpretation": None
                                if self.host.delegated
                                else rm.persona_interpretation,
                            },
                            token_count=get_token_count(m.content),
                        )
                        retrieved_chunks.append((chunk, rm.score))
            except Exception as e:
                logger.warning("Memory retrieval failed: %s", e)

        # Retrieve RAG context if enabled AND material exists. Never scans or
        # uploads host files implicitly — only previously indexed session docs.
        rag_enabled = True
        try:
            rag_enabled = bool(config.get("rag_enabled"))
        except Exception as _boundary_error:
            # Optional fallback preserves the primary outcome; report no payload.
            record_failure("agent._prepare_context", _boundary_error)
        if rag_enabled and not self.host.delegated:
            rag_chunks = await self.get_rag_context(session_id, user_message)
        else:
            rag_chunks = []
            self.host._last_rag_mode = "disabled"
        retrieved_chunks.extend(rag_chunks)

        if not self.host.delegated:
            try:
                _archive_hits = context_manager.search_archive_text(session_id, user_message)
                retrieved_chunks.extend(await asyncio.wait_for(_archive_hits, timeout=5))
            except Exception as e:
                logger.warning("Archived context retrieval failed: %s", e)

        # T5 evidence projection (dedup + budget) with prompt disclosure.
        retrieved_chunks, omitted_evidence = project_retrieved(retrieved_chunks)

        # Prompt sections assemble in machina.core.prompt (moves-only): the
        # composition order below matches the previous inline version exactly,
        # so the assembled text is byte-identical.
        meta_route_hint: str | None = None
        if not self.host.delegated:
            try:
                if bool(config.get("meta_harness_enabled")):
                    from machina.core.meta_harness import route_prompt

                    triage = getattr(self.host, "_last_triage", None)
                    meta_route = route_prompt(
                        user_message,
                        task_state=session.state,
                        recent=recent,
                        goal=session.goal,
                        task_class=None if triage == "ambiguous" else triage,
                    )
                    self.host._last_meta_route = meta_route
                    if meta_route.mode == "external":
                        audit_log(
                            "meta_harness_route",
                            session_id=str(session_id),
                            task_class=meta_route.task_class,
                            primary=meta_route.primary,
                            confidence=round(meta_route.confidence, 3),
                            steps=len(meta_route.steps),
                        )
                    can_delegate_harness = (
                        self.host.allowed_tools is None
                        or "harness_delegate" in self.host.allowed_tools
                    )
                    if bool(config.get("meta_harness_hint")) and can_delegate_harness:
                        meta_route_hint = meta_route.prompt_hint() or None
            except Exception as _boundary_error:
                record_failure("agent.meta_harness.prepare", _boundary_error)
        if self.host.delegated:
            system_prompt = self.host.system_prompt
        else:
            await query_expansion.prepare_turn(user_message)
            _project_role, project_instructions = build_project_instruction_section(session)
            system_prompt = build_system_prompt(
                self.host.soul_spec,
                self.host.allowed_tools,
                session,
                user_message,
                recent,
                growth_notes,
                growth_state,
                _rsi_choice.get() or {},
                _rsi_skills.get(),
                {tool.name for tool in self.host._tool_defs()},
                triage=getattr(self.host, "_last_triage", None),  # advisory; None = unchanged
                meta_route=meta_route_hint,
                project_instructions=project_instructions,
                include_repair=False,
            )
        # Keep the task-critical repair protocol outside the truncatable system
        # prefix. A long persona/skill section must not silently remove it.
        required_tail = "" if self.host.delegated else swe_repair_protocol(user_message)
        messages = build_context_messages(
            system_prompt,
            summary,
            history_messages,
            assembler,
            session.goal,
            recent,
            retrieved_chunks,
            user_message,
            omitted_evidence,
            images,
            session.context_budget,
            required_tail=required_tail,
        )
        return session, messages

    async def get_rag_context(
        self,
        session_id: uuid.UUID,
        query: str,
    ) -> list[tuple]:
        """Retrieve RAG context for the query if a RAG pipeline is configured.

        Returns a list of (ContextChunk, score) tuples for prompt assembly.
        """
        if self.host.rag_pipeline is None:
            # Test-observed diagnostic: last RAG retrieval mode (no production reader).
            self.host._last_rag_mode = getattr(self, "_last_rag_mode", "disabled")
            return []

        try:
            results = await asyncio.wait_for(
                self.host.rag_pipeline.search(
                    query=query, session_id=session_id, top_k=5, rerank=True
                ),
                timeout=15,
            )
            self.host._last_rag_mode = "ready" if results else "empty-index"
            return [(r.chunk, r.score) for r in results]
        except Exception as e:
            self.host._last_rag_mode = "unavailable"
            logger.warning("RAG retrieval failed: %s", e)
            return []
