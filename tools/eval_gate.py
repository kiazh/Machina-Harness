#!/usr/bin/env python3
"""Compare an evaluation result with a saved baseline and fail on regressions.

The gate is deliberately metric-shape agnostic: any JSON object can be used as
long as the compared leaves are numbers. External evaluation runners
(Promptfoo, Ragas, DeepEval) own their own pass/fail assertions; this script
owns the baseline contract for scripts that emit JSON metrics.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

__all__ = ["compare_metrics", "flatten_metrics", "load_metrics", "main"]


def flatten_metrics(value: Any, prefix: str = "") -> dict[str, float]:
    """Flatten numeric leaves; ignore counts/asymmetry keys that are not rates."""
    result: dict[str, float] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            result.update(flatten_metrics(child, name))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        result[prefix] = float(value)
    return result


def compare_metrics(
    baseline: dict[str, Any],
    current: dict[str, Any],
    tolerances: dict[str, float] | None = None,
    required: dict[str, float] | None = None,
) -> list[str]:
    """Return human-readable failures; an empty list means the gate passed."""
    tolerances = tolerances or {}
    required = required or {}
    old = flatten_metrics(baseline)
    new = flatten_metrics(current)
    failures: list[str] = []
    for metric, expected in old.items():
        tolerance = max(0.0, float(tolerances.get(metric, tolerances.get("*", 0.0))))
        if metric not in new:
            failures.append(f"{metric}: missing from current metrics")
            continue
        actual = new[metric]
        if actual < expected - tolerance:
            failures.append(
                f"{metric}: {actual:.6g} < baseline {expected:.6g} (tolerance {tolerance:.6g})"
            )
    for metric, floor in required.items():
        if metric not in new:
            failures.append(f"{metric}: required metric missing")
        elif new[metric] < floor:
            failures.append(f"{metric}: {new[metric]:.6g} < required {floor:.6g}")
    return failures


def load_metrics(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("metrics JSON must be an object")
    return value


def _parse_numeric_pairs(values: list[str]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for value in values:
        key, separator, raw = value.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"expected key=value, got {value!r}")
        parsed[key.strip()] = float(raw)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", help="committed baseline JSON")
    parser.add_argument("--current", help="current result JSON")
    parser.add_argument("--tolerance", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--require", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument(
        "--promptfoo-config",
        help="run Promptfoo instead of the JSON baseline (requires npx)",
    )
    parser.add_argument("--ragas", action="store_true", help="run evals/ragas_gate.py")
    parser.add_argument(
        "--deepeval",
        action="store_true",
        help="run the DeepEval suite in evals/test_agent.py",
    )
    args = parser.parse_args(argv)

    failures: list[str] = []
    if args.promptfoo_config:
        result = subprocess.run(
            ["npx", "--yes", "promptfoo@latest", "eval", "-c", args.promptfoo_config],
            check=False,
        )
        failures = [] if result.returncode == 0 else ["promptfoo: evaluation failed"]
    if args.ragas:
        result = subprocess.run([sys.executable, "evals/ragas_gate.py"], check=False)
        if result.returncode != 0:
            failures.append("ragas: evaluation failed")
    if args.deepeval:
        result = subprocess.run(
            [sys.executable, "-m", "deepeval", "test", "run", "evals/test_agent.py"],
            check=False,
        )
        if result.returncode != 0:
            failures.append("deepeval: evaluation failed")
    if not (args.promptfoo_config or args.ragas or args.deepeval):
        if not args.baseline or not args.current:
            parser.error("--baseline and --current are required for a metrics gate")
        try:
            failures = compare_metrics(
                load_metrics(args.baseline),
                load_metrics(args.current),
                _parse_numeric_pairs(args.tolerance),
                _parse_numeric_pairs(args.require),
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            failures = [f"gate input error: {error}"]

    payload = {"passed": not failures, "failures": failures}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif failures:
        print("evaluation gate failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
    else:
        print("evaluation gate passed")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
