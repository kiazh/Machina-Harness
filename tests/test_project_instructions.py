"""AGENTS.md project instructions are bounded, local, and non-authoritative."""

from __future__ import annotations

from pathlib import Path

from machina.core.project_instructions import (
    MAX_PROJECT_INSTRUCTION_BYTES,
    build_project_instruction_section,
    load_project_instructions,
)


def test_global_and_workspace_instructions_load_local_file_last(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    home.mkdir()
    workspace.mkdir()
    (home / "AGENTS.md").write_text("Use my personal commit style.", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("Run focused tests first.", encoding="utf-8")

    loaded = load_project_instructions(workspace=workspace, home=home)
    assert [item.source for item in loaded] == ["global", "workspace"]
    section = build_project_instruction_section(workspace=workspace, home=home)
    assert "Use my personal commit style." in section
    assert "Run focused tests first." in section
    assert "not as system policy" in section


def test_missing_agents_files_produce_no_prompt_section(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    home.mkdir()
    workspace.mkdir()
    assert load_project_instructions(workspace=workspace, home=home) == []
    assert build_project_instruction_section(workspace=workspace, home=home) == ""


def test_project_instructions_redact_secret_looking_text(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    home.mkdir()
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        "Never print sk-abcdefghijklmnopqrstuvwx.", encoding="utf-8"
    )
    section = build_project_instruction_section(workspace=workspace, home=home)
    assert "sk-abcdefghijklmnopqrstuvwx" not in section
    assert "[REDACTED" in section


def test_project_instruction_file_is_byte_bounded(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    home.mkdir()
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        "x" * (MAX_PROJECT_INSTRUCTION_BYTES + 100), encoding="utf-8"
    )
    loaded = load_project_instructions(workspace=workspace, home=home)
    assert len(loaded) == 1
    assert len(loaded[0].text.encode("utf-8")) <= MAX_PROJECT_INSTRUCTION_BYTES + 100
    assert "truncated" in loaded[0].text


def test_prompt_builder_accepts_project_instructions_without_loading_them(monkeypatch) -> None:
    import machina.core.prompt as prompt

    monkeypatch.setattr(prompt, "build_persona_section", lambda *a, **k: ("system", "persona"))
    monkeypatch.setattr(
        prompt, "build_capability_section", lambda *a, **k: ("system", "capability")
    )
    monkeypatch.setattr(prompt, "build_skill_section", lambda *a, **k: ("system", "skills"))
    monkeypatch.setattr(prompt, "output_policy_section", lambda: "")
    session = type("Session", (), {"context_budget": 8000})()

    base = prompt.build_system_prompt(
        None, None, session, "hello", [], None, "skipped", {}, [], set()
    )
    with_project = prompt.build_system_prompt(
        None,
        None,
        session,
        "hello",
        [],
        None,
        "skipped",
        {},
        [],
        set(),
        project_instructions="\n\n## Project Instructions\nFollow local conventions.",
    )
    assert base == "personacapabilityskills"
    assert (
        with_project
        == "personacapability\n\n## Project Instructions\nFollow local conventions.\n\nskills"
    )
