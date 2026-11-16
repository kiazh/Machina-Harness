"""Loopback classification is for client targets, not wildcard binds."""

from __future__ import annotations

import pytest

from machina.security.providers import is_loopback_base_url


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://[::1]:11434/v1",
    ],
)
def test_loopback_targets_are_exempt_from_plaintext_gate(base_url: str) -> None:
    assert is_loopback_base_url(base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://0.0.0.0:11434/v1",
        "http://example.com/v1",
    ],
)
def test_wildcard_and_remote_targets_are_not_loopback(base_url: str) -> None:
    assert not is_loopback_base_url(base_url)
