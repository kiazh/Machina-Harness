"""The audit CLI is read-only and bounded."""

from __future__ import annotations

from typer.testing import CliRunner

from machina.cli import app
from machina.cli.audit import _parse_since


def test_audit_since_accepts_iso_timestamps() -> None:
    assert _parse_since("") is None
    parsed = _parse_since("2026-01-02T03:04:05Z")
    assert parsed is not None
    assert parsed.isoformat() == "2026-01-02T03:04:05+00:00"


def test_audit_help_exposes_limit_since_and_json() -> None:
    result = CliRunner().invoke(app, ["audit", "--help"])
    assert result.exit_code == 0
    assert "--limit" in result.stdout
    assert "--since" in result.stdout
    assert "--json" in result.stdout
