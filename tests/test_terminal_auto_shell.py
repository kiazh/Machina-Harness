"""Windows defaults compound commands to Git Bash when it is installed."""

from __future__ import annotations

from machina.permissions import shell


def test_windows_compound_commands_auto_select_git_bash(monkeypatch) -> None:
    monkeypatch.setattr(shell.os, "name", "nt")
    monkeypatch.delenv("MACHINA_TERMINAL_SHELL", raising=False)
    monkeypatch.setattr(shell, "_bash_executable", lambda: r"C:\Program Files\Git\bin\bash.exe")
    parsed = shell.parse_terminal_command("git diff --stat; git diff -- calc.py")
    assert parsed.shell == "bash"
    assert parsed.argv[:2] == [r"C:\Program Files\Git\bin\bash.exe", "-c"]


def test_simple_windows_commands_stay_shell_free(monkeypatch) -> None:
    monkeypatch.setattr(shell.os, "name", "nt")
    parsed = shell.parse_terminal_command("git status --short")
    assert parsed.shell == ""
    assert parsed.argv == ["git", "status", "--short"]
