"""The supported way to read a hidden turn traceback."""

from __future__ import annotations

from typer.testing import CliRunner

from machina.cli import debug
from machina.cli.common import EXIT_FAILED


def test_debug_logs_prints_requested_tail(tmp_path, monkeypatch) -> None:
    log = tmp_path / "gateway.log"
    log.write_text("old\nsecond\nlast\n", encoding="utf-8")
    monkeypatch.setattr(debug, "_paths", lambda: {"log": str(tmp_path)})

    result = CliRunner().invoke(debug.app, ["logs", "--lines", "2"])

    assert result.exit_code == 0
    assert "second" in result.stdout
    assert "last" in result.stdout
    assert "old" not in result.stdout


def test_debug_logs_rejects_path_names(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(debug, "_paths", lambda: {"log": str(tmp_path)})

    result = CliRunner().invoke(debug.app, ["logs", "--name", "../gateway.log"])

    assert result.exit_code == EXIT_FAILED
    assert "file name" in result.output


def test_debug_logs_reports_missing_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(debug, "_paths", lambda: {"log": str(tmp_path)})

    result = CliRunner().invoke(debug.app, ["logs"])

    assert result.exit_code == EXIT_FAILED
    assert "No log file" in result.output
