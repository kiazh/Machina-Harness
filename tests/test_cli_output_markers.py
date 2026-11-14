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


def test_ensure_utf8_stream_reconfigures_legacy_windows_stream() -> None:
    class LegacyStream:
        encoding = "cp1252"

        def __init__(self) -> None:
            self.kwargs: dict[str, str] | None = None

        def reconfigure(self, **kwargs: str) -> None:
            self.kwargs = kwargs

    stream = LegacyStream()
    output._ensure_utf8_stream(stream, is_windows=True)

    assert stream.kwargs == {"encoding": "utf-8", "errors": "replace"}


def test_ensure_utf8_stream_leaves_utf8_and_non_windows_streams_alone() -> None:
    class Utf8Stream:
        encoding = "utf-8"

        def reconfigure(self, **_kwargs: str) -> None:
            raise AssertionError("must not reconfigure a UTF-8 stream")

    output._ensure_utf8_stream(Utf8Stream(), is_windows=True)

    class LegacyStream:
        encoding = "cp1252"

        def reconfigure(self, **_kwargs: str) -> None:
            raise AssertionError("must not reconfigure a non-Windows stream")

    output._ensure_utf8_stream(LegacyStream(), is_windows=False)
