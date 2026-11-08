"""CLI output markers must not crash on legacy Windows code pages."""

from __future__ import annotations

from types import SimpleNamespace

from machina.cli import output


def test_marker_falls_back_to_ascii_for_cp1252(monkeypatch) -> None:
    monkeypatch.setattr(
        output,
        "console",
        SimpleNamespace(file=SimpleNamespace(encoding="cp1252")),
    )

    assert output._marker("\u2713", "+") == "+"
    assert output._marker("\u2717", "x") == "x"


def test_marker_keeps_unicode_on_utf8(monkeypatch) -> None:
    monkeypatch.setattr(
        output,
        "console",
        SimpleNamespace(file=SimpleNamespace(encoding="utf-8")),
    )

    assert output._marker("\u2713", "+") == "\u2713"
