"""Temporal/entity graph over durable memories.

The graph is deliberately derived from the same redacted memory content and
stored in PostgreSQL. It is not a second knowledge base: mentions and edges
are retrieval hints, and every expansion is filtered through the normal
memory row (agent, category, quarantine, and date range) before it reaches a
caller.

Extraction is deterministic and bounded. A memory write can therefore index
the graph without making an extra model call or changing the write's
transaction semantics beyond a best-effort follow-up.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from machina.db.lazy import LazyDb
from machina.memory.models import MemoryEntry
from machina.observability.diagnostics import record_failure

__all__ = [
    "Entity",
    "MemoryGraph",
    "extract_entities",
    "extract_relations",
    "has_temporal_cue",
    "memory_graph",
]

logger = logging.getLogger(__name__)
db = LazyDb()

_MAX_ENTITIES = 12
_MAX_QUERY_ENTITIES = 8
_ENTITY_LIMIT = 120

_CODE_RE = re.compile(r"`([^`\n]{2,100})`")
_QUOTED_RE = re.compile(r'"([^"\n]{2,100})"|\'([^\'\n]{2,100})\'')
_PHRASE_RE = re.compile(r"\b[A-Z][A-Za-z0-9_.-]{1,}(?:\s+[A-Z][A-Za-z0-9_.-]{1,}){0,3}\b")
_RELATION_RE = re.compile(
    r"\b(uses|using|used|depends\s+on|requires|prefers|works\s+with|owns|integrates|"
    r"calls|reads|writes|extends|replaces)\b",
    re.IGNORECASE,
)
_TEMPORAL_RE = re.compile(
    r"\b(before|after|since|until|when|timeline|chronolog\w*|earlier|later|previous|next|"
    r"first|last|yesterday|today|tomorrow|week|month|year|date|history)\b",
    re.IGNORECASE,
)
_STOP_ENTITIES = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "with",
    "i",
    "we",
    "you",
}


@dataclass(frozen=True, slots=True)
class Entity:
    display: str
    normalized: str
    kind: str = "term"


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _add_entity(entities: list[Entity], seen: set[str], value: str, kind: str) -> None:
    display = " ".join(value.split()).strip(" .,:;()[]{}")
    normalized = _normalize(display)
    if len(normalized) < 2 or len(normalized) > _ENTITY_LIMIT:
        return
    if normalized in _STOP_ENTITIES or normalized in seen:
        return
    if all(part in _STOP_ENTITIES for part in normalized.split()):
        return
    seen.add(normalized)
    entities.append(Entity(display=display[:160], normalized=normalized, kind=kind))


def extract_entities(content: str) -> list[Entity]:
    """Extract a bounded set of entity names from memory content.

    Code spans and quoted names are preferred over capitalized prose because
    they are explicit. Capitalized multi-word phrases fill the remaining
    slots; the result is deduplicated by normalized form and never exceeds
    ``_MAX_ENTITIES``.
    """
    if not content or not content.strip():
        return []
    entities: list[Entity] = []
    seen: set[str] = set()
    for match in _CODE_RE.finditer(content):
        _add_entity(entities, seen, match.group(1), "code")
    for match in _QUOTED_RE.finditer(content):
        _add_entity(entities, seen, match.group(1) or match.group(2) or "", "quoted")
    for sentence in _sentences(content):
        for match in _PHRASE_RE.finditer(sentence):
            phrase = match.group(0)
            # A sentence-initial "The" or "This" is prose, not an entity.
            words = phrase.split()
            while words and words[0].lower() in _STOP_ENTITIES:
                words.pop(0)
            if words:
                _add_entity(entities, seen, " ".join(words), "term")
            if len(entities) >= _MAX_ENTITIES:
                break
        if len(entities) >= _MAX_ENTITIES:
            break
    return entities[:_MAX_ENTITIES]


def _sentences(content: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+|\n+", content) if part.strip()]


def extract_relations(content: str, entities: list[Entity]) -> list[tuple[Entity, Entity, str]]:
    """Extract subject-relation-object hints where both ends are known entities.

    This is intentionally conservative: a relation needs two extracted
    entities in one sentence and an explicit relation verb. Co-occurrence is
    represented by mentions, not by a synthetic edge.
    """
    if len(entities) < 2:
        return []
    relations: list[tuple[Entity, Entity, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for sentence in _sentences(content):
        present = [entity for entity in entities if entity.display in sentence]
        verb = _RELATION_RE.search(sentence)
        if len(present) < 2 or verb is None:
            continue
        present.sort(key=lambda entity: sentence.index(entity.display))
        relation = " ".join(verb.group(1).lower().split())
        for left, right in zip(present, present[1:], strict=False):
            if left.normalized == right.normalized:
                continue
            key = (left.normalized, right.normalized, relation)
            if key in seen:
                continue
            seen.add(key)
            relations.append((left, right, relation))
            if len(relations) >= 16:
                return relations
    return relations


def has_temporal_cue(query: str) -> bool:
    return bool(_TEMPORAL_RE.search(query))


class MemoryGraph:
    """Persistent entity graph and time-aware retrieval expansion."""

    def __init__(self, database: Any | None = None) -> None:
        self.db = database or db
        self._warned = False

    def enabled(self) -> bool:
        try:
            from machina.core.config import get_config

            return bool(get_config().get("memory_graph_enabled"))
        except Exception:
            return True

    async def ingest(self, memory: MemoryEntry) -> None:
        """Index one durable memory. Failures degrade retrieval, not the write."""
        if not self.enabled() or memory.quarantined:
            return
        entities = extract_entities(memory.content)
        if not entities:
            return
        relations = extract_relations(memory.content, entities)
        try:
            async with self.db.acquire() as conn, conn.transaction():
                entity_ids: dict[str, Any] = {}
                for entity in entities:
                    entity_id = await conn.fetchval(
                        """
                        INSERT INTO memory_entities
                            (normalized_name, display_name, kind)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (normalized_name) DO UPDATE
                        SET display_name = EXCLUDED.display_name,
                            kind = EXCLUDED.kind,
                            last_seen = now()
                        RETURNING id
                        """,
                        entity.normalized,
                        entity.display,
                        entity.kind,
                    )
                    entity_ids[entity.normalized] = entity_id
                    await conn.execute(
                        """
                        INSERT INTO memory_entity_mentions (memory_id, entity_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                        """,
                        memory.id,
                        entity_id,
                    )
                for left, right, relation in relations:
                    source = entity_ids.get(left.normalized)
                    target = entity_ids.get(right.normalized)
                    if source is None or target is None or source == target:
                        continue
                    await conn.execute(
                        """
                        INSERT INTO memory_edges
                            (source_entity_id, target_entity_id, relation, memory_id)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT DO NOTHING
                        """,
                        source,
                        target,
                        relation,
                        memory.id,
                    )
        except Exception as error:
            if not self._warned:
                self._warned = True
                logger.warning(
                    "memory graph unavailable; retrieval will use flat memories: %s",
                    type(error).__name__,
                )
                record_failure("memory_graph.ingest", error)

    async def expand(
        self,
        seed_memory_ids: list[Any],
        *,
        query: str = "",
        agent_id: str | None = None,
        category: str | None = None,
        date_range: tuple[datetime, datetime] | None = None,
        limit: int = 10,
    ) -> list[tuple[MemoryEntry, float]]:
        """Return graph-related memories ordered by shared entity and time.

        Seeds contribute their entities; a query may contribute explicit
        entity names even when the flat retrieval arms returned no rows.
        Explicit edges are traversed one hop. Rows stay subject to the same
        agent/category/date filters as ordinary memory retrieval.
        """
        if not self.enabled():
            return []
        query_entities = [entity.normalized for entity in extract_entities(query)][
            :_MAX_QUERY_ENTITIES
        ]
        seed_ids = [str(value) for value in seed_memory_ids if value is not None]
        if not seed_ids and not query_entities:
            return []
        params: list[Any] = [seed_ids, query_entities]
        param_idx = 3
        conditions = ["m.quarantined = FALSE"]
        if agent_id is not None:
            conditions.append(f"m.agent_id = ${param_idx}")
            params.append(agent_id)
            param_idx += 1
        if category is not None:
            conditions.append(f"m.category = ${param_idx}")
            params.append(category)
            param_idx += 1
        if date_range is not None:
            conditions.append(f"m.created_at BETWEEN ${param_idx} AND ${param_idx + 1}")
            params.extend(date_range)
            param_idx += 2
        temporal = has_temporal_cue(query)
        order = (
            "ABS(EXTRACT(EPOCH FROM (m.created_at - (SELECT anchor FROM seed_anchor)))) ASC"
            if temporal
            else "m.created_at DESC"
        )
        params.append(max(1, min(64, int(limit))))
        sql = f"""
            WITH seed_entities AS (
                SELECT DISTINCT entity_id
                FROM memory_entity_mentions
                WHERE memory_id = ANY($1::uuid[])
                UNION
                SELECT id FROM memory_entities
                WHERE normalized_name = ANY($2::text[])
            ),
            related_entities AS (
                SELECT entity_id FROM seed_entities
                UNION
                SELECT CASE
                    WHEN e.source_entity_id IN (SELECT entity_id FROM seed_entities)
                    THEN e.target_entity_id
                    ELSE e.source_entity_id
                END
                FROM memory_edges e
                WHERE e.source_entity_id IN (SELECT entity_id FROM seed_entities)
                   OR e.target_entity_id IN (SELECT entity_id FROM seed_entities)
            ),
            seed_anchor AS (
                SELECT max(created_at) AS anchor
                FROM memories
                WHERE id = ANY($1::uuid[])
            ),
            matches AS (
                SELECT mem.memory_id AS id,
                       COUNT(DISTINCT mem.entity_id) AS shared
                FROM memory_entity_mentions mem
                WHERE mem.entity_id IN (SELECT entity_id FROM related_entities)
                  AND mem.memory_id <> ALL($1::uuid[])
                GROUP BY mem.memory_id
            )
            SELECT m.id, m.session_id, m.agent_id, m.content, m.category,
                   m.importance, m.created_at, m.last_accessed, m.access_count,
                   m.explicitly_important, m.base_strength, m.quarantined,
                   matches.shared
            FROM matches
            JOIN memories m ON m.id = matches.id
            WHERE {" AND ".join(conditions)}
            ORDER BY matches.shared DESC, {order}
            LIMIT ${param_idx}
        """  # nosec B608 # Fixed clauses; every external value is a bound parameter.
        try:
            rows = await self.db.fetch(sql, *params)
        except Exception as error:
            if not self._warned:
                self._warned = True
                logger.warning(
                    "memory graph expansion unavailable; using flat retrieval: %s",
                    type(error).__name__,
                )
                record_failure("memory_graph.expand", error)
            return []
        from machina.memory.store import memory_store

        results: list[tuple[MemoryEntry, float]] = []
        for row in rows:
            entry = memory_store._row_to_entry(row)
            shared = int(row["shared"] or 1)
            results.append((entry, min(0.4, 0.06 + 0.04 * shared)))
        return results


memory_graph = MemoryGraph()
