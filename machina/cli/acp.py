"""Editor-facing ACP transport over stdin/stdout."""

from __future__ import annotations

import sys

import typer

from machina.core.session_mode import MODES


def acp(
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="Initial session mode: ask|workspace|sandbox|full (default: configured)",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Start ACP sessions in full host mode (equivalent to --mode full)",
    ),
) -> None:
    """Run Machina as an Agent Client Protocol agent for editors such as Zed."""
    selected = "full" if auto else mode
    if selected is not None and selected not in MODES:
        sys.stderr.write(f"mode must be one of: {', '.join(MODES)}\n")
        raise typer.Exit(2)
    from machina.acp_bridge import main

    try:
        main(default_mode=selected)
    except KeyboardInterrupt:
        raise typer.Exit(130) from None
    except Exception as error:
        # stdout is the ACP JSON-RPC channel. Diagnostics belong on stderr.
        sys.stderr.write(f"machina acp failed: {type(error).__name__}: {error}\n")
        raise typer.Exit(1) from None
