"""The no-subcommand path must reach the UI without importing the full CLI."""

import os
from pathlib import Path

from machina import update
from machina.entrypoint import _interactive_mode
from machina.ui_launcher import ui_dir


def test_no_arguments_launches_the_ui_directly() -> None:
    assert _interactive_mode([]) == (True, None)


def test_global_mode_is_part_of_the_ui_fast_path() -> None:
    assert _interactive_mode(["--mode", "sandbox"]) == (True, "sandbox")
    assert _interactive_mode(["--mode=full"]) == (True, "full")


def test_subcommands_help_and_unknown_flags_use_the_full_cli() -> None:
    assert _interactive_mode(["chat", "hello"]) == (False, None)
    assert _interactive_mode(["version"]) == (False, None)
    assert _interactive_mode(["--help"]) == (False, None)
    assert _interactive_mode(["--mode", "bogus"]) == (False, None)
    assert _interactive_mode(["--mode"]) == (False, None)


def test_ui_dir_points_at_the_repository_ui_package() -> None:
    assert ui_dir().name == "ui"
    assert (ui_dir() / "src" / "main.ts").is_file()


def test_recent_update_check_skips_the_network(tmp_path: Path) -> None:
    marker = tmp_path / ".git" / update._CHECK_MARKER
    marker.parent.mkdir()
    marker.touch()

    assert update._check_is_fresh(tmp_path)


def test_stale_update_marker_does_not_skip_the_network(tmp_path: Path) -> None:
    marker = tmp_path / ".git" / update._CHECK_MARKER
    marker.parent.mkdir()
    marker.write_text("old", encoding="utf-8")
    old = marker.stat().st_mtime - update._CHECK_INTERVAL_SECONDS - 1
    os.utime(marker, (old, old))

    assert not update._check_is_fresh(tmp_path)
