"""A worker's no-edit instruction must not override the parent's build request."""

from __future__ import annotations

from machina.core.intent import classify_task
from machina.core.task_descriptor import build_task_descriptor


def test_parent_build_releases_worker_no_edit_constraint() -> None:
    prompt = (
        "Delegate an advisory task to the researcher; do not edit files. "
        "After the report, implement the page yourself and create index.html."
    )

    descriptor = build_task_descriptor(prompt)

    assert classify_task(prompt) == "implement"
    assert descriptor.task_class == "implement"
    assert descriptor.operation_scope == "mutating"
    assert "no-edit" not in descriptor.constraints
    assert "read-only" not in descriptor.constraints


def test_pure_readonly_review_keeps_the_constraint() -> None:
    descriptor = build_task_descriptor("Review the API and do not edit files.")

    assert descriptor.operation_scope == "read-only"
    assert "no-edit" in descriptor.constraints
