"""Durable LLM usage accounting and optional session/agent budgets.

Each attempted provider call receives a reservation before network I/O. A
transaction-scoped advisory lock serializes checks for the session and agent,
so concurrent turns cannot both spend the same remaining budget. Provider
errors and missing usage keep the conservative reservation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from machina.core.assembler import get_token_count
from machina.core.config import config
from machina.core.exceptions import DatabaseError, UsageBudgetExceededError, ValidationError
from machina.core.metrics import metrics
from machina.core.optimization.cache_context import (
    AUXILIARY_READ_ONLY_PURPOSES,
    CacheContext,
)
from machina.core.optimization.canonicalizer import canonicalize
from machina.core.optimization.compression.learned import (
    compressor_revision,
    maybe_compress_request,
)
from machina.core.optimization.policy import (
    OptimizationMode,
    effective_mode,
    evaluate_semantic_cache,
)
from machina.core.optimization.semantic_cache import SemanticCache
from machina.core.optimization.shadow import ShadowEvent, schedule_shadow
from machina.core.pricing import estimate_cost, parse_overrides
from machina.db.lazy import LazyDb, parse_command_count

logger = logging.getLogger(__name__)

db = LazyDb()


def _row_int(row: Any, key: str, default: int = 0) -> int:
    """Tolerate old mocks/rows that predate an additive ledger column."""
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError):
        return default


def provider_identity(provider: Any) -> str:
    """Attribute shared transports to their configured provider family."""
    name = getattr(getattr(provider, "spec", None), "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().lower()
    return type(provider).__name__.removesuffix("Provider").lower()


def _limits() -> dict[str, int]:
    names = (
        "usage_session_token_limit",
        "usage_session_request_limit",
        "usage_agent_token_limit",
        "usage_agent_request_limit",
    )
    limits = {name: int(config.get(name)) for name in names}
    if any(value < 0 for value in limits.values()):
        raise ValidationError("usage budgets must be non-negative")
    return limits


def _estimate_tokens(messages: list[dict[str, Any]], tools: list[Any], max_tokens: int) -> int:
    """Reserve a conservative amount when provider usage is not yet known."""
    from machina.core.images import token_messages

    messages, visual_tokens = token_messages(messages)
    payload = json.dumps({"messages": messages, "tools": tools}, default=str, ensure_ascii=False)
    try:
        prompt_estimate = get_token_count(payload)
    except ValueError:
        # tiktoken rejects certain special-token spellings by default; those
        # are valid user text and must not prevent a provider call.
        prompt_estimate = max(1, len(payload) // 3)
    return max(1, int((prompt_estimate + visual_tokens) * 1.2) + 16 + max_tokens)


def _check_limit(
    scope: str, requests: int, tokens: int, request_limit: int, token_limit: int, reservation: int
) -> None:
    if request_limit and requests + 1 > request_limit:
        raise UsageBudgetExceededError(
            f"{scope} request budget exceeded ({requests}/{request_limit})"
        )
    if token_limit and tokens + reservation > token_limit:
        raise UsageBudgetExceededError(
            f"{scope} token budget exceeded ({tokens} used, {reservation} requested, "
            f"{token_limit} limit)"
        )


def _auto_cache_context(
    provider: Any,
    session_id: uuid.UUID | None,
    agent_id: str,
    messages: list[dict[str, Any]],
    tools: list[Any] | None,
    *,
    purpose: str,
    max_tokens: int,
    temperature: float | None,
) -> Any | None:
    """Build the conservative auxiliary context only when caching is active.

    Interactive turns must pass an explicit context from the agent loop: the
    accounting seam does not know their task scope or input revision, and a
    missing guard fact must not be guessed.
    """
    try:
        if effective_mode("semantic_cache") is OptimizationMode.OFF:
            return None
        if purpose not in AUXILIARY_READ_ONLY_PURPOSES:
            return None
        context = CacheContext.for_auxiliary(
            session_id=session_id,
            agent_id=agent_id,
            provider=provider_identity(provider),
            model=str(getattr(provider, "model", "") or ""),
            purpose=purpose,
            messages=messages,
            tools=tools,
            temperature=0.0 if temperature is None else float(temperature),
            max_tokens=max_tokens,
            compressor_revision=compressor_revision(),
            workspace_root=str(config.get("workspace_root") or ""),
        )
        if bool(config.get("optimization_semantic_cache_allow_cross_session_aux")):
            context = context.stateless()
        return context
    except Exception:
        # An optimization-context failure must never prevent the call.
        return None


class UsageStore:
    async def complete_call(
        self,
        provider: Any,
        session_id: uuid.UUID | None,
        agent_id: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[Any] | None = None,
        max_tokens: int = 4096,
        purpose: str = "agent",
        **kwargs: Any,
    ) -> Any:
        """Call a provider once, accounting for auxiliary and agent requests.

        ``purpose`` labels the call's role in the task (agent loop, context
        summary, memory rerank, ...) so the ledger can attribute auxiliary
        spend. It is accounting metadata only — never sent to the provider.
        Semantic-cache context is built only for the read-only auxiliary
        allowlist; interactive turns do not enter this cache.
        """
        call_messages = messages
        canonical = None
        semantic_cache = None
        semantic_eligibility = None
        cache_context = _auto_cache_context(
            provider,
            session_id,
            agent_id,
            messages,
            tools,
            purpose=purpose,
            max_tokens=max_tokens,
            # The provider defaults to 0.7 when no temperature is passed;
            # a context that assumed 0.0 would make the cache ineligible
            # facts disagree with the wire request.
            temperature=kwargs.get("temperature", 0.7),
        )

        if cache_context is not None:
            try:
                canonical = canonicalize(messages, tools)
                semantic_eligibility = evaluate_semantic_cache(
                    cache_context, canonical, tools_present=bool(tools)
                )
                if semantic_eligibility.mode is OptimizationMode.OFF:
                    semantic_cache = None
                elif semantic_eligibility.should_compute:
                    semantic_cache = SemanticCache()
                    started = time.monotonic()
                    hit = await asyncio.wait_for(
                        semantic_cache.lookup(
                            cache_context,
                            canonical,
                            threshold=float(
                                config.get("optimization_semantic_cache_similarity_threshold")
                            ),
                            record_hit=semantic_eligibility.should_serve,
                        ),
                        timeout=2.0,
                    )
                    latency_ms = (time.monotonic() - started) * 1000
                    schedule_shadow(
                        ShadowEvent(
                            subsystem="semantic_cache",
                            decision=("would_hit" if hit else "miss")
                            if not semantic_eligibility.should_serve
                            else ("hit" if hit else "miss"),
                            reason_codes=(
                                ("similarity",)
                                if hit
                                else tuple(str(reason) for reason in semantic_eligibility.reasons)
                            ),
                            mode=str(semantic_eligibility.mode),
                            session_id=cache_context.session_id,
                            agent_id=cache_context.agent_id,
                            provider=cache_context.provider,
                            model=cache_context.model,
                            task_class=cache_context.task_class,
                            request_hash=canonical.request_hash,
                            similarity=hit.similarity if hit else None,
                            latency_ms=latency_ms,
                            tokens_before=max(1, canonical.char_count // 4),
                            tokens_after=0 if hit else max(1, canonical.char_count // 4),
                        )
                    )
                    if hit and semantic_eligibility.should_serve:
                        return hit.response
                else:
                    schedule_shadow(
                        ShadowEvent(
                            subsystem="semantic_cache",
                            decision="skipped",
                            reason_codes=tuple(
                                str(reason) for reason in semantic_eligibility.reasons
                            ),
                            mode=str(semantic_eligibility.mode),
                            session_id=cache_context.session_id,
                            agent_id=cache_context.agent_id,
                            provider=cache_context.provider,
                            model=cache_context.model,
                            task_class=cache_context.task_class,
                            request_hash=canonical.request_hash,
                        )
                    )
            except Exception:
                # Optimization is additive. Any failure falls back to the
                # unoptimized provider call.
                semantic_cache = None
                semantic_eligibility = None

        try:
            if effective_mode("compression") is not OptimizationMode.OFF:
                if canonical is None:
                    canonical = canonicalize(messages, tools)
                compression = await maybe_compress_request(
                    messages,
                    tools,
                    provider=provider,
                    model=str(getattr(provider, "model", "") or ""),
                    session_id=session_id,
                    agent_id=agent_id,
                    request_hash=canonical.request_hash if canonical else "",
                    task_class=getattr(cache_context, "task_class", "") if cache_context else "",
                )
                if compression.applied:
                    call_messages = list(compression.messages)
        except Exception:
            call_messages = messages

        reservation = None
        if session_id is not None:
            model = str(getattr(provider, "model", "unknown"))
            provider_name = provider_identity(provider)
            reservation_max_tokens = max_tokens
            reservation_limit = getattr(type(provider), "reservation_max_tokens", None)
            if callable(reservation_limit):
                reservation_max_tokens = reservation_limit(
                    provider, max_tokens, kwargs.get("reasoning_effort")
                )
            try:
                reservation = await self.reserve(
                    session_id,
                    agent_id,
                    provider_name,
                    model,
                    call_messages,
                    tools or [],
                    reservation_max_tokens,
                    purpose=purpose,
                )
            except UsageBudgetExceededError:
                raise
            except Exception as e:
                raise DatabaseError("usage accounting unavailable") from e
        call_kwargs = {
            "messages": call_messages,
            "tools": tools or [],
            "session_id": session_id,
            "agent_id": agent_id,
            **kwargs,
        }
        if getattr(provider, "owns_usage_reservation", False):
            # This wrapper owns the single outer reservation; self-accounting
            # providers must only settle it, never create a second row.
            call_kwargs["usage_reserved"] = session_id is not None
        if max_tokens != 4096:
            call_kwargs["max_tokens"] = max_tokens
        try:
            response = await provider.complete(**call_kwargs)
        except BaseException:
            try:
                from machina.core.background_tasks import await_joined

                await await_joined(self.finish(reservation, failed=True))
            except Exception as e:
                raise DatabaseError("usage accounting unavailable") from e
            raise
        try:
            from machina.core.background_tasks import await_joined

            usage = (
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                if getattr(response, "cache_hit", False)
                else getattr(response, "usage", {})
            )
            cache_source = getattr(response, "cache_source", "none")
            await await_joined(
                self.finish(
                    reservation,
                    usage,
                    model=getattr(response, "model", None),
                    cache_source=cache_source if cache_source in ("exact", "semantic") else None,
                )
            )
        except Exception as e:
            raise DatabaseError("usage accounting unavailable") from e

        if (
            semantic_cache is not None
            and semantic_eligibility is not None
            and semantic_eligibility.should_serve
            and not getattr(response, "cache_hit", False)
            and canonical is not None
            and cache_context is not None
        ):
            try:
                stored = await asyncio.wait_for(
                    semantic_cache.store(
                        cache_context,
                        canonical,
                        response,
                        ttl_seconds=int(config.get("optimization_semantic_cache_ttl_seconds")),
                        max_entries=int(config.get("optimization_semantic_cache_max_entries")),
                    ),
                    timeout=2.0,
                )
                schedule_shadow(
                    ShadowEvent(
                        subsystem="semantic_cache",
                        decision="stored" if stored else "store_skipped",
                        reason_codes=(("stored",) if stored else ("response_invalid",)),
                        mode=str(semantic_eligibility.mode),
                        session_id=cache_context.session_id,
                        agent_id=cache_context.agent_id,
                        provider=cache_context.provider,
                        model=cache_context.model,
                        task_class=cache_context.task_class,
                        request_hash=canonical.request_hash,
                        tokens_before=int(
                            (getattr(response, "usage", {}) or {}).get("prompt_tokens", 0) or 0
                        ),
                    )
                )
            except Exception:
                pass
        return response

    async def prepare_stream(
        self,
        agent: Any,
        session_id: uuid.UUID | None,
        messages: list[dict[str, Any]],
        tools: list[Any] | None,
    ) -> list[dict[str, Any]]:
        """Apply gated learned compression before a streaming provider call.

        The streaming path bypasses ``complete_call``, so it must use the same
        compression policy explicitly. Any failure returns the original
        messages unchanged.
        """
        provider = getattr(agent, "provider", None)
        try:
            result = await maybe_compress_request(
                messages,
                tools,
                provider=provider,
                model=str(getattr(provider, "model", "") or ""),
                session_id=session_id,
                agent_id=str(getattr(agent, "agent_id", "") or ""),
            )
            return list(result.messages) if result.applied else messages
        except Exception:
            return messages

    async def reserve(
        self,
        session_id: uuid.UUID,
        agent_id: str,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[Any],
        max_tokens: int,
        *,
        purpose: str = "agent",
    ) -> uuid.UUID | None:
        limits = _limits()
        if not db.connected:
            if any(limits.values()):
                raise DatabaseError("database required for usage budgets")
            return None

        reserved = _estimate_tokens(messages, tools, max_tokens)
        async with db.acquire() as conn:
            async with conn.transaction():
                lineage = await conn.fetchrow(
                    """
                    SELECT ancestor_session_ids AS session_ids,
                           ancestor_agent_ids AS agent_ids,
                           is_fork
                    FROM sessions WHERE id = $1
                    """,
                    session_id,
                )
                if (lineage or {}).get("is_fork"):
                    # A user fork is an independent branch, not a delegated
                    # child: its spend must not consume the source budgets.
                    ancestor_sessions = []
                    ancestor_agents = []
                else:
                    ancestor_sessions = list((lineage or {}).get("session_ids", []))
                    ancestor_agents = list((lineage or {}).get("agent_ids", []))
                session_scopes = sorted({session_id, *ancestor_sessions}, key=str)
                agent_scopes = sorted({agent_id, *ancestor_agents})
                scopes = [("agent", aid) for aid in agent_scopes] + [
                    ("session", str(sid)) for sid in session_scopes
                ]
                # Lock in a fixed order to avoid deadlocks between concurrent
                # calls for the same agent in different sessions.
                # Conservative accounting: budget checks count every prior row
                # (reserved + complete + error). Errored calls keep the
                # conservative reservation, so excluding them would allow
                # overspend. Only 'complete'/'reserved' represent live spend,
                # but counting all statuses is intentionally conservative.
                for scope, identifier in scopes:
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        f"llm_usage:{scope}:{identifier}",
                    )
                # Combine both scope aggregates into a single query with UNION ALL
                scope_rows = await conn.fetch(
                    """
                    SELECT 'agent' AS scope, identifier, COUNT(usage.id) AS requests,
                           COALESCE(SUM(usage.accounted_tokens), 0) AS tokens
                    FROM unnest($1::text[]) AS identifier
                    LEFT JOIN llm_usage usage ON usage.agent_id = identifier
                        OR usage.ancestor_agent_ids @> ARRAY[identifier]
                    GROUP BY identifier
                    UNION ALL
                    SELECT 'session' AS scope, identifier::text, COUNT(usage.id) AS requests,
                           COALESCE(SUM(usage.accounted_tokens), 0) AS tokens
                    FROM unnest($2::uuid[]) AS identifier
                    LEFT JOIN llm_usage usage ON usage.session_id = identifier
                        OR usage.ancestor_session_ids @> ARRAY[identifier]
                    GROUP BY identifier
                    """,
                    agent_scopes,
                    session_scopes,
                )
                for scope_data in scope_rows:
                    scope = scope_data["scope"]
                    _check_limit(
                        scope,
                        int(scope_data["requests"]),
                        int(scope_data["tokens"]),
                        limits[f"usage_{scope}_request_limit"],
                        limits[f"usage_{scope}_token_limit"],
                        reserved,
                    )
                row = await conn.fetchrow(
                    "INSERT INTO llm_usage "
                    "(session_id, agent_id, provider, model, requested_model, purpose, status, "
                    "reserved_tokens, accounted_tokens, ancestor_session_ids, ancestor_agent_ids) "
                    "VALUES ($1, $2, $3, $4, $4, $5, 'reserved', $6, $6, $7, $8) RETURNING id",
                    session_id,
                    agent_id,
                    provider,
                    model,
                    purpose,
                    reserved,
                    ancestor_sessions,
                    ancestor_agents,
                )
        # S2 observe-only hook: attribute the reservation to the active turn
        # span. Accounting above is untouched; failures here never propagate.
        try:
            from machina.observability.traces import note_reserve as _trace_note_reserve

            _trace_note_reserve(
                row["id"],
                model=model,
                reserved_tokens=reserved,
                provider=provider,
            )
        except Exception:
            pass
        return row["id"]

    async def finish(
        self,
        reservation_id: uuid.UUID | None,
        usage: dict[str, Any] | None = None,
        *,
        failed: bool = False,
        model: str | None = None,
        cache_source: str | None = None,
    ) -> bool:
        """Complete a reservation. Returns False if already finished (no row with status='reserved').

        The WHERE status='reserved' guard prevents a late finish from
        overwriting a row already cleaned up or completed by another path.

        Cache categories are stored exactly as the provider reported them
        (``cache_reported`` False leaves them NULL — unknown, never zero).
        Cost is a dated estimate: NULL whenever the price snapshot cannot
        cover the used categories.
        """
        if reservation_id is None:
            return False
        usage = usage or {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        total = usage.get("total_tokens")
        total = (
            total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else 0
        )
        known = (
            not failed
            and not usage.get("estimated", False)
            and isinstance(prompt, int)
            and not isinstance(prompt, bool)
            and prompt >= 0
            and isinstance(completion, int)
            and not isinstance(completion, bool)
            and completion >= 0
            and (prompt + completion > 0 or total > 0)
        )
        accounted = max(prompt + completion, total) if known else None

        def _token(name: str) -> int | None:
            value = usage.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value

        cache_reported = bool(usage.get("cache_reported")) and known
        cache_read = _token("cache_read_tokens") if cache_reported else None
        cache_write = _token("cache_write_tokens") if cache_reported else None
        uncached = _token("uncached_input_tokens") if known else None
        if known and uncached is None and cache_read is None and cache_write is None:
            # No split was reported: the whole logical input is uncached
            # (conservative upper bound for cost).
            uncached = prompt

        if failed:
            basis: str | None = "failed"
        elif known:
            basis = str(usage.get("usage_basis") or "provider")
        elif usage.get("estimated", False):
            basis = "local_estimate"
        else:
            basis = "missing"

        cost_usd: float | None = None
        price_version: str | None = None
        if known and uncached is not None:
            try:
                row_provider = await db.fetchval(
                    "SELECT provider FROM llm_usage WHERE id = $1", reservation_id
                )
                overrides_raw = config.get("pricing_table_json")
                cost_usd, price_version = estimate_cost(
                    row_provider if isinstance(row_provider, str) else None,
                    model,
                    uncached_input_tokens=uncached,
                    cache_read_tokens=cache_read or 0,
                    cache_write_tokens=cache_write or 0,
                    output_tokens=completion,
                    overrides=parse_overrides(
                        overrides_raw if isinstance(overrides_raw, str) else ""
                    ),
                )
            except Exception:
                # Cost is additive information; a pricing failure must never
                # block the durable accounting write.
                cost_usd, price_version = None, None

        result = await db.execute(
            "UPDATE llm_usage SET status = $2, prompt_tokens = $3, completion_tokens = $4, "
            "accounted_tokens = COALESCE($5, reserved_tokens), model = COALESCE($6, model), "
            "cache_read_tokens = $7, cache_write_tokens = $8, usage_basis = $9, "
            "cost_usd = $10, price_version = $11, "
            "cache_source = COALESCE($12, cache_source), completed_at = now() "
            "WHERE id = $1 AND status = 'reserved'",
            reservation_id,
            "error" if failed else "complete",
            prompt if known else None,
            completion if known else None,
            accounted,
            model,
            cache_read,
            cache_write,
            basis,
            cost_usd,
            price_version,
            cache_source,
        )
        # S2 observe-only hook: record the exact persisted figures onto the
        # owning turn span. Only rows actually written are attributed.
        # Accounting above is untouched; failures here never propagate.
        written = parse_command_count(result) > 0
        try:
            from machina.observability.traces import note_finish as _trace_note_finish

            if written:
                _trace_note_finish(
                    reservation_id,
                    prompt_tokens=prompt if known else None,
                    completion_tokens=completion if known else None,
                    accounted_tokens=accounted,
                    failed=failed,
                    model=model,
                )
        except Exception:
            pass
        if not written:
            # M2-partner: the WHERE status='reserved' guard means another
            # path (cleanup, duplicate finish, clobber) already closed this
            # reservation. Surface the lost finish — warning + counter, never
            # raise, so the boolean contract and conservative accounting hold.
            logger.warning(
                "usage finish lost reservation %s: no reserved row "
                "(already finished, cleaned up, or clobbered)",
                reservation_id,
            )
            try:
                metrics.increment_counter("usage.finish_lost")
            except Exception:
                # Observe-only: a broken metrics sink must never break accounting.
                pass
        return written

    async def mark_accepted(self, session_id: uuid.UUID, agent_id: str) -> bool:
        """Mark the newest completed agent call for this session as accepted.

        Called by the agent loop only when a turn produced its final answer
        (no pending tool calls and no pause). Retried, summarization, and
        failed calls are never marked; the 30-minute window bounds the match
        to the live turn window (the turn timeout is far shorter), so a
        stale unaccepted row from an aborted turn can never be promoted.
        Returns False when there is nothing to mark.
        """
        try:
            result = await db.execute(
                "UPDATE llm_usage SET accepted = true WHERE id = ("
                "SELECT id FROM llm_usage WHERE session_id = $1 AND agent_id = $2 "
                "AND status = 'complete' AND accepted IS NULL "
                "AND purpose = 'agent' "
                "AND completed_at > now() - interval '30 minutes' "
                "ORDER BY completed_at DESC NULLS LAST, created_at DESC, id DESC LIMIT 1) "
                "AND status = 'complete'",
                session_id,
                agent_id,
            )
        except Exception as e:
            raise DatabaseError("usage accounting unavailable") from e
        return parse_command_count(result) > 0

    async def summary(self, session_id: uuid.UUID, agent_id: str) -> dict[str, Any]:
        limits = _limits()
        _AGG = (
            "SELECT COUNT(*) FILTER (WHERE status = 'complete') AS requests, "
            "COALESCE(SUM(accounted_tokens) FILTER (WHERE status = 'complete'), 0) AS tokens, "
            "COUNT(*) AS charged_requests, COALESCE(SUM(accounted_tokens), 0) AS charged_tokens, "
            "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
            "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
            "COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens, "
            "COUNT(*) FILTER (WHERE cache_source = 'exact') AS exact_cache_hits, "
            "COUNT(*) FILTER (WHERE prompt_tokens IS NULL) AS unknown_calls, "
            "COUNT(*) FILTER (WHERE usage_basis = 'local_estimate') AS estimated_calls, "
            "COUNT(*) FILTER (WHERE accepted) AS accepted_completions, "
            "COALESCE(SUM(cost_usd), 0) AS known_cost_usd, "
            "COUNT(*) FILTER (WHERE status = 'complete' AND cost_usd IS NULL) "
            "AS unknown_cost_calls "
            "FROM llm_usage WHERE "
        )
        session = await db.fetchrow(
            _AGG + "session_id = $1 OR ancestor_session_ids @> ARRAY[$1::uuid]",
            session_id,
        )
        agent = await db.fetchrow(
            _AGG + "agent_id = $1 OR ancestor_agent_ids @> ARRAY[$1::text]",
            agent_id,
        )

        def view(row: Any, prefix: str) -> dict[str, Any]:
            requests, tokens = int(row["requests"]), int(row["tokens"])
            charged_requests = int(row["charged_requests"])
            charged_tokens = int(row["charged_tokens"])
            accepted = int(row["accepted_completions"])
            known_cost = float(row["known_cost_usd"])
            request_limit = limits[f"usage_{prefix}_request_limit"]
            token_limit = limits[f"usage_{prefix}_token_limit"]
            # Cost per accepted completion includes every recorded call
            # (failures, retries, summaries) divided by completed turns the
            # loop marked accepted. No accepted completions -> no figure.
            return {
                "requests": requests,
                "accountedTokens": tokens,
                "chargedRequests": charged_requests,
                "chargedTokens": charged_tokens,
                "knownPromptTokens": int(row["prompt_tokens"]),
                "knownCompletionTokens": int(row["completion_tokens"]),
                "cacheReadTokens": int(row["cache_read_tokens"]),
                "cacheWriteTokens": int(row["cache_write_tokens"]),
                "exactCacheHits": _row_int(row, "exact_cache_hits"),
                "unknownCalls": int(row["unknown_calls"]),
                "estimatedCalls": int(row["estimated_calls"]),
                "acceptedCompletions": accepted,
                "knownCostUsd": known_cost,
                "unknownCostCalls": int(row["unknown_cost_calls"]),
                "costPerAcceptedCompletionUsd": (known_cost / accepted) if accepted else None,
                "requestLimit": request_limit or None,
                "tokenLimit": token_limit or None,
                "requestsRemaining": max(0, request_limit - charged_requests)
                if request_limit
                else None,
                "tokensRemaining": max(0, token_limit - charged_tokens) if token_limit else None,
            }

        return {
            "sessionId": str(session_id),
            "agentId": agent_id,
            "session": view(session, "session"),
            "agent": view(agent, "agent"),
        }

    async def cleanup_orphaned_reservations(self, older_than_minutes: int = 0) -> int:
        """Mark stale 'reserved' rows as 'error' — called on startup to clean up after crashes.

        Only reaps reservations older than `older_than_minutes` (default 0
        preserves historical behaviour and reaps all reserved).
        Production startup should pass 10 so in-flight calls are not clobbered.
        Caps each pass at 1000 rows.

        Returns the number of rows that were cleaned up.
        """
        if older_than_minutes <= 0:
            result = await db.execute(
                "UPDATE llm_usage SET status = 'error', completed_at = now() WHERE id IN ("
                "SELECT id FROM llm_usage WHERE status = 'reserved' LIMIT 1000)"
            )
        else:
            result = await db.execute(
                "UPDATE llm_usage SET status = 'error', completed_at = now() WHERE id IN ("
                "SELECT id FROM llm_usage WHERE status = 'reserved' "
                "AND created_at < now() - make_interval(mins => $1) LIMIT 1000)",
                older_than_minutes,
            )
        # Parse the command count from the result (e.g., "UPDATE 3")
        return parse_command_count(result)

    async def totals(self) -> dict[str, int]:
        row = await db.fetchrow(
            "SELECT COUNT(*) AS requests, COALESCE(SUM(accounted_tokens), 0) AS tokens, "
            "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
            "COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens, "
            "COUNT(*) FILTER (WHERE prompt_tokens IS NULL) AS unknown_calls FROM llm_usage"
        )
        return {
            "requests": int(row["requests"]),
            "accounted_tokens": int(row["tokens"]),
            "cache_read_tokens": int(row["cache_read_tokens"]),
            "cache_write_tokens": int(row["cache_write_tokens"]),
            "unknown_calls": int(row["unknown_calls"]),
        }


usage_store = UsageStore()
