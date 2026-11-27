"""The installer and release workflow keep their fail-closed contracts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_fails_when_schema_is_not_ready() -> None:
    script = (ROOT / "install.sh").read_text(encoding="utf-8")

    marker = 'if [ "$INIT_STATE" = "failed" ]; then'
    assert marker in script
    block = script.split(marker, 1)[1].split("\nfi", 1)[0]
    assert "exit 1" in block
    assert "NOT created" in block


def test_release_workflow_checks_the_tag_and_runs_tests() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "Verify the tag matches the package version" in workflow
    assert "Run the repository smoke suite" in workflow
