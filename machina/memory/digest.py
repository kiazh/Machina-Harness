"""Query-conditioned memory digest.

Memorilla compresses a whole collection into a fixed set of query-dependent
memory tokens. Machina cannot use that module directly: it needs a trained
GPU model and changes the provider interface. The transferable contract is
smaller and deterministic:

* retrieve a wider candidate set than the normal top-k,
* select complementary facts instead of near-duplicates,
* render a bounded digest with provenance,
* disclose what was omitted.

This module is pure: no database, model, provider, or network access.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from machina.memory.models import RetrievedMemory

__all__ = ["MemoryDigest", "build_memory_digest", "select_diverse"]

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_+#.-]*", re.IGNORECASE)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "please",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "you",
        "your",
    }
)
_CONTENT_CHAR_LIMIT = 320
_MIN_DIGEST_CHARS = 80


@dataclass(frozen=True)
class MemoryDigest:
    """A bounded, query-conditioned view of retrieved memories."""

    text: str
    selected: tuple[RetrievedMemory, ...]
    omitted: int
    char_count: int


def _tokens(text: str) -> set[str]:
    tokens = {
        token
        for token in (
            match.group(0).lower().strip(".-") for match in _WORD_RE.finditer(text or "")
        )
        if len(token) >= 2 and token not in _STOPWORDS
    }
    return tokens


def _category_match(category: str, query_tokens: set[str]) -> bool:
    category = (category or "").lower()
    if not category:
        return False
    return any(
        token == category or token.startswith(category) or category.startswith(token)
        for token in query_tokens
    )


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _relevance(memory: RetrievedMemory, max_score: float) -> float:
    normalized = memory.score / max_score if max_score > 0 else 0.0
    entry = memory.memory
    return min(1.0, max(0.0, 0.78 * normalized + 0.22 * entry.importance))


def _query_overlap(tokens: set[str], query_tokens: set[str]) -> float:
    if not tokens or not query_tokens:
        return 0.0
    return len(tokens & query_tokens) / len(query_tokens)


def _one_line(text: str, limit: int) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _render_item(memory: RetrievedMemory) -> str:
    entry = memory.memory
    content = _one_line(entry.content, _CONTENT_CHAR_LIMIT)
    # Keep the prompt-visible wrapper compact; the chunk payload carries the
    # full source IDs, scores, and importance for auditability.
    rendered = f"- [{entry.category}] {content}"
    interpretation = memory.persona_interpretation
    if interpretation:
        rendered += f" (persona: {_one_line(interpretation, 120)})"
    return rendered


def select_diverse(
    memories: Sequence[RetrievedMemory],
    query: str,
    *,
    max_items: int = 6,
    max_chars: int = 1200,
    diversity: float = 0.28,
) -> tuple[RetrievedMemory, ...]:
    """Select complementary memories with maximal marginal relevance.

    Relevance is the retriever's score plus memory importance; the query
    overlap is a small tie-breaker. Duplicate contents are collapsed before
    selection so repeated facts cannot crowd out a second useful fact.
    """
    if max_items <= 0 or max_chars < _MIN_DIGEST_CHARS:
        return ()
    query_tokens = _tokens(query)
    unique: list[RetrievedMemory] = []
    seen: set[str] = set()
    for memory in memories:
        content = _normalize(memory.memory.content)
        if not content or content in seen:
            continue
        seen.add(content)
        unique.append(memory)
    if not unique:
        return ()

    max_score = max(memory.score for memory in unique)
    token_sets = {id(memory): _tokens(memory.memory.content) for memory in unique}
    relevance = {id(memory): _relevance(memory, max_score) for memory in unique}
    query_overlap = {id(memory): _query_overlap(token_sets[id(memory)], query_tokens) for memory in unique}

    selected: list[RetrievedMemory] = []
    selected_tokens: list[set[str]] = []
    remaining = list(unique)
    char_budget = max_chars - len("[memory digest: 0 of 0 facts]\n")
    while remaining and len(selected) < max_items:
        best: RetrievedMemory | None = None
        best_score = -1.0
        for memory in remaining:
            key = id(memory)
            redundancy = max(
                (_jaccard(token_sets[key], prior) for prior in selected_tokens),
                default=0.0,
            )
            category_bonus = 1.0 if _category_match(memory.memory.category, query_tokens) else 0.0
            utility = (
                0.52 * relevance[key]
                + 0.24 * query_overlap[key]
                + 0.12 * category_bonus
                + 0.12 * memory.memory.importance
                - diversity * redundancy
            )
            if utility > best_score or (
                utility == best_score and best is not None and relevance[key] > relevance[id(best)]
            ):
                best, best_score = memory, utility
        if best is None:
            break
        rendered = _render_item(best)
        if selected and len(rendered) + 1 > char_budget:
            break
        if not selected and len(rendered) > char_budget:
            # Always keep one fact; truncate it to the caller's hard budget.
            entry = best.memory
            allowed = max(20, char_budget - 60)
            entry_content = _one_line(entry.content, allowed)
            rendered = f"- [{entry.category}] {entry_content}"
        selected.append(best)
        selected_tokens.append(token_sets[id(best)])
        char_budget -= len(rendered) + 1
        remaining.remove(best)
    return tuple(selected)


def build_memory_digest(
    memories: Sequence[RetrievedMemory],
    query: str,
    *,
    max_items: int = 6,
    max_chars: int = 1200,
    diversity: float = 0.28,
) -> MemoryDigest:
    """Render a bounded, query-conditioned digest with explicit omission."""
    if not memories or max_items <= 0 or max_chars < _MIN_DIGEST_CHARS:
        return MemoryDigest("", (), 0, 0)
    unique_count = len({_normalize(memory.memory.content) for memory in memories if memory.memory.content.strip()})
    selected = select_diverse(
        memories,
        query,
        max_items=max_items,
        max_chars=max_chars,
        diversity=diversity,
    )
    if not selected:
        return MemoryDigest("", (), unique_count, 0)
    omitted = max(0, unique_count - len(selected))
    header = f"[memory digest: {len(selected)} of {unique_count} facts]"
    lines = [header, *(_render_item(memory) for memory in selected)]
    if omitted:
        lines.append(f"[+{omitted} omitted]")
    text = "\n".join(lines)
    if len(text) > max_chars:
        # The final omission line is the first to lose under pressure, but the
        # selected fact count already discloses that a digest is not complete.
        text = text[: max(0, max_chars - 1)].rstrip() + "…"
    return MemoryDigest(text, selected, omitted, len(text))
