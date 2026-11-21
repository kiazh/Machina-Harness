"""Test-only guards must not turn a repair request into a read-only review."""

from __future__ import annotations

from machina.core.intent import classify_task, detect_constraints


def test_do_not_edit_tests_does_not_make_the_task_read_only() -> None:
    prompt = (
        "Resolve the issue below. Do not edit tests. "
        "Run the relevant tests when useful. The tests are failing with an ImportError."
    )
    assert classify_task(prompt) == "debug"
    assert "read-only" not in detect_constraints(prompt)
    assert "no-edit" not in detect_constraints(prompt)


def test_do_not_edit_anything_still_means_read_only() -> None:
    prompt = "Review this module. Do not edit anything."
    assert classify_task(prompt) == "review-readonly"
    assert "read-only" in detect_constraints(prompt)
