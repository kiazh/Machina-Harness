"""Local benchmark for deterministic meta-harness routing.

Run from the repository root:

    python tools/bench_meta_harness.py

No provider is called. All harnesses are treated as installed so the result
is stable across machines; this measures the classifier, not CLI detection.
The suite is a hand-labeled regression set, not a statistical sample. It is
kept here so future weight/threshold changes have to face the same cases.
"""

from __future__ import annotations

import json
import statistics
import time

import machina.core.meta_harness as meta_harness

# prompt, expected mode, expected primary (None = any external), expected steps
_TUNING_SUITE: tuple[tuple[str, str, str | None, int], ...] = (
    ("Fix the failing pytest in src/router.py", "external", None, 1),
    ("Refactor the workspace service and run tests", "external", None, 1),
    ("Add a React accessibility fix to the settings dialog", "external", None, 1),
    ("Implement a new SQL migration for the sessions table", "external", None, 1),
    ("Debug the Windows shell spawn timeout", "external", None, 1),
    ("Optimize the token estimator hot path", "external", None, 1),
    ("Research the current Kiro credit pricing and summarize sources", "external", "hermes", 1),
    ("Compare local embedding models for a coding router", "external", "hermes", 1),
    ("Investigate how Codex headless mode handles approvals", "external", None, 1),
    ("Summarize the latest OpenCode release notes", "external", "hermes", 1),
    ("Review this checkout for accessibility issues", "external", None, 1),
    ("Audit the auth flow for security problems", "external", None, 1),
    ("Review the plan for fixing the failing test", "external", None, 1),
    ("Write documentation for the meta-harness router", "external", "hermes", 1),
    ("Draft a README section for harness delegation", "external", "hermes", 1),
    ("hello", "internal", "machina", 1),
    ("Explain how the auth flow works", "internal", "machina", 1),
    ("What can you do?", "internal", "machina", 1),
    ("Thanks, that worked", "internal", "machina", 1),
    ("Research Kiro pricing; then implement a React comparison page", "external", None, 2),
    ("Review the API and then update the docs", "external", None, 2),
    ("Fix the failing test; then summarize the root cause in the changelog", "external", None, 2),
)

_EXPANDED_SUITE: tuple[tuple[str, str, str | None, int], ...] = (
    ("Add pagination to the sessions endpoint", "external", None, 1),
    ("Why is the desktop build failing on Windows?", "external", None, 1),
    ("Profile the startup path and remove the slow import", "external", None, 1),
    ("Look up the current OpenCode release notes", "external", "hermes", 1),
    ("Write a migration guide for the new auth API", "external", None, 1),
    ("Compare Kiro and OpenCode for long coding tasks", "external", "hermes", 1),
    ("Review the CORS handling in the gateway", "external", None, 1),
    ("Explain the difference between a session and a workspace", "internal", "machina", 1),
    ("What's the weather?", "internal", "machina", 1),
    ("Please continue", "internal", "machina", 1),
    ("Fix the flaky desktop test and then document it in the changelog", "external", None, 2),
    ("Audit the SQL migration for injection risks", "external", None, 1),
    ("Summarize this error message: connection refused", "internal", "machina", 1),
    ("Draft a release note for the harness router", "external", "hermes", 1),
    ("Update the API docs for the new endpoint", "external", "hermes", 1),
    ("Implement a CLI flag and update the README", "external", None, 1),
    (
        "Research the best local embedding model and then write a comparison",
        "external",
        "hermes",
        2,
    ),
    ("Find the cause of the timeout in the test suite", "external", None, 1),
    ("Debug this stack trace", "external", None, 1),
    ("Write a poem about autumn", "internal", "machina", 1),
    ("What is the capital of France?", "internal", "machina", 1),
    ("Explain this function", "internal", "machina", 1),
)

_SUITES = (("tuning", _TUNING_SUITE), ("expanded", _EXPANDED_SUITE))


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def main() -> int:
    meta_harness._platform_availability = lambda refresh=False: {
        key: {"installed": True, "registered": None, "run_supported": True}
        for key in ("opencode", "codex", "hermes")
    }
    meta_harness.clear_availability_cache()

    cold_started = time.perf_counter()
    meta_harness.route_prompt("Fix the failing test")
    cold_ms = (time.perf_counter() - cold_started) * 1000.0

    latencies: list[float] = []
    rows: list[dict] = []
    suite_summary: dict[str, dict[str, int]] = {}
    for suite_name, suite in _SUITES:
        suite_correct = 0
        for prompt, expected_mode, expected_primary, expected_steps in suite:
            started = time.perf_counter()
            route = meta_harness.route_prompt(prompt)
            first_ms = (time.perf_counter() - started) * 1000.0
            for _ in range(2):
                warm_started = time.perf_counter()
                meta_harness.route_prompt(prompt)
                latencies.append((time.perf_counter() - warm_started) * 1000.0)
            latencies.append(first_ms)
            primary_ok = expected_primary is None or route.primary == expected_primary
            mode_ok = route.mode == expected_mode
            steps_ok = len(route.steps) == expected_steps
            ok = mode_ok and primary_ok and steps_ok
            suite_correct += int(ok)
            top = route.candidates[0] if route.candidates else None
            rows.append(
                {
                    "suite": suite_name,
                    "prompt": prompt,
                    "mode": route.mode,
                    "primary": route.primary,
                    "confidence": round(route.confidence, 3),
                    "steps": len(route.steps),
                    "ms": round(first_ms, 2),
                    "ok": ok,
                    "vector": round(top.vector, 3) if top else 0.0,
                    "keyword": round(top.keyword, 3) if top else 0.0,
                    "family": round(top.family, 3) if top else 0.0,
                    "capability": round(top.capability, 3) if top else 0.0,
                }
            )
        suite_summary[suite_name] = {"prompts": len(suite), "correct": suite_correct}

    correct = sum(1 for row in rows if row["ok"])
    external_rows = [row for row in rows if row["mode"] == "external"]
    internal_rows = [row for row in rows if row["mode"] == "internal"]
    output = {
        "prompts": len(rows),
        "correct": correct,
        "accuracy": round(correct / len(rows), 3),
        "external": len(external_rows),
        "internal": len(internal_rows),
        "suites": suite_summary,
        "cold_ms": round(cold_ms, 1),
        "p50_ms": round(statistics.median(latencies), 2),
        "p95_ms": round(_p95(latencies), 2),
        "provider_calls": 0,
        "provider_tokens": 0,
        "mean_top_vector": round(statistics.mean(row["vector"] for row in external_rows), 3),
        "mean_top_keyword": round(statistics.mean(row["keyword"] for row in external_rows), 3),
        "failures": [row for row in rows if not row["ok"]],
    }
    print(json.dumps(output, indent=2))
    for row in rows:
        print(
            f"{'PASS' if row['ok'] else 'FAIL'} [{row['suite']}] "
            f"ms={row['ms']:6.2f} mode={row['mode']:8s} "
            f"primary={row['primary']:8s} conf={row['confidence']:.2f} "
            f"steps={row['steps']} v={row['vector']:.2f} "
            f"k={row['keyword']:.2f} f={row['family']:.2f} "
            f"c={row['capability']:.2f} {row['prompt']}"
        )
    return 0 if correct == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
