"""HTTP approval continuation rebuilds the exact reviewed action.

Regression: ``edit_file``'s action mapping carries review-only fields
(``old_text``/``count``) that are already folded into the digest-bound content.
Passing them straight into ``build_request`` raised ``TypeError`` and made an
approved edit impossible to resume over HTTP.
"""

from __future__ import annotations

import pytest

from machina.core import approval_resume
from machina.permissions.policy import build_request
from machina.permissions.tools import action_for_tool


class _Context:
    mode = "ask"
    backend = "host"
    workspace = "/tmp/machina-workspace"


async def _context(*_args, **_kwargs):
    return _Context()


@pytest.mark.asyncio
async def test_resumed_edit_file_does_not_pass_review_only_fields(monkeypatch):
    monkeypatch.setattr(approval_resume, "resolve_execution_context", _context)
    arguments = {
        "path": "src/app.py",
        "old_text": "value = 1",
        "new_text": "value = 2",
        "count": 1,
    }
    record = {
        "session_id": "00000000-0000-0000-0000-000000000001",
        "agent_id": "harness",
        "turn_id": "turn-1",
        "proposal": {"tool": "edit_file"},
    }

    request = await approval_resume.resumed_request(record, arguments)

    spec = action_for_tool("edit_file", arguments)
    expected = build_request(
        operation=spec["operation"],
        targets=spec["targets"],
        content=spec["content"],
        mode="ask",
        backend="host",
        workspace="/tmp/machina-workspace",
        tool="edit_file",
        agent_id="harness",
        session_id=record["session_id"],
        turn_id="turn-1",
    )
    assert request.digest == expected.digest
    assert request.operation == "file.write"
    assert request.targets[0].endswith("/src/app.py")


@pytest.mark.asyncio
async def test_resumed_terminal_keeps_argv_and_shell_payload(monkeypatch):
    monkeypatch.setattr(approval_resume, "resolve_execution_context", _context)
    arguments = {"command": "git status --short"}
    record = {
        "session_id": "00000000-0000-0000-0000-000000000002",
        "agent_id": "coder",
        "turn_id": "turn-2",
        "proposal": {"tool": "terminal"},
    }

    request = await approval_resume.resumed_request(record, arguments)

    assert request.operation == "process.exec"
    assert request.argv == ["git", "status", "--short"]


@pytest.mark.asyncio
async def test_resume_digest_changes_when_arguments_change(monkeypatch):
    monkeypatch.setattr(approval_resume, "resolve_execution_context", _context)
    record = {
        "session_id": "00000000-0000-0000-0000-000000000003",
        "agent_id": "harness",
        "turn_id": "turn-3",
        "proposal": {"tool": "write_file"},
    }

    first = await approval_resume.resumed_request(record, {"path": "notes.txt", "content": "one"})
    second = await approval_resume.resumed_request(record, {"path": "notes.txt", "content": "two"})

    assert first.digest != second.digest
