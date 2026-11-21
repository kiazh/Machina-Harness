"""Repair turns must not fetch external source or package indexes."""

from __future__ import annotations

from machina.tools.terminal import _command_uses_external_network


def test_external_source_fetches_are_blocked() -> None:
    assert _command_uses_external_network(
        "curl -sS https://raw.githubusercontent.com/pytest-dev/pytest/7.2.0/x.py"
    )
    assert _command_uses_external_network("pip install pytest")
    assert _command_uses_external_network("git fetch origin")
    assert _command_uses_external_network("python -c 'import urllib.request'")


def test_local_source_and_tests_are_not_blocked() -> None:
    assert not _command_uses_external_network("git diff -- src/_pytest/pathlib.py")
    assert not _command_uses_external_network("pytest -q testing/acceptance_test.py")
    assert not _command_uses_external_network("curl -sS http://127.0.0.1:8000/health")
    assert not _command_uses_external_network("python -m pytest -q")
