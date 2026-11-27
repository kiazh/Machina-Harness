from __future__ import annotations

from types import SimpleNamespace

import pytest

from machina.core.exceptions import ToolError
from machina.tools import apple


@pytest.mark.asyncio
async def test_apple_mail_draft_uses_argv_and_never_sends(monkeypatch) -> None:
    seen: dict = {}

    monkeypatch.setattr(apple.shutil, "which", lambda name: "/usr/bin/osascript")

    def fake_run(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout="12345\n", stderr="")

    monkeypatch.setattr(apple.subprocess, "run", fake_run)

    result = await apple.apple_mail_draft("a@example.com", "Hello", "Body")
    assert "draft" in result.lower()
    assert "12345" in result
    assert seen["command"][-3:] == ["a@example.com", "Hello", "Body"]
    assert "send" not in " ".join(seen["command"]).lower()


@pytest.mark.asyncio
async def test_apple_mail_draft_rejects_bad_recipient() -> None:
    with pytest.raises(ToolError):
        await apple.apple_mail_draft("not-an-email", "Hello", "Body")


@pytest.mark.asyncio
async def test_apple_calendar_agenda_is_read_only(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(apple.shutil, "which", lambda name: "/usr/bin/osascript")

    def fake_run(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout="2026-11-27T10:00:00.000Z  Standup  Work\n", stderr="")

    monkeypatch.setattr(apple.subprocess, "run", fake_run)
    result = await apple.apple_calendar_agenda("2026-11-27T00:00:00", "2026-11-28T00:00:00")

    assert "Standup" in result
    assert "save" not in " ".join(seen["command"]).lower()
    assert "delete" not in " ".join(seen["command"]).lower()
