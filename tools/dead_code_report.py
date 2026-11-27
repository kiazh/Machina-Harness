#!/usr/bin/env python3
"""Report private definitions whose name is never referenced elsewhere.

Advisory, not a CI gate: dynamic dispatch, decorators, and framework hooks can
make a real symbol look unreferenced. The report is a review list; deletion
still requires a call-site search and a green test run.

Usage:
    python tools/dead_code_report.py
    python tools/dead_code_report.py --json
    python tools/dead_code_report.py --include-public
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import tokenize
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_ROOTS = ("machina", "tools", "tests")
TS_ROOT = "ui/src"
_TS_NAME = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def _python_candidates() -> tuple[Counter[str], list[dict]]:
    counts: Counter[str] = Counter()
    definitions: list[dict] = []
    for root in PY_ROOTS:
        for path in (ROOT / root).rglob("*.py"):
            try:
                with path.open("rb") as handle:
                    for token in tokenize.tokenize(handle.readline):
                        if token.type == tokenize.NAME:
                            counts[token.string] += 1
            except (OSError, tokenize.TokenError, SyntaxError):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    definitions.append(
                        {
                            "path": str(path.relative_to(ROOT)),
                            "line": node.lineno,
                            "name": node.name,
                            "kind": "method" if node.name != node.name.lstrip("_") else "function",
                            "private": node.name.startswith("_") and not node.name.startswith("__"),
                        }
                    )
    return counts, definitions


def _ts_candidates() -> tuple[Counter[str], list[dict]]:
    counts: Counter[str] = Counter()
    definitions: list[dict] = []
    pattern = re.compile(
        r"^\t(private |protected )?(?:readonly )?(?:static )?(?:async )?"
        r"([A-Za-z_$][\w$]*)\s*[(<]"
    )
    for path in (ROOT / TS_ROOT).rglob("*.ts"):
        text = path.read_text(encoding="utf-8")
        counts.update(_TS_NAME.findall(text))
        if path.name.endswith(".test.ts"):
            continue
        for index, line in enumerate(text.splitlines(), 1):
            match = pattern.match(line)
            if not match:
                continue
            name = match.group(2)
            definitions.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "line": index,
                    "name": name,
                    "kind": "method",
                    "private": match.group(1) in {"private ", "protected "},
                }
            )
    return counts, definitions


def collect(include_public: bool = False) -> list[dict]:
    rows: list[dict] = []
    for label, (counts, definitions) in (
        ("python", _python_candidates()),
        ("typescript", _ts_candidates()),
    ):
        for item in definitions:
            if not include_public and not item["private"]:
                continue
            # A definition contributes at least one occurrence (its own name).
            # <=1 means no other textual reference outside the definition.
            if counts[item["name"]] <= 1:
                rows.append({**item, "language": label, "references": counts[item["name"]]})
    return sorted(rows, key=lambda row: (row["path"], row["line"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-public", action="store_true")
    args = parser.parse_args()
    rows = collect(include_public=args.include_public)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"{len(rows)} candidate(s) with no textual reference outside their definition")
    for row in rows:
        print(
            f"  {row['language']:10} {row['path']}:{row['line']:<5} "
            f"{row['kind']:8} {row['name']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
