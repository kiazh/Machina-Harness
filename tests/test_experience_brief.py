"""Bounded domain experience briefs from RSI evidence."""

from __future__ import annotations

import json

from machina.memory.experience import build_experience_brief
from machina.memory.rsi import aggregate, strategy_context


def test_experience_brief_reports_evidence_not_authority() -> None:
    brief = build_experience_brief(
        "coding",
        {
            "observations": 7,
            "tools": {"terminal": 1.12, "read_file": 1.05, "web_search": 0.9},
            "tool_failures": {"web_search": 2, "terminal": 0},
        },
    )

    assert "coding" in brief
    assert "7 observed episodes" in brief
    assert "terminal" in brief
    assert "read_file" in brief
    assert "web_search (2)" in brief
    assert "not authority" in brief
    assert len(brief) <= 700


def test_cold_start_or_malformed_payload_makes_no_claim() -> None:
    assert build_experience_brief("coding", {"observations": 0}) == ""
    assert build_experience_brief("coding", None) == ""
    assert build_experience_brief("coding", {"observations": "bad"}) == ""


def test_aggregate_keeps_domain_counts_and_failure_evidence() -> None:
    rows = [
        {
            "reward": 0.5,
            "automatic_reward": 0.2,
            "domain": "coding",
            "strategy": "verify",
            "skills": json.dumps(["testing-qa"]),
            "tools": json.dumps(
                [
                    {"name": "terminal", "ok": True},
                    {"name": "web_search", "ok": False},
                ]
            ),
        },
        {
            "reward": None,
            "automatic_reward": -0.25,
            "domain": "coding",
            "strategy": "verify",
            "skills": "[]",
            "tools": json.dumps([{"name": "terminal", "ok": True}]),
        },
    ]

    metrics = aggregate(rows)

    coding = metrics["domains"]["coding"]
    assert coding["observations"] == 2
    assert coding["tool_failures"]["web_search"] == 1
    assert coding["strategies"]["verify"] > 1.0
    assert "Domain experience: coding; 2 observed episodes" in strategy_context(
        {"domain": "coding", "strategy": "verify", "experience": build_experience_brief("coding", coding)}
    )
