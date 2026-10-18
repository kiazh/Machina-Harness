"""Quality guards for learned prompt compression.

The guard is deliberately strict: every path, URL, line reference, number,
quoted literal, and backticked symbol in an eligible span must survive. When
an embedding backend is available, semantic similarity must also clear the
configured floor. A rejected span is sent verbatim.
"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

__all__ = [
    "QualityResult",
    "anchor_retention",
    "cosine_similarity",
    "evaluate_compression",
    "extract_anchors",
]

_ANCHOR_PATTERNS = (
    re.compile(r"https?://[^\s)\]\"'`]+", re.IGNORECASE),
    re.compile(r"(?:\.{0,2}/|~/|[A-Za-z]:\\)[\w./\\-]+"),
    re.compile(r"\b[\w./\\-]+:\d+(?::\d+)?\b"),
    re.compile(r"`[^`\n]{1,200}`"),
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    re.compile(r"\b\d+(?:\.\d+)?\b"),
    # Code symbols: snake_case, dunder, namespace, or camelCase. Ordinary
    # prose words are not anchors; requiring every word would make any
    # compression impossible.
    re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b"),
    re.compile(r"\b[A-Za-z][A-Za-z0-9]*::[A-Za-z0-9_:]+\b"),
    re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b"),
    re.compile(r"\"[^\"\n]{2,120}\"|'[^'\n]{2,120}'"),
)


@dataclass(frozen=True)
class QualityResult:
    allowed: bool
    reason: str
    retention: float
    similarity: float | None = None


def extract_anchors(text: str) -> set[str]:
    """Extract high-value literals that compression must not drop."""
    anchors: set[str] = set()
    for pattern in _ANCHOR_PATTERNS:
        anchors.update(match.group(0) for match in pattern.finditer(text or ""))
    return anchors


def anchor_retention(original: str, compressed: str) -> float:
    anchors = extract_anchors(original)
    if not anchors:
        return 1.0
    kept = sum(1 for anchor in anchors if anchor in compressed)
    return kept / len(anchors)


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return dot / (left_norm * right_norm)


async def evaluate_compression(
    original: str,
    compressed: str,
    *,
    embed: Callable[[str], Awaitable[list[float] | None]] | None = None,
    min_anchor_retention: float = 1.0,
    min_similarity: float = 0.88,
) -> QualityResult:
    """Check anchor and semantic fidelity; fail closed on any doubt."""
    if not compressed.strip():
        return QualityResult(False, "empty", 0.0)
    retention = anchor_retention(original, compressed)
    if retention < min_anchor_retention:
        return QualityResult(False, "anchor_loss", retention)
    if embed is None:
        return QualityResult(True, "anchors_preserved", retention)
    try:
        original_vector = await embed(original)
        compressed_vector = await embed(compressed)
    except Exception:
        return QualityResult(False, "embedding_failed", retention)
    similarity = cosine_similarity(original_vector, compressed_vector)
    if similarity is None:
        return QualityResult(False, "embedding_unavailable", retention)
    if similarity < min_similarity:
        return QualityResult(False, "semantic_drift", retention, similarity)
    return QualityResult(True, "quality_ok", retention, similarity)
