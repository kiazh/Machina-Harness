"""The API client keeps auth in headers and rejects malformed header input."""

from __future__ import annotations

import pytest

from machina.cli.api import _headers


def test_api_headers_use_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MACHINA_API_KEY", "secret")
    headers = _headers(["X-Trace: abc"], None)
    assert headers["X-Trace"] == "abc"
    assert headers["X-API-Key"] == "secret"
    assert headers["Accept"] == "application/json"


def test_api_headers_reject_invalid_header() -> None:
    with pytest.raises(ValueError):
        _headers(["not-a-header"], "secret")
