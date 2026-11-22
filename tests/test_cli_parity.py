"""The OpenCode-style command names are registered and expose help."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from machina.cli._app import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.parametrize(
    "args",
    [
        ["run", "--help"],
        ["api", "--help"],
        ["acp", "--help"],
        ["completions", "--help"],
        ["models", "--help"],
        ["update", "--help"],
        ["upgrade", "--help"],
        ["session", "--help"],
        ["session", "list", "--help"],
        ["session", "diff", "--help"],
        ["session", "undo", "--help"],
        ["snapshots", "--help"],
        ["undo", "--help"],
        ["mcp", "--help"],
        ["plugin", "--help"],
        ["service", "--help"],
    ],
)
def test_parity_commands_are_registered(runner: CliRunner, args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output
