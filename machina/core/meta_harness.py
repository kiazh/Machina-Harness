"""Deterministic per-prompt routing across installed agent harnesses.

This module is the local classifier for the meta-harness layer. It never
calls a provider and never executes a harness. It builds one TaskDescriptor
with the existing deterministic intent layer, compares it against a small
capability registry with hashed local vectors and lexical overlap, and returns
a bounded route plan.

The route is advisory. It cannot widen tools, execution mode, or delegation
authority. Actual harness execution goes through the approval-gated
``harness_delegate`` tool.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from machina import interop
from machina.core.intent import TASK_CLASSES
from machina.core.task_descriptor import TASK_FAMILIES, TaskDescriptor, build_task_descriptor

__all__ = [
    "HarnessCard",
    "MetaRoute",
    "RouteCandidate",
    "RouteStep",
    "clear_availability_cache",
    "harness_cards",
    "route_prompt",
]

_VECTOR_DIMENSIONS = 192
_AVAILABILITY_TTL_SECONDS = 30.0
_MIN_EXTERNAL_SCORE = 0.42
_MIN_EXTERNAL_MARGIN = 0.025
_MAX_CLAUSES = 4
_MAX_PROMPT_CHARS = 20_000
_EXTERNAL_KEYS = ("opencode", "codex", "hermes")

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_+#.\-]*", re.IGNORECASE)
_CLAUSE_RE = re.compile(
    r"\s*(?:;|\n|\band then\b|\bthen\b|\band also\b)\s*",
    re.IGNORECASE,
)
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
        "into",
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
        "so",
        "that",
        "the",
        "their",
        "this",
        "to",
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


# Bounded local cue sets for the cases where the shared intent classifier is
# deliberately conservative (an ambiguous imperative or a short follow-on).
# They fill only missing descriptor facts; they never override conversational
# turns, and they never select a harness by themselves.
_REVIEW_ACTIONS = frozenset(
    {"audit", "checkout", "critique", "penetration", "review"}
)
_DOCUMENTATION_CUES = frozenset(
    {
        "changelog",
        "docs",
        "documentation",
        "draft",
        "guide",
        "manual",
        "notes",
        "readme",
        "release",
        "summary",
        "summarize",
        "writeup",
    }
)
_DOCUMENTATION_ANCHORS = frozenset(
    {"changelog", "docs", "documentation", "guide", "manual", "readme", "writeup"}
)
_RESEARCH_ANCHORS = frozenset(
    {
        "benchmark",
        "benchmarking",
        "compare",
        "comparison",
        "embedding",
        "embeddings",
        "investigate",
        "literature",
        "paper",
        "papers",
        "pubmed",
        "research",
        "source",
        "sources",
        "survey",
    }
)
_CODING_ACTIONS = frozenset(
    {
        "benchmark",
        "build",
        "compile",
        "debug",
        "fix",
        "implement",
        "lint",
        "migrate",
        "migration",
        "optimize",
        "patch",
        "profile",
        "refactor",
        "repair",
        "test",
        "tests",
        "testing",
    }
)
_CODING_NOUNS = frozenset(
    {
        "api",
        "backend",
        "cache",
        "cli",
        "component",
        "database",
        "endpoint",
        "estimator",
        "flag",
        "frontend",
        "function",
        "handler",
        "middleware",
        "pagination",
        "pytest",
        "query",
        "react",
        "repo",
        "schema",
        "shell",
        "spawn",
        "sql",
        "timeout",
        "token",
        "typescript",
        "javascript",
        "python",
        "rust",
        "ui",
        "accessibility",
        "performance",
        "latency",
    }
)
_CODE_CONTEXT = _CODING_ACTIONS | _CODING_NOUNS


@dataclass(frozen=True)
class HarnessCard:
    """A capability card for one harness or the internal loop.

    Cards are data, not prompts. The route vocabulary is bounded and shared
    with TaskDescriptor families/capabilities so a new card cannot invent a
    third taxonomy.
    """

    name: str
    label: str
    kind: str
    description: str
    task_families: tuple[str, ...]
    task_classes: tuple[str, ...]
    capabilities: tuple[str, ...]
    strengths: tuple[str, ...]
    keywords: tuple[str, ...]
    installed: bool = True
    run_supported: bool = True
    registered: bool | None = None

    @property
    def available(self) -> bool:
        return self.kind == "internal" or (self.installed and self.run_supported)

    def document(self) -> str:
        return " ".join(
            (
                self.label,
                self.description,
                " ".join(self.strengths),
                " ".join(self.keywords),
                " ".join(self.task_families),
                " ".join(self.task_classes),
                " ".join(self.capabilities),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "description": self.description,
            "taskFamilies": list(self.task_families),
            "taskClasses": list(self.task_classes),
            "capabilities": list(self.capabilities),
            "strengths": list(self.strengths),
            "installed": self.installed,
            "runSupported": self.run_supported,
            "registered": self.registered,
            "available": self.available,
        }


@dataclass(frozen=True)
class RouteCandidate:
    """One scored harness candidate with the components that produced it."""

    name: str
    label: str
    score: float
    vector: float
    keyword: float
    family: float
    capability: float
    available: bool
    run_supported: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "score": round(self.score, 4),
            "vector": round(self.vector, 4),
            "keyword": round(self.keyword, 4),
            "family": round(self.family, 4),
            "capability": round(self.capability, 4),
            "available": self.available,
            "runSupported": self.run_supported,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RouteStep:
    """One independently routed clause from a multi-intent prompt."""

    intent: str
    task_class: str
    family: str
    primary: str
    mode: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "taskClass": self.task_class,
            "family": self.family,
            "primary": self.primary,
            "mode": self.mode,
            "confidence": round(self.confidence, 4),
        }


@dataclass(frozen=True)
class MetaRoute:
    """Deterministic route plan for one user prompt."""

    query: str
    task_class: str
    task_families: tuple[str, ...]
    capabilities: tuple[str, ...]
    complexity: str
    primary: str
    mode: str
    confidence: float
    reason: str
    candidates: tuple[RouteCandidate, ...]
    steps: tuple[RouteStep, ...]
    local_only: bool = True

    def prompt_hint(self) -> str:
        """Compact advisory hint for the system prompt; empty for internal."""
        if self.mode != "external" or self.primary == "machina":
            return ""
        primary = next(
            (candidate for candidate in self.candidates if candidate.name == self.primary),
            None,
        )
        if primary is None:
            return ""
        label = primary.label
        alternate = next(
            (
                candidate
                for candidate in self.candidates
                if candidate.name != self.primary and candidate.available
            ),
            None,
        )
        route_text = f"{self.task_class} -> {label} ({self.confidence:.2f})"
        if len(self.steps) > 1:
            route_text = "; ".join(
                f"{step.family or step.task_class} -> {step.primary} "
                f"({step.confidence:.2f})"
                for step in self.steps[:2]
            )
        elif alternate is not None and primary.score - alternate.score < 0.08:
            route_text += f"; alternate {alternate.label} ({alternate.score:.2f})"
        return (
            "Meta-harness route (local, advisory): "
            f"{route_text}. For a bounded second implementation or review, "
            f"call harness_delegate(platform=\"{self.primary}\", task=\"<task>\"). "
            "Approval and tool permissions still apply."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "taskClass": self.task_class,
            "taskFamilies": list(self.task_families),
            "capabilities": list(self.capabilities),
            "complexity": self.complexity,
            "primary": self.primary,
            "mode": self.mode,
            "confidence": round(self.confidence, 4),
            "localOnly": self.local_only,
            "reason": self.reason,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "steps": [step.to_dict() for step in self.steps],
            "promptHint": self.prompt_hint(),
        }


_INTERNAL_CARD = HarnessCard(
    name="machina",
    label="Machina",
    kind="internal",
    description=(
        "Local agent orchestration with permissions, memory, skills, file editing, "
        "terminal tools, review, and planning."
    ),
    task_families=("web-build", "coding", "research", "review", "task", "continuation"),
    task_classes=TASK_CLASSES,
    capabilities=(
        "testing",
        "authentication",
        "security",
        "database",
        "payments",
        "deployment",
        "documentation",
        "api",
        "research",
        "review",
        "ui",
        "accessibility",
    ),
    strengths=(
        "local orchestration",
        "permission-safe editing",
        "memory and recall",
        "skills",
        "file editing",
        "terminal",
        "review",
        "planning",
    ),
    keywords=(
        "machina",
        "local",
        "workspace",
        "agent",
        "permissions",
        "memory",
        "skill",
        "code",
        "review",
        "research",
        "plan",
        "implement",
        "fix",
        "test",
        "ui",
        "api",
        "security",
        "database",
        "deployment",
    ),
)

_BASE_EXTERNAL_CARDS: tuple[HarnessCard, ...] = (
    HarnessCard(
        name="opencode",
        label="OpenCode",
        kind="external",
        description=(
            "Terminal-first coding harness for fast local repository edits, "
            "multi-file implementation, and iterative test loops."
        ),
        task_families=("web-build", "coding", "review", "task"),
        task_classes=("implement", "debug", "review-readonly", "learn-directory"),
        capabilities=(
            "testing",
            "api",
            "ui",
            "database",
            "deployment",
            "accessibility",
        ),
        strengths=(
            "fast terminal code edits",
            "repo navigation",
            "multi-file implementation",
            "test loops",
            "iterative fixes",
        ),
        keywords=(
            "opencode",
            "code",
            "edit",
            "implement",
            "refactor",
            "fix",
            "bug",
            "debug",
            "error",
            "stack",
            "trace",
            "test",
            "pytest",
            "repo",
            "file",
            "typescript",
            "javascript",
            "python",
            "react",
            "frontend",
            "backend",
            "ui",
            "terminal",
            "multi-file",
            "shell",
            "spawn",
            "timeout",
            "windows",
            "process",
            "performance",
            "latency",
            "optimize",
            "profile",
            "benchmark",
            "migration",
            "sql",
            "endpoint",
            "pagination",
            "handler",
            "schema",
            "middleware",
            "cli",
            "flag",
            "cache",
            "query",
        ),
        installed=False,
        run_supported=True,
    ),
    HarnessCard(
        name="codex",
        label="Codex",
        kind="external",
        description=(
            "Autonomous coding harness for deep code changes, test-driven fixes, "
            "security review, long-running refactors, and CI repair."
        ),
        task_families=("coding", "review", "task"),
        task_classes=("implement", "debug", "review-readonly", "learn-directory"),
        capabilities=(
            "testing",
            "security",
            "deployment",
            "api",
            "documentation",
        ),
        strengths=(
            "deep autonomous code changes",
            "test-driven fixes",
            "security review",
            "long-running refactors",
            "CI repair",
        ),
        keywords=(
            "codex",
            "autonomous",
            "code",
            "refactor",
            "test",
            "pytest",
            "review",
            "audit",
            "bug",
            "fix",
            "debug",
            "error",
            "stack",
            "trace",
            "security",
            "vulnerability",
            "ci",
            "build",
            "typescript",
            "rust",
            "python",
            "long-running",
            "shell",
            "spawn",
            "timeout",
            "windows",
            "process",
            "performance",
            "latency",
            "optimize",
            "profile",
            "benchmark",
            "migration",
            "sql",
            "endpoint",
            "pagination",
            "handler",
            "schema",
            "middleware",
            "cli",
            "flag",
            "cache",
            "query",
        ),
        installed=False,
        run_supported=True,
    ),
    HarnessCard(
        name="hermes",
        label="Hermes",
        kind="external",
        description=(
            "Long-context research harness for source comparison, synthesis, "
            "documentation, and implementation planning."
        ),
        task_families=("research", "task"),
        task_classes=("explain", "learn-directory", "implement"),
        capabilities=("research", "documentation"),
        strengths=(
            "long-context research",
            "web synthesis",
            "planning",
            "writing",
            "source comparison",
        ),
        keywords=(
            "hermes",
            "research",
            "investigate",
            "compare",
            "comparison",
            "summarize",
            "summary",
            "sources",
            "source",
            "web",
            "documentation",
            "docs",
            "readme",
            "changelog",
            "release",
            "notes",
            "draft",
            "guide",
            "writeup",
            "latest",
            "embedding",
            "embeddings",
            "models",
            "benchmark",
            "plan",
            "architecture",
            "explain",
            "literature",
            "paper",
            "papers",
            "survey",
        ),
        installed=False,
        run_supported=True,
    ),
)

_AVAILABILITY_LOCK = threading.Lock()
_AVAILABILITY_CACHE: tuple[float, dict[str, dict[str, Any]]] | None = None


def clear_availability_cache() -> None:
    """Drop the spawn-free harness availability cache (tests and explicit refresh)."""
    global _AVAILABILITY_CACHE
    with _AVAILABILITY_LOCK:
        _AVAILABILITY_CACHE = None


def _platform_availability(refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Spawn-free installed/run-support snapshot for the known harnesses.

    ``interop.state_summary`` performs PATH lookups and a state-file read only.
    It never probes versions, process lists, or MCP registrations. The 30s
    cache keeps a per-turn router from repeating even that small cost.
    """
    global _AVAILABILITY_CACHE
    now = time.monotonic()
    with _AVAILABILITY_LOCK:
        if (
            not refresh
            and _AVAILABILITY_CACHE is not None
            and now - _AVAILABILITY_CACHE[0] < _AVAILABILITY_TTL_SECONDS
        ):
            return _AVAILABILITY_CACHE[1]
    try:
        summary = interop.state_summary()
        supported = set(interop.supported_runs())
        platforms = summary.get("platforms", {}) if isinstance(summary, dict) else {}
        rows: dict[str, dict[str, Any]] = {}
        for key in _EXTERNAL_KEYS:
            entry = platforms.get(key, {}) if isinstance(platforms, dict) else {}
            entry = entry if isinstance(entry, dict) else {}
            rows[key] = {
                "installed": bool(entry.get("installed")),
                "registered": entry.get("registered"),
                "run_supported": key in supported,
            }
    except Exception:
        rows = {
            key: {"installed": False, "registered": None, "run_supported": False}
            for key in _EXTERNAL_KEYS
        }
    with _AVAILABILITY_LOCK:
        _AVAILABILITY_CACHE = (now, rows)
    return rows


def harness_cards(*, refresh: bool = False) -> tuple[HarnessCard, ...]:
    """All known cards with live, spawn-free availability attached."""
    rows = _platform_availability(refresh=refresh)
    cards = [_INTERNAL_CARD]
    for base in _BASE_EXTERNAL_CARDS:
        row = rows.get(base.name, {})
        cards.append(
            replace(
                base,
                installed=bool(row.get("installed")),
                run_supported=bool(row.get("run_supported")),
                registered=row.get("registered"),
            )
        )
    return tuple(cards)


def _variants(token: str) -> tuple[str, ...]:
    """Small deterministic morphology for common English task words."""
    if len(token) < 5:
        return ()
    variants: list[str] = []
    for suffix, keep in (("ing", 3), ("ed", 2), ("es", 2), ("s", 1)):
        if token.endswith(suffix) and len(token) - len(suffix) >= keep:
            variant = token[: -len(suffix)]
            if variant not in variants:
                variants.append(variant)
    return tuple(variants)


def _tokens(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for raw in _WORD_RE.findall(str(text or "").lower()):
        token = raw.strip(".-")
        if len(token) < 2 or token in _STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
        for variant in _variants(token):
            if variant not in _STOPWORDS and variant not in tokens:
                tokens.append(variant)
    return tuple(tokens)


@lru_cache(maxsize=512)
def _embed(text: str) -> tuple[float, ...]:
    """Signed hashed local vector: word, character-trigram, and bigram features.

    This is deterministic, dependency-free, and local. It is a vector-space
    embedding, not a provider call or a downloaded model.
    """
    vector = [0.0] * _VECTOR_DIMENSIONS
    tokens = _tokens(text)

    def add(feature: str, weight: float) -> None:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % _VECTOR_DIMENSIONS
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign * weight

    for token in tokens:
        add("w:" + token, 1.0)
        if len(token) >= 4:
            for offset in range(len(token) - 2):
                add("c:" + token[offset : offset + 3], 0.22)
    for left, right in zip(tokens, tokens[1:]):
        add("b:" + left + ":" + right, 0.42)
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0.0:
        return tuple(vector)
    return tuple(value / norm for value in vector)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def _keyword_score(query_tokens: set[str], card: HarnessCard) -> float:
    card_tokens = set(_tokens(" ".join(card.keywords)))
    if not query_tokens or not card_tokens:
        return 0.0
    matches = len(query_tokens & card_tokens)
    # Two distinct capability keywords are enough to signal a match; one is
    # weak evidence. The cap keeps breadth from dominating family/class facts.
    return min(1.0, matches / 2.0)


def _score_card(
    descriptor: TaskDescriptor,
    query: str,
    query_vector: Sequence[float],
    query_tokens: set[str],
    card: HarnessCard,
) -> RouteCandidate:
    vector = _cosine(query_vector, _embed(card.document()))
    keyword = _keyword_score(query_tokens, card)
    family = (
        len(set(descriptor.task_families) & set(card.task_families))
        / max(1, len(descriptor.task_families))
        if descriptor.task_families
        else 0.0
    )
    capability = (
        len(set(descriptor.requested_capabilities) & set(card.capabilities))
        / max(1, len(descriptor.requested_capabilities))
        if descriptor.requested_capabilities
        else 0.0
    )
    class_match = 1.0 if descriptor.task_class in card.task_classes else 0.0
    score = (
        0.38 * vector
        + 0.22 * keyword
        + 0.18 * class_match
        + 0.14 * family
        + 0.08 * capability
    )
    reasons = []
    if class_match:
        reasons.append(f"class={descriptor.task_class}")
    if family:
        reasons.append(f"family={family:.2f}")
    if capability:
        reasons.append(f"capability={capability:.2f}")
    if keyword:
        reasons.append(f"keywords={keyword:.2f}")
    if not reasons:
        reasons.append("lexical only")
    return RouteCandidate(
        name=card.name,
        label=card.label,
        score=round(score, 6),
        vector=vector,
        keyword=keyword,
        family=family,
        capability=capability,
        available=card.available,
        run_supported=card.run_supported,
        reason=", ".join(reasons),
    )


def _complexity_label(prompt: str, descriptor: TaskDescriptor) -> str:
    try:
        from machina.controller.complexity import score_complexity

        return str(score_complexity(prompt).get("level") or "single")
    except Exception:
        return "subagents" if len(prompt) > 800 or len(descriptor.task_families) > 2 else "single"


def _merge_families(existing: Sequence[str], added: Sequence[str]) -> tuple[str, ...]:
    combined = set(existing) | set(added)
    return tuple(family for family in TASK_FAMILIES if family in combined)


def _merge_capabilities(
    existing: Sequence[str], added: Sequence[str]
) -> tuple[str, ...]:
    merged = list(existing)
    for capability in added:
        if capability not in merged:
            merged.append(capability)
    return tuple(merged)


def _infer_routing_descriptor(prompt: str, descriptor: TaskDescriptor) -> TaskDescriptor:
    """Fill descriptor gaps from bounded routing cues.

    The shared intent classifier is intentionally conservative about short
    imperatives ("optimize the token estimator") and follow-ons ("update the
    docs"). The meta router needs enough family/class signal to compare
    harnesses, so it adds only the missing family/class/capability facts here.
    Conversational turns are never changed, and explicit caller task classes
    bypass this function.
    """
    if descriptor.task_class == "conversational":
        return descriptor
    tokens = set(_tokens(prompt))
    review_action = bool(tokens & _REVIEW_ACTIONS)
    research = bool(tokens & _RESEARCH_ANCHORS)
    documentation = bool(tokens & _DOCUMENTATION_ANCHORS) or len(
        tokens & _DOCUMENTATION_CUES
    ) >= 2
    # A documentation request can name API endpoints, React, or a schema and
    # still be a writing task; only an actual coding verb makes the code
    # harness the better first route. Research anchors win even with a verb
    # ("research how to implement ...") because the requested output is still
    # synthesis, not an edit.
    strong_code = bool(tokens & _CODING_ACTIONS)
    coding_signals = len(tokens & _CODE_CONTEXT)

    if review_action:
        return replace(
            descriptor,
            task_class="review-readonly",
            task_families=_merge_families(descriptor.task_families, ("coding", "review")),
            requested_capabilities=_merge_capabilities(
                descriptor.requested_capabilities, ("review",)
            ),
        )

    research_explicit = bool(tokens & {"investigate", "literature", "pubmed", "research"})
    if research_explicit or (research and not strong_code) or (
        documentation and not strong_code
    ):
        capabilities: list[str] = []
        if research:
            capabilities.append("research")
        if documentation:
            capabilities.append("documentation")
        return replace(
            descriptor,
            task_class="explain",
            task_families=("research",),
            requested_capabilities=_merge_capabilities(
                descriptor.requested_capabilities, capabilities
            ),
        )

    # "Summarize this error message" or "What is the capital of France?" is an
    # explanation request, not an imperative to hand to a coding harness. Keep
    # the descriptor informational unless a real code action/noun is present;
    # generic error/stack words alone are not enough.
    informational = bool(tokens & {"describe", "explain", "summarize", "summary", "what", "why"})
    if (
        informational
        and not tokens & _CODING_ACTIONS
        and not tokens & (_CODE_CONTEXT - {"error", "stack", "trace"})
    ):
        return replace(descriptor, task_class="explain", task_families=())

    if coding_signals >= 2 or any(token in _CODING_ACTIONS for token in tokens):
        task_class = (
            "debug"
            if tokens
            & {"bug", "debug", "error", "pytest", "shell", "spawn", "stack", "timeout", "trace"}
            else "implement"
        )
        capabilities = ["testing"] if tokens & {"pytest", "test", "testing", "tests"} else []
        return replace(
            descriptor,
            task_class=task_class,
            task_families=_merge_families(descriptor.task_families, ("coding",)),
            requested_capabilities=_merge_capabilities(
                descriptor.requested_capabilities, capabilities
            ),
        )

    return descriptor


def _descriptor(
    prompt: str,
    *,
    task_state: Mapping[str, Any] | None,
    recent: Sequence[Mapping[str, Any]] | None,
    goal: str | None,
    task_class: str | None,
) -> TaskDescriptor:
    descriptor = build_task_descriptor(
        prompt,
        task_state=task_state,
        recent=recent,
        goal=goal,
    )
    if task_class in TASK_CLASSES:
        if descriptor.task_class != task_class:
            descriptor = replace(descriptor, task_class=task_class)
        return descriptor
    return _infer_routing_descriptor(prompt, descriptor)


def _candidate_supported(
    candidate: RouteCandidate,
    descriptor: TaskDescriptor,
    cards: Sequence[HarnessCard],
) -> bool:
    """Whether a harness card shares the descriptor's task class or family."""
    card = next((entry for entry in cards if entry.name == candidate.name), None)
    if card is None:
        return False
    return bool(set(descriptor.task_families) & set(card.task_families)) or (
        descriptor.task_class in card.task_classes
    )


def _route_one(
    prompt: str,
    descriptor: TaskDescriptor,
    cards: Sequence[HarnessCard],
    *,
    top_n: int = 3,
) -> tuple[str, str, float, str, tuple[RouteCandidate, ...]]:
    """Route one clause. Returns primary, mode, confidence, reason, candidates."""
    external = [card for card in cards if card.kind == "external"]
    query_tokens = set(_tokens(prompt))
    query_vector = _embed(prompt)
    candidates = tuple(
        sorted(
            (
                _score_card(descriptor, prompt, query_vector, query_tokens, card)
                for card in external
            ),
            key=lambda candidate: (-candidate.score, candidate.name),
        )[:top_n]
    )
    available = [candidate for candidate in candidates if candidate.available]
    if descriptor.task_class == "conversational" or not available:
        return (
            "machina",
            "internal",
            0.0,
            "Conversational turn or no runnable external harness is available.",
            candidates,
        )
    top = available[0]
    second_score = available[1].score if len(available) > 1 else 0.0
    margin = top.score - second_score
    # A close race is only a reason to stay local when neither candidate has
    # class/family support. Two coding harnesses tying on a coding task is
    # still a valid external route; the alternate is surfaced in the hint.
    close_without_support = (
        top.score < 0.55
        and margin < _MIN_EXTERNAL_MARGIN
        and not _candidate_supported(top, descriptor, external)
    )
    if top.score < _MIN_EXTERNAL_SCORE or close_without_support:
        return (
            "machina",
            "internal",
            top.score,
            "No external harness clears the local confidence threshold.",
            candidates,
        )
    match_label = descriptor.task_families[0] if descriptor.task_families else descriptor.task_class
    return (
        top.name,
        "external",
        top.score,
        f"{top.label} is the best local match for {match_label}.",
        candidates,
    )


def _split_intents(prompt: str) -> tuple[str, ...]:
    parts = [part.strip() for part in _CLAUSE_RE.split(prompt) if part.strip()]
    if len(parts) <= 1:
        return (prompt.strip(),)
    merged: list[str] = []
    for part in parts:
        # A one-token fragment ("then test") is a continuation; two content
        # words ("update the docs") are a real second intent and must stay
        # separate so its own route can differ.
        if merged and len(_tokens(part)) < 2:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return tuple(merged[:_MAX_CLAUSES])


def _internal_route(
    prompt: str,
    descriptor: TaskDescriptor,
    candidates: tuple[RouteCandidate, ...] = (),
) -> MetaRoute:
    step = RouteStep(
        intent=prompt,
        task_class=descriptor.task_class,
        family=descriptor.task_families[0] if descriptor.task_families else "",
        primary="machina",
        mode="internal",
        confidence=0.0,
    )
    return MetaRoute(
        query=prompt,
        task_class=descriptor.task_class,
        task_families=descriptor.task_families,
        capabilities=descriptor.requested_capabilities,
        complexity=_complexity_label(prompt, descriptor),
        primary="machina",
        mode="internal",
        confidence=0.0,
        reason=(
            "The local Machina loop is the default; no external harness cleared "
            "the deterministic route threshold."
        ),
        candidates=candidates,
        steps=(step,),
    )


def route_prompt(
    prompt: str,
    *,
    task_state: Mapping[str, Any] | None = None,
    recent: Sequence[Mapping[str, Any]] | None = None,
    goal: str | None = None,
    task_class: str | None = None,
    refresh_availability: bool = False,
) -> MetaRoute:
    """Route one user prompt locally and deterministically.

    No provider call, no harness spawn, and no file scan. The result is
    guidance only. If the prompt contains multiple separable intents, each
    clause is routed and the plan keeps up to four deterministic steps.
    """
    clean = str(prompt or "").strip()
    if not clean or len(clean) > _MAX_PROMPT_CHARS:
        descriptor = _descriptor(
            clean[: _MAX_PROMPT_CHARS],
            task_state=task_state,
            recent=recent,
            goal=goal,
            task_class=task_class,
        )
        return _internal_route(clean, descriptor)
    cards = harness_cards(refresh=refresh_availability)
    clauses = _split_intents(clean)
    descriptor = _descriptor(
        clean,
        task_state=task_state,
        recent=recent,
        goal=goal,
        task_class=task_class,
    )
    if len(clauses) <= 1:
        primary, mode, confidence, reason, candidates = _route_one(
            clean, descriptor, cards
        )
        if mode != "external":
            return _internal_route(clean, descriptor, candidates)
        step = RouteStep(
            intent=clean,
            task_class=descriptor.task_class,
            family=descriptor.task_families[0] if descriptor.task_families else "",
            primary=primary,
            mode=mode,
            confidence=confidence,
        )
        return MetaRoute(
            query=clean,
            task_class=descriptor.task_class,
            task_families=descriptor.task_families,
            capabilities=descriptor.requested_capabilities,
            complexity=_complexity_label(clean, descriptor),
            primary=primary,
            mode=mode,
            confidence=confidence,
            reason=reason,
            candidates=candidates,
            steps=(step,),
        )

    steps: list[RouteStep] = []
    all_candidates: dict[str, RouteCandidate] = {}
    for clause in clauses:
        clause_descriptor = _descriptor(
            clause,
            task_state=task_state,
            recent=recent,
            goal=goal,
            task_class=None,
        )
        primary, mode, confidence, _reason, candidates = _route_one(
            clause, clause_descriptor, cards
        )
        for candidate in candidates:
            existing = all_candidates.get(candidate.name)
            if existing is None or candidate.score > existing.score:
                all_candidates[candidate.name] = candidate
        steps.append(
            RouteStep(
                intent=clause,
                task_class=clause_descriptor.task_class,
                family=(
                    clause_descriptor.task_families[0]
                    if clause_descriptor.task_families
                    else ""
                ),
                primary=primary,
                mode=mode,
                confidence=confidence,
            )
        )
    external_steps = [step for step in steps if step.mode == "external"]
    candidates = tuple(
        sorted(
            all_candidates.values(),
            key=lambda candidate: (-candidate.score, candidate.name),
        )[:4]
    )
    if not external_steps:
        return _internal_route(clean, descriptor, candidates)
    primary_step = max(external_steps, key=lambda step: step.confidence)
    return MetaRoute(
        query=clean,
        task_class=descriptor.task_class,
        task_families=descriptor.task_families,
        capabilities=descriptor.requested_capabilities,
        complexity=_complexity_label(clean, descriptor),
        primary=primary_step.primary,
        mode="external",
        confidence=primary_step.confidence,
        reason=(
            f"{len(steps)} deterministic intents routed; "
            f"{len(external_steps)} match a runnable external harness."
        ),
        candidates=candidates,
        steps=tuple(steps),
    )
