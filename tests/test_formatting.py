"""Post-write formatter behavior is opt-in and never raises."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from machina.workspace import formatting


def test_formatter_off_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(formatting, "_configured", lambda: "off")
    target = tmp_path / "app.py"
    target.write_text("x=1\n", encoding="utf-8")
    assert formatting.format_file(target) is None
    assert target.read_text(encoding="utf-8") == "x=1\n"


def test_formatter_runs_detected_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "app.py"
    target.write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(formatting, "_configured", lambda: "auto")
    monkeypatch.setattr(
        formatting,
        "_command_for",
        lambda path: (
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('app.py').write_text('x = 1\\n')",
            ],
            "fake",
        ),
    )
    assert formatting.format_file(target) == "Formatted with fake"
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_formatter_failure_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "app.py"
    target.write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(formatting, "_configured", lambda: "auto")
    monkeypatch.setattr(
        formatting,
        "_command_for",
        lambda path: ([sys.executable, "-c", "raise SystemExit(3)"], "bad"),
    )
    result = formatting.format_file(target)
    assert result is not None and result.startswith("Formatter bad failed:")
