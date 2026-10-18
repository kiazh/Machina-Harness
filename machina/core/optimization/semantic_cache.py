"""Tier-2 semantic cache storage and lookup.

The cache never performs a vector comparison without exact scope guards.
Only redacted, non-tool, complete responses are stored, and only a bounded
canonical request is embedded. Every failure degrades to a cache miss.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from machina.core.models import LLMResponse
from machina.core.optimization.cache_context import CacheContext
from machina.core.optimization.canonicalizer import (
    CanonicalRequest,
    scope_fingerprint,
)
from machina.core.optimization.policy import _contains_pii, breakers
from machina.db.lazy import LazyDb
from machina.memory.embeddings import embed_query, embedding_space_id
from machina.memory.redaction import redact_secrets

__all__ = [
    "SemanticCache",
    "SemanticHit",
    "VECTOR_DIMENSIONS",
    "normalize_embedding",
    "valid_response",
]

VECTOR_DIMENSIONS = 1536
_MAX_RESPONSE_CHARS = 64_000


@dataclass(frozen=True)
class SemanticHit:
    response: LLMResponse
    similarity: float
    entry_id: uuid.UUID


def normalize_embedding(vector: list[float] | None) -> list[float] | None:
    """Return a finite 1536-wide vector, or None for unusable input."""
    if not vector:
        return None
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError):
        return None
    if not values or not all(math.isfinite(value) for value in values):
        return None
    if not any(values):
        return None
    if len(values) > VECTOR_DIMENSIONS:
        values = values[:VECTOR_DIMENSIONS]
    elif len(values) < VECTOR_DIMENSIONS:
        values = [*values, *([0.0] * (VECTOR_DIMENSIONS - len(values)))]
    return values


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.9g}" for value in vector) + "]"


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return default


def _finish_reason(response: LLMResponse) -> str:
    raw = response.raw if isinstance(response.raw, dict) else {}
    reason = raw.get("finish_reason")
    if reason is None:
        reason = raw.get("stop_reason")
    if reason is None and isinstance(raw.get("choices"), list) and raw["choices"]:
        choice = raw["choices"][0]
        if isinstance(choice, dict):
            reason = choice.get("finish_reason")
    return str(reason or "").lower()


def valid_response(response: Any) -> bool:
    """Whether a provider response is safe to store and replay."""
    if not isinstance(response, LLMResponse):
        return False
    if not isinstance(response.content, str) or not response.content.strip():
        return False
    if response.tool_calls:
        return False
    if len(response.content) > _MAX_RESPONSE_CHARS:
        return False
    reason = _finish_reason(response)
    if reason in {"length", "max_tokens", "content_filter", "error"}:
        return False
    raw = response.raw if isinstance(response.raw, dict) else {}
    if raw.get("error"):
        return False
    try:
        if redact_secrets(response.content).was_redacted:
            return False
    except Exception:
        return False
    return not _contains_pii(response.content)


def _default_embedder() -> Callable[[str], Awaitable[list[float] | None]]:
    async def _embed(text: str) -> list[float] | None:
        return await embed_query(text)

    return _embed


def _default_embedding_space() -> str | None:
    try:
        return embedding_space_id()
    except Exception:
        return None


class SemanticCache:
    """Thin, parameterized repository over ``semantic_cache_entries``."""

    def __init__(
        self,
        *,
        database: Any | None = None,
        embedder: Callable[[str], Awaitable[list[float] | None]] | None = None,
        embedding_space: Callable[[], str | None] | None = None,
    ) -> None:
        self.db = database if database is not None else LazyDb()
        self.embedder = embedder or _default_embedder()
        self.embedding_space = embedding_space or _default_embedding_space

    @property
    def _connected(self) -> bool:
        try:
            return bool(self.db.connected)
        except Exception:
            return False

    async def lookup(
        self,
        context: CacheContext,
        canonical: CanonicalRequest,
        *,
        threshold: float,
        record_hit: bool = True,
    ) -> SemanticHit | None:
        if not context.is_auxiliary_read_only:
            return None
        if canonical.char_count > 2000:
            return None
        if context.freshness_sensitive or context.attachments:
            return None
        if not self._connected:
            return None
        if not breakers().allow("semantic_cache"):
            return None
        try:
            space = self.embedding_space()
        except Exception:
            breakers().record_failure("semantic_cache")
            return None
        if not isinstance(space, str) or not space:
            return None
        try:
            embedding = normalize_embedding(await self.embedder(canonical.embedding_text))
        except Exception:
            breakers().record_failure("semantic_cache")
            return None
        if embedding is None:
            return None
        scope = scope_fingerprint(
            context,
            embedding_space=space,
            schema_hash=context.tool_schema_hash,
        )
        try:
            rows = await self.db.fetch(
                """
                SELECT id, response_content, response_model, finish_reason,
                       1 - (embedding <=> $1::vector) AS similarity
                FROM semantic_cache_entries
                WHERE scope_fingerprint = $2
                  AND embedding_space = $3
                  AND expires_at > now()
                ORDER BY embedding <=> $1::vector
                LIMIT 1
                """,
                _vector_literal(embedding),
                scope,
                space,
            )
        except Exception:
            breakers().record_failure("semantic_cache")
            return None
        if not rows:
            breakers().record_success("semantic_cache")
            return None
        row = rows[0]
        try:
            similarity = float(_row_value(row, "similarity", 0.0) or 0.0)
        except (TypeError, ValueError):
            similarity = 0.0
        if similarity < threshold:
            breakers().record_success("semantic_cache")
            return None
        content = _row_value(row, "response_content", "")
        if not isinstance(content, str) or not content.strip():
            return None
        stored_finish = str(_row_value(row, "finish_reason", "") or "").lower()
        if stored_finish in {"length", "max_tokens", "content_filter", "error"}:
            return None
        entry_id = _row_value(row, "id")
        response = LLMResponse(
            content=content,
            model=str(_row_value(row, "response_model", context.model) or context.model),
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            cache_hit=True,
            cache_source="semantic",
        )
        if not valid_response(response):
            return None
        if record_hit:
            try:
                await self.db.execute(
                    "UPDATE semantic_cache_entries "
                    "SET last_hit_at = now(), hit_count = hit_count + 1 WHERE id = $1",
                    entry_id,
                )
            except Exception:
                # A telemetry write must not turn a valid hit into a miss.
                pass
        breakers().record_success("semantic_cache")
        return SemanticHit(response=response, similarity=similarity, entry_id=entry_id)

    async def store(
        self,
        context: CacheContext,
        canonical: CanonicalRequest,
        response: Any,
        *,
        ttl_seconds: int,
        max_entries: int,
    ) -> bool:
        if not context.is_auxiliary_read_only:
            return False
        if canonical.char_count > 2000:
            return False
        if context.freshness_sensitive or context.attachments:
            return False
        if not self._connected or ttl_seconds <= 0 or not valid_response(response):
            return False
        if not breakers().allow("semantic_cache"):
            return False
        try:
            space = self.embedding_space()
        except Exception:
            breakers().record_failure("semantic_cache")
            return False
        if not isinstance(space, str) or not space:
            return False
        try:
            embedding = normalize_embedding(await self.embedder(canonical.embedding_text))
        except Exception:
            breakers().record_failure("semantic_cache")
            return False
        if embedding is None:
            return False
        schema_hash = context.tool_schema_hash
        scope = scope_fingerprint(context, embedding_space=space, schema_hash=schema_hash)
        try:
            await self.db.execute(
                """
                INSERT INTO semantic_cache_entries (
                    scope_fingerprint, session_id, agent_id, provider, model,
                    task_class, request_hash, tool_schema_hash,
                    workspace_root, embedding_space, embedding,
                    response_content, response_model, finish_reason, expires_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                    $11::vector, $12, $13, $14,
                    now() + make_interval(secs => $15)
                )
                ON CONFLICT (scope_fingerprint, request_hash, embedding_space)
                DO UPDATE SET
                    response_content = EXCLUDED.response_content,
                    response_model = EXCLUDED.response_model,
                    finish_reason = EXCLUDED.finish_reason,
                    expires_at = EXCLUDED.expires_at
                """,
                scope,
                context.session_id,
                context.agent_id,
                context.provider,
                context.model,
                context.task_class,
                canonical.request_hash,
                schema_hash,
                context.workspace_root,
                space,
                _vector_literal(embedding),
                response.content,
                response.model,
                _finish_reason(response) or None,
                int(ttl_seconds),
            )
            if max_entries > 0:
                await self._trim(max_entries, context.workspace_root)
            breakers().record_success("semantic_cache")
            return True
        except Exception:
            breakers().record_failure("semantic_cache")
            return False

    async def _trim(self, max_entries: int, workspace_root: str) -> None:
        try:
            await self.db.execute(
                """
                DELETE FROM semantic_cache_entries
                WHERE workspace_root = $2
                  AND id IN (
                    SELECT id FROM semantic_cache_entries
                    WHERE workspace_root = $2
                    ORDER BY created_at ASC
                    OFFSET $1
                )
                """,
                int(max_entries),
                workspace_root,
            )
        except Exception:
            pass

    async def vacuum(self) -> int:
        if not self._connected:
            return 0
        try:
            result = await self.db.execute(
                "DELETE FROM semantic_cache_entries WHERE expires_at <= now()"
            )
        except Exception:
            breakers().record_failure("semantic_cache")
            return 0
        try:
            return int(str(result).split()[-1])
        except (TypeError, ValueError, IndexError):
            return 0

    async def invalidate(self, *, session_id: uuid.UUID | None = None) -> int:
        if not self._connected or session_id is None:
            return 0
        try:
            result = await self.db.execute(
                "DELETE FROM semantic_cache_entries WHERE session_id = $1", session_id
            )
        except Exception:
            breakers().record_failure("semantic_cache")
            return 0
        try:
            return int(str(result).split()[-1])
        except (TypeError, ValueError, IndexError):
            return 0
