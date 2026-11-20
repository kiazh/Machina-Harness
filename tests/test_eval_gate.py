"""Baseline evaluation gate."""

from __future__ import annotations

import json
from pathlib import Path

from tools.eval_gate import compare_metrics, flatten_metrics, main


def test_flatten_only_numeric_leaves() -> None:
    assert flatten_metrics({"a": 1, "nested": {"b": 0.5, "text": "x"}, "flag": True}) == {
        "a": 1.0,
        "nested.b": 0.5,
    }


def test_regression_is_reported_with_tolerance() -> None:
    failures = compare_metrics(
        {"answer": 0.8, "recall": 0.9},
        {"answer": 0.78, "recall": 0.7},
        {"answer": 0.03},
    )
    assert failures == ["recall: 0.7 < baseline 0.9 (tolerance 0)"]


def test_missing_and_required_metrics_fail_closed() -> None:
    failures = compare_metrics(
        {"answer": 0.8},
        {"other": 1.0},
        required={"recall": 0.6},
    )
    assert "answer: missing from current metrics" in failures
    assert "recall: required metric missing" in failures


def test_cli_writes_a_machine_readable_result(tmp_path: Path, capsys) -> None:
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    baseline.write_text(json.dumps({"quality": 0.8}), encoding="utf-8")
    current.write_text(json.dumps({"quality": 0.7}), encoding="utf-8")
    code = main(
        [
            "--baseline",
            str(baseline),
            "--current",
            str(current),
            "--tolerance",
            "quality=0.15",
            "--json",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
