"""Completion scripts are generated from the installed lazy command table."""

from __future__ import annotations

import pytest

from machina.cli.completions import completion_script
from machina.cli.lazy import lazy_app


def test_bash_completion_uses_lazy_command_name() -> None:
    script = completion_script("bash", lazy_app)
    assert "_MACHINA_COMPLETE" in script
    assert "machina" in script


def test_fish_completion_is_available() -> None:
    script = completion_script("fish", lazy_app)
    assert "--command machina" in script


def test_unknown_shell_is_rejected() -> None:
    with pytest.raises(ValueError):
        completion_script("tcsh", lazy_app)
