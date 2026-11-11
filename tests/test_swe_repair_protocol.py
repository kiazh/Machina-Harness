"""SWE issue-repair prompt protocol and project-environment preservation."""

from __future__ import annotations

import asyncio
import uuid

from machina.core import shell_jobs
from machina.core.assembler import PromptAssembler
from machina.core.child_process import sanitized_env
from machina.core.config import config
from machina.core.continuity import build_context_messages
from machina.core.models import Session
from machina.core.prompt import build_system_prompt, swe_repair_protocol
from machina.permissions import shell as shell_parser
from machina.soulspec.schema import SoulSpec
from machina.tools import builtins


def _soul_spec() -> SoulSpec:
    return SoulSpec(
        name="t",
        persona=SoulSpec.Persona(name="t", system_prompt="CORE IDENTITY"),
    )


def _build(message: str) -> str:
    session = Session(id=uuid.uuid4())
    session.context_budget = 8000
    return build_system_prompt(
        _soul_spec(),
        None,
        session,
        message,
        [],
        None,
        "skipped",
        {},
        [],
        set(),
    )


def test_repair_protocol_is_selected_for_a_code_issue() -> None:
    protocol = swe_repair_protocol("Fix the failing test in src/app.py")
    assert protocol
    assert "issue report as an actionable task" in protocol
    assert "related helper" in protocol
    assert "sibling public entry point" in protocol
    assert "do not add a new opt-in flag" in protocol
    assert "Inspect base classes" in protocol
    assert "values equal to the default" in protocol
    assert "before the separator" in protocol
    assert "combined field" in protocol
    assert "AssertionError" in protocol
    assert "negated form" in protocol
    assert "point of use" in protocol
    assert "both public attributes" in protocol


def test_repair_protocol_is_not_added_to_a_greeting() -> None:
    assert swe_repair_protocol("hello") == ""
    assert "Software repair protocol" not in _build("hello")


def test_repair_protocol_is_added_to_issue_prompts() -> None:
    assert "Software repair protocol" in _build("pytest --collect-only needs a shortcut")
    assert "Software repair protocol" in _build("Fix the traceback in src/app.py")


def test_repair_protocol_survives_system_prompt_truncation() -> None:
    protocol = swe_repair_protocol("Fix the failing test in src/app.py")
    messages = build_context_messages(
        "PERSONA " * 10000,
        None,
        [],
        PromptAssembler(),
        None,
        [],
        [],
        "q",
        0,
        [],
        8000,
        required_tail=protocol,
    )
    assert "Software repair protocol" in messages[0]["content"]
    assert "values equal to the default" in messages[0]["content"]


def test_sanitized_env_preserves_project_pythonpath(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "src")
    monkeypatch.setenv("VIRTUAL_ENV", "venv")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    env = sanitized_env()
    assert env["PYTHONPATH"] == "src"
    assert env["VIRTUAL_ENV"] == "venv"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_windows_launch_args_resolve_against_child_path(monkeypatch) -> None:
    monkeypatch.setattr(
        shell_jobs.shutil,
        "which",
        lambda name, path=None: rf"C:\task-venv\Scripts\{name}.exe" if name == "python" else None,
    )
    args = shell_jobs._resolve_windows_launch_args(
        ["python", "-m", "pytest"], {"PATH": r"C:\task-venv\Scripts"}, windows=True
    )
    assert args == [r"C:\task-venv\Scripts\python.exe", "-m", "pytest"]
    assert shell_jobs._resolve_windows_launch_args(["python"], {}, windows=False) == ["python"]


def test_terminal_parser_can_use_posix_shell_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(shell_parser.os, "name", "nt")
    monkeypatch.setattr(
        shell_parser,
        "_bash_executable",
        lambda: r"C:\Program Files\Git\bin\bash.exe",
    )
    monkeypatch.setenv("MACHINA_TERMINAL_SHELL", "bash")
    parsed = shell_parser.parse_terminal_command("python -m pytest; echo done")
    assert parsed.argv == [
        r"C:\Program Files\Git\bin\bash.exe",
        "-c",
        "python -m pytest; echo done",
    ]
    assert parsed.shell == "bash"


def test_terminal_parser_keeps_cmd_when_posix_shell_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(shell_parser.os, "name", "nt")
    monkeypatch.setattr(shell_parser, "_bash_executable", lambda: None)
    monkeypatch.setenv("MACHINA_TERMINAL_SHELL", "bash")
    parsed = shell_parser.parse_terminal_command("python -m pytest; echo done")
    assert parsed.argv[:4] == ["cmd.exe", "/d", "/s", "/c"]
    assert parsed.shell == "cmd"


def test_search_files_is_recursive(monkeypatch, tmp_path) -> None:
    root = tmp_path / "workspace"
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    (nested / "module.py").write_text("needle = 1\n", encoding="utf-8")
    monkeypatch.setattr(config, "workspace_root", str(root))

    result = asyncio.run(builtins.search_files("needle", "."))

    assert "Found 1 matches:" in result
    assert "src" in result and "pkg" in result and "module.py:1" in result
