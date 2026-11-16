"""Doctor must remain runnable without a live database or harnesses."""

from __future__ import annotations

from unittest.mock import AsyncMock

from typer.testing import CliRunner

from machina import cli


def test_doctor_imports_node_version_from_the_real_launcher(monkeypatch) -> None:
    monkeypatch.setattr("machina.db.lifecycle.acquire", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("machina.db.lifecycle.release", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.db, "connect", AsyncMock())
    monkeypatch.setattr(cli.db, "fetchval", AsyncMock(return_value="PostgreSQL 18.4 test"))
    monkeypatch.setattr(cli.db, "close", AsyncMock())
    monkeypatch.setattr(cli.interop_mod, "detect", lambda: {})

    result = CliRunner().invoke(cli.app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "Machina Doctor" in result.output
    assert "Node.js:" in result.output
