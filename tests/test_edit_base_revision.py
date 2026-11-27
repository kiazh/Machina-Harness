"""Approved writes are bound to the target bytes the human reviewed."""

from __future__ import annotations

import hashlib

import pytest

from machina.core.exceptions import ToolError
from machina.permissions import broker
from machina.permissions.policy import build_request
from machina.tools import file as file_tools


def _revision(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def test_digest_changes_when_the_target_changes(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("value = 1\n")

    first = build_request(operation="file.write", targets=[str(target)], content="new")
    target.write_text("value = 2\n")
    second = build_request(operation="file.write", targets=[str(target)], content="new")

    assert first.base_revision == _revision(b"value = 1\n")
    assert first.digest != second.digest


def test_missing_target_is_distinct_from_existing(tmp_path):
    target = tmp_path / "new.txt"

    missing = build_request(operation="file.write", targets=[str(target)], content="new")
    target.write_text("")
    existing = build_request(operation="file.write", targets=[str(target)], content="new")

    assert missing.base_revision == "missing"
    assert existing.base_revision == _revision(b"")
    assert missing.digest != existing.digest


def test_execution_refuses_changed_bytes(tmp_path, monkeypatch):
    target = tmp_path / "app.py"
    target.write_text("before\n")
    monkeypatch.setattr(
        broker,
        "get_execution_context",
        lambda: {"base_revision": _revision(b"before\n")},
    )
    file_tools._verify_base_revision(target)

    target.write_text("after\n")
    with pytest.raises(ToolError, match="changed after the action was approved"):
        file_tools._verify_base_revision(target)


def test_execution_refuses_a_new_file_created_after_review(tmp_path, monkeypatch):
    target = tmp_path / "new.txt"
    monkeypatch.setattr(broker, "get_execution_context", lambda: {"base_revision": "missing"})

    target.write_text("someone else\n")
    with pytest.raises(ToolError):
        file_tools._verify_base_revision(target)
