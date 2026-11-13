"""Compatibility shim for the interactive launcher.

The implementation moved to :mod:`machina.ui_launcher` so the no-subcommand
entry point can start the UI without importing the full Typer command surface.
Existing imports of ``machina.cli.launcher`` keep working.
"""

from machina.ui_launcher import MIN_NODE_VERSION, launch_ui, ui_dir

__all__ = ["MIN_NODE_VERSION", "launch_ui", "ui_dir"]
