#!/usr/bin/env python3
"""Measure the structural hotspots the decomposition plan is shrinking.

This is a ratchet, not a style score. It records the files and methods that
are deliberately being split, plus the internal import-cycle count. ``--check``
fails when a recorded file or method grows past its baseline. The baseline is
lowered by the task that actually removes code; it is never raised to make a
change pass.

Usage:
    python tools/architecture_report.py
    python tools/architecture_report.py --json
    python tools/architecture_report.py --check
    python tools/architecture_report.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / "architecture_baseline.json"
PYTHON_ROOTS = ("machina",)
TS_ROOTS = ("ui/src",)
# Only these files are ratcheted one-by-one. New modules are measured but not
# constrained until a task adds them to the baseline on purpose.
TRACKED = {
    "machina/core/agent.py",
    "machina/core/context_prep.py",
    "machina/core/llm_calls.py",
    "machina/core/tool_batch.py",
    "machina/core/tool_results.py",
    "machina/core/turn_state.py",
    "machina/core/react_phases.py",
    "machina/core/react_loop.py",
    "machina/core/post_turn.py",
    "machina/core/agent_support.py",
    "ui/src/app.ts",
    "ui/src/app/status.ts",
    "ui/src/app/changes.ts",
    "ui/src/app/approvals.ts",
    "ui/src/app/sessions.ts",
    "ui/src/app/events.ts",
    "ui/src/app/keyboard.ts",
    "ui/src/app/commands.ts",
    "ui/src/app/workspace.ts",
    "ui/src/app/bootstrap.ts",
    "ui/src/app/contracts.ts",
}


def _python_metrics(path: Path) -> tuple[int, dict[str, int], int]:
    source = path.read_text(encoding="utf-8")
    lines = source.count("\n") + (0 if source.endswith("\n") else 1)
    tree = ast.parse(source)
    methods: dict[str, int] = {}
    max_depth = 0

    def walk(node: ast.AST, depth: int) -> None:
        nonlocal max_depth
        max_depth = max(max_depth, depth)
        for child in ast.iter_child_nodes(node):
            walk(child, depth + 1)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods[node.name] = node.end_lineno - node.lineno + 1
    walk(tree, 0)
    return lines, methods, max_depth


_TS_METHOD = re.compile(
    r"^\t(?:private |public |protected |static |async |readonly |get |set )*"
    r"([A-Za-z_$][\w$]*)\s*\("
)


def _ts_metrics(path: Path) -> tuple[int, dict[str, int], int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [
        (index + 1, match.group(1))
        for index, line in enumerate(lines)
        if (match := _TS_METHOD.match(line))
    ]
    methods: dict[str, int] = {}
    for index, (start, name) in enumerate(starts):
        end = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(lines)
        # Last definition wins when overloads/duplicates exist; that is enough
        # for a size ratchet.
        methods[name] = end - start + 1
    return len(lines), methods, 0


def _python_edges() -> tuple[Counter[str], list[list[str]]]:
    modules: dict[str, Path] = {}
    for root in PYTHON_ROOTS:
        for path in (ROOT / root).rglob("*.py"):
            name = str(path.relative_to(ROOT))[:-3].replace("/", ".")
            if name.endswith(".__init__"):
                name = name[: -len(".__init__")]
            modules[name] = path
    edges: dict[str, set[str]] = {name: set() for name in modules}
    for name, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.startswith("machina"):
                    targets.append(node.module)
                    targets.extend(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Import):
                targets.extend(
                    alias.name for alias in node.names if alias.name.startswith("machina")
                )
            for target in targets:
                if target in modules:
                    edges[name].add(target)
    fan_in: Counter[str] = Counter()
    for deps in edges.values():
        for dep in deps:
            fan_in[dep] += 1
    return fan_in, _cycles(edges)


def _cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []
    counter = 0

    def visit(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for target in edges.get(node, ()):
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            component = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1:
                result.append(sorted(component))

    sys.setrecursionlimit(20_000)
    for node in edges:
        if node not in index:
            visit(node)
    return sorted(result, key=len, reverse=True)


def collect() -> dict:
    files: dict[str, dict] = {}
    for root in PYTHON_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            lines, methods, depth = _python_metrics(path)
            files[rel] = {"lines": lines, "methods": methods, "max_nesting": depth}
    for root in TS_ROOTS:
        for path in sorted((ROOT / root).rglob("*.ts")):
            if path.name.endswith(".test.ts"):
                continue
            rel = str(path.relative_to(ROOT))
            lines, methods, depth = _ts_metrics(path)
            files[rel] = {"lines": lines, "methods": methods, "max_nesting": depth}
    fan_in, cycles = _python_edges()
    return {
        "version": 1,
        "files": files,
        "cycles": cycles,
        "fan_in": dict(fan_in.most_common(20)),
    }


def _tracked(report: dict) -> dict:
    return {
        "files": {
            path: report["files"][path] for path in sorted(TRACKED) if path in report["files"]
        },
        "cycle_count": len(report["cycles"]),
    }


def check(report: dict) -> list[str]:
    if not BASELINE.exists():
        return [f"missing baseline: {BASELINE.relative_to(ROOT)}"]
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    problems: list[str] = []
    for path, recorded in baseline["files"].items():
        current = report["files"].get(path)
        if current is None:
            continue
        if current["lines"] > recorded["lines"]:
            problems.append(f"{path}: {current['lines']} lines > baseline {recorded['lines']}")
        for name, size in recorded["methods"].items():
            now = current["methods"].get(name)
            if now is not None and now > size:
                problems.append(f"{path}::{name}: {now} lines > baseline {size}")
    if len(report["cycles"]) > baseline.get("cycle_count", len(report["cycles"])):
        problems.append(
            f"import cycles: {len(report['cycles'])} > baseline {baseline['cycle_count']}"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the full report")
    parser.add_argument("--check", action="store_true", help="fail on ratchet growth")
    parser.add_argument("--write-baseline", action="store_true", help="replace the baseline")
    args = parser.parse_args()

    report = collect()
    tracked = _tracked(report)
    if args.write_baseline:
        BASELINE.write_text(json.dumps(tracked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE.relative_to(ROOT)}")
        return 0
    if args.check:
        problems = check(report)
        if problems:
            print("architecture ratchet failed:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print("architecture ratchet clean")
        return 0
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    files = sorted(report["files"].items(), key=lambda item: item[1]["lines"], reverse=True)
    print("Largest files")
    for path, data in files[:15]:
        print(f"  {data['lines']:5}  {path}")
    methods = [
        (path, name, size)
        for path, data in report["files"].items()
        for name, size in data["methods"].items()
    ]
    methods.sort(key=lambda item: item[2], reverse=True)
    print("\nLargest methods")
    for path, name, size in methods[:15]:
        print(f"  {size:5}  {path}::{name}")
    print(f"\nImport cycles: {len(report['cycles'])}")
    for cycle in report["cycles"][:5]:
        shown = " <-> ".join(cycle[:6])
        print(f"  {len(cycle):3}  {shown}{' ...' if len(cycle) > 6 else ''}")
    print("\nFan-in")
    for name, count in list(report["fan_in"].items())[:10]:
        print(f"  {count:4}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
