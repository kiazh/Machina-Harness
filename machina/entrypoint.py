"""Process entry point with a fast path for the interactive UI.

``machina`` with no subcommand is the common launch path. Importing the full
Typer CLI (every one-shot command, tool registration, DB/session/skill
helpers) costs seconds before the UI child can even spawn. This module
recognises the no-subcommand forms, runs the lightweight launcher, and only
imports :mod:`machina.cli` for real subcommands and help.
"""

from __future__ import annotations

import sys

_VALID_MODES = {"ask", "workspace", "sandbox", "full"}


def _interactive_mode(args: list[str]) -> tuple[bool, str | None]:
    """Return ``(is_ui_launch, mode)`` without importing Typer.

    Only the no-argument form and the global ``--mode`` option are fast-path
    UI launches. Anything else (``chat``, ``version``, ``--help``, unknown
    flags) goes through the normal Typer parser so behavior stays identical.
    """
    if not args:
        return True, None
    mode: str | None = None
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--mode":
            if index + 1 >= len(args):
                return False, None
            mode = args[index + 1]
            index += 2
        elif argument.startswith("--mode="):
            mode = argument.partition("=")[2]
            index += 1
        else:
            return False, None
    if mode is not None and mode.strip().lower() not in _VALID_MODES:
        return False, None
    return True, mode


def main() -> None:
    """Console-script entry point (``machina``)."""
    is_ui, mode = _interactive_mode(sys.argv[1:])
    if is_ui:
        from machina.update import auto_update

        auto_update()
        from machina.ui_launcher import launch_ui

        raise SystemExit(launch_ui(mode=mode))

    from machina.cli import app

    app()


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    main()
