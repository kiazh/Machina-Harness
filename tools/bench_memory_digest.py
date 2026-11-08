"""Deterministic benchmark for the query-conditioned memory digest.

This is a synthetic regression bench, not a claim about a trained model. It
compares the pre-digest behavior (top-k rows by retrieval score) with the new
digest selector on the same candidate sets: required-fact coverage, character
cost, redundancy, and latency.

Run from the repository root:

    python tools/bench_memory_digest.py
"""

from __future__ import annotations

import json
import statistics
import time
import uuid
from dataclasses import dataclass

from machina.memory.digest import build_memory_digest
from machina.memory.models import MemoryEntry, RetrievedMemory


@dataclass(frozen=True)
class Scenario:
    name: str
    query: str
    required: tuple[str, ...]
    memories: tuple[tuple[str, float, str], ...]
    hard: bool = False


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="personal-breakfast",
        query="What breakfast fits my health history, preferences, and blood sugar?",
        required=("diabetes", "yogurt", "sugary"),
        memories=(
            ("User likes a high-protein breakfast.", 0.95, "preference"),
            ("User likes a high-protein breakfast.", 0.94, "preference"),
            ("The weather is warm today.", 0.30, "fact"),
            ("User has a family history of type 2 diabetes.", 0.82, "fact"),
            ("User prefers Greek yogurt with chia seeds.", 0.78, "preference"),
            ("User avoids sugary cereal in the morning.", 0.72, "preference"),
            ("User drinks orange juice on weekends.", 0.40, "preference"),
            ("The office has a new coffee machine.", 0.25, "event"),
        ),
    ),
    Scenario(
        name="stack",
        query="Which database, API, and frontend stack does the project use?",
        required=("postgresql", "fastapi", "redis", "frontend"),
        memories=(
            ("The project uses PostgreSQL with pgvector.", 0.93, "decision"),
            ("PostgreSQL stores the vector index.", 0.91, "fact"),
            ("The office coffee machine is on the third floor.", 0.30, "fact"),
            ("The API is written in Python with FastAPI.", 0.83, "decision"),
            ("Redis handles short-lived session caching.", 0.78, "decision"),
            ("The frontend uses React and TypeScript.", 0.72, "decision"),
            ("The team uses a shared calendar for releases.", 0.25, "event"),
        ),
    ),
    Scenario(
        name="travel",
        query="Plan a trip considering my budget, diet, transport, and airport stress.",
        required=("budget", "vegetarian", "train", "airport"),
        memories=(
            ("The user has a strict travel budget.", 0.90, "preference"),
            ("The user is vegetarian.", 0.86, "preference"),
            ("The user prefers train travel over flying.", 0.82, "preference"),
            ("The user likes window seats.", 0.70, "preference"),
            ("The user once visited Kyoto.", 0.65, "event"),
            ("The user dislikes crowded airports.", 0.62, "preference"),
            ("The user owns a blue suitcase.", 0.30, "fact"),
        ),
    ),
    Scenario(
        name="research",
        query="What do the papers say about latent memory, retrieval benchmarks, and latency?",
        required=("latent", "retrieval", "benchmark", "latency"),
        memories=(
            ("Memorilla compresses documents into query-conditioned latent memory.", 0.92, "fact"),
            ("The paper evaluates latent memory against RAG retrieval.", 0.88, "fact"),
            ("The benchmark includes multi-fact personalization questions.", 0.84, "fact"),
            ("Retrieval benchmarks often report recall at five documents.", 0.80, "fact"),
            ("A benchmark measures the latency of memory compression.", 0.70, "fact"),
            ("The lab moved to a new building.", 0.25, "event"),
        ),
    ),
    Scenario(
        name="project-review",
        query="Review the deployment, testing, API, and auth decisions.",
        required=("deployment", "testing", "api", "auth"),
        memories=(
            ("Deployment runs in Docker on a single host.", 0.91, "decision"),
            ("The deployment pipeline requires a manual approval.", 0.88, "decision"),
            ("Testing uses pytest and a real PostgreSQL instance.", 0.85, "decision"),
            ("The API is versioned under /v1.", 0.80, "fact"),
            ("The API rejects requests without an auth token.", 0.75, "fact"),
            ("The team uses a shared calendar for releases.", 0.25, "event"),
        ),
    ),
    Scenario(
        name="preferences",
        query="What breakfast, coffee, and decaf does the user like?",
        required=("breakfast", "coffee", "decaf"),
        memories=(
            ("The user likes a savory breakfast.", 0.93, "preference"),
            ("The user likes a savory breakfast with eggs.", 0.91, "preference"),
            ("The user drinks oat milk flat whites.", 0.84, "preference"),
            ("The user avoids coffee after noon.", 0.79, "preference"),
            ("The user keeps decaf beans for evenings.", 0.60, "preference"),
            ("The user owns a blue bicycle.", 0.25, "fact"),
        ),
    ),
    Scenario(
        name="hard-constraints",
        query="Summarize the database, API, deployment, testing, security, documentation, and frontend constraints.",
        required=("postgresql", "fastapi", "docker", "pytest", "security", "documentation", "react"),
        memories=(
            ("The project uses PostgreSQL for persistence.", 0.95, "decision"),
            ("PostgreSQL is the primary database.", 0.94, "fact"),
            ("The database is PostgreSQL with pgvector.", 0.93, "decision"),
            ("The API uses FastAPI and Python.", 0.62, "decision"),
            ("Deployment runs in Docker.", 0.60, "decision"),
            ("Testing uses pytest.", 0.58, "decision"),
            ("Security review is required before release.", 0.56, "decision"),
            ("Documentation lives in the docs directory.", 0.54, "fact"),
            ("The frontend uses React.", 0.52, "decision"),
            ("The team has a shared calendar.", 0.20, "event"),
        ),
        hard=True,
    ),
    Scenario(
        name="hard-personal",
        query="What should I know about breakfast, coffee, travel, budget, work, and family?",
        required=("breakfast", "coffee", "travel", "budget", "work", "family"),
        memories=(
            ("The user likes a high-protein breakfast.", 0.95, "preference"),
            ("The user likes a high-protein breakfast with eggs.", 0.94, "preference"),
            ("The user drinks oat milk flat whites.", 0.61, "preference"),
            ("The user prefers train travel.", 0.59, "preference"),
            ("The user has a strict travel budget.", 0.57, "preference"),
            ("The user works remotely on weekdays.", 0.55, "fact"),
            ("The user has a family history of diabetes.", 0.53, "fact"),
            ("The user owns a blue bicycle.", 0.20, "fact"),
        ),
        hard=True,
    ),
    Scenario(
        name="hard-research",
        query="Compare latent memory, retrieval, benchmarks, latency, personalization, and sources.",
        required=("latent", "retrieval", "benchmark", "latency", "personalization", "sources"),
        memories=(
            ("Memorilla uses latent memory tokens.", 0.95, "fact"),
            ("Latent memory is the core of the method.", 0.94, "fact"),
            ("The method compresses documents into latent memory.", 0.93, "fact"),
            ("The paper evaluates retrieval against RAG.", 0.63, "fact"),
            ("The benchmark includes personalization questions.", 0.60, "fact"),
            ("The latency stays fixed as the collection grows.", 0.57, "fact"),
            ("The sources are cited in the report.", 0.54, "fact"),
            ("The lab moved to a new building.", 0.20, "event"),
        ),
        hard=True,
    ),
)


def _entry(content: str, score: float, category: str) -> RetrievedMemory:
    return RetrievedMemory(
        memory=MemoryEntry(
            id=uuid.uuid5(uuid.NAMESPACE_URL, content),
            session_id=None,
            agent_id="bench",
            content=content,
            category=category,
            importance=0.5,
        ),
        score=score,
    )


def _coverage(memories: list[RetrievedMemory], required: tuple[str, ...]) -> float:
    text = " ".join(memory.memory.content.lower() for memory in memories)
    return sum(1 for term in required if term in text) / len(required)


def _redundancy(memories: list[RetrievedMemory]) -> float:
    token_sets = [set(memory.memory.content.lower().split()) for memory in memories]
    scores: list[float] = []
    for index, left in enumerate(token_sets):
        for right in token_sets[index + 1 :]:
            union = left | right
            if union:
                scores.append(len(left & right) / len(union))
    return statistics.mean(scores) if scores else 0.0


def main() -> int:
    rows: list[dict] = []
    for scenario in SCENARIOS:
        candidates = [
            _entry(content, score, category)
            for content, score, category in scenario.memories
        ]
        naive = sorted(candidates, key=lambda memory: (-memory.score, memory.memory.content))[:5]
        digest = build_memory_digest(
            candidates,
            scenario.query,
            max_items=6,
            max_chars=900,
        )
        selected = list(digest.selected)
        rows.append(
            {
                "name": scenario.name,
                "hard": scenario.hard,
                "naive_coverage": round(_coverage(naive, scenario.required), 3),
                "digest_coverage": round(_coverage(selected, scenario.required), 3),
                "naive_chars": sum(len(memory.memory.content) for memory in naive),
                "digest_chars": digest.char_count,
                "naive_coverage_per_1k": round(
                    _coverage(naive, scenario.required)
                    / max(1, sum(len(memory.memory.content) for memory in naive))
                    * 1000,
                    3,
                ),
                "digest_coverage_per_1k": round(
                    _coverage(selected, scenario.required)
                    / max(1, digest.char_count)
                    * 1000,
                    3,
                ),
                "naive_redundancy": round(_redundancy(naive), 3),
                "digest_redundancy": round(_redundancy(selected), 3),
                "digest_omitted": digest.omitted,
            }
        )

    started = time.perf_counter()
    iterations = 500
    for _ in range(iterations):
        build_memory_digest(
            [
                _entry(content, score, category)
                for content, score, category in SCENARIOS[0].memories
            ],
            SCENARIOS[0].query,
            max_items=6,
            max_chars=900,
        )
    p50_ms = (time.perf_counter() - started) * 1000.0 / iterations

    hard_rows = [row for row in rows if row["hard"]]
    easy_rows = [row for row in rows if not row["hard"]]
    hard_naive = statistics.mean(row["naive_coverage"] for row in hard_rows)
    hard_digest = statistics.mean(row["digest_coverage"] for row in hard_rows)
    naive_coverage = statistics.mean(row["naive_coverage"] for row in rows)
    digest_coverage = statistics.mean(row["digest_coverage"] for row in rows)
    naive_chars = sum(row["naive_chars"] for row in rows)
    digest_chars = sum(row["digest_chars"] for row in rows)
    naive_redundancy = statistics.mean(row["naive_redundancy"] for row in rows)
    digest_redundancy = statistics.mean(row["digest_redundancy"] for row in rows)
    output = {
        "scenarios": len(rows),
        "naive_coverage": round(naive_coverage, 3),
        "digest_coverage": round(digest_coverage, 3),
        "coverage_gain": round(digest_coverage - naive_coverage, 3),
        "hard_scenarios": len(hard_rows),
        "hard_naive_coverage": round(hard_naive, 3),
        "hard_digest_coverage": round(hard_digest, 3),
        "hard_coverage_gain": round(hard_digest - hard_naive, 3),
        "easy_scenarios": len(easy_rows),
        "naive_chars": naive_chars,
        "digest_chars": digest_chars,
        "char_reduction": round(1 - digest_chars / naive_chars, 3) if naive_chars else 0.0,
        "naive_redundancy": round(naive_redundancy, 3),
        "digest_redundancy": round(digest_redundancy, 3),
        "digest_p50_ms": round(p50_ms, 3),
        "provider_calls": 0,
        "rows": rows,
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
