"""Launch the TypeScript terminal UI that lives in ``ui/``.

The UI is a Node.js program (built on ``@earendil-works/pi-tui``). It spawns
``python -m machina.gateway`` with the interpreter passed in ``MACHINA_PYTHON`` and talks
to it over JSON-RPC on stdio.

This module stays import-light: it is the first code the no-subcommand CLI
touches, so heavyweight command modules and optional lifecycle services are
imported only after the UI child process has started.
"""

from __future__ import annotations

import atexit
import os
import secrets
import shutil
import signal
import subprocess
import sys
from pathlib import Path

__all__ = ["MIN_NODE_VERSION", "launch_ui", "ui_dir"]

MIN_NODE_VERSION = (22, 19)


def _error(message: str, hint: str | None = None) -> None:
    """Render the shared CLI error only when a launch attempt fails."""
    from machina.cli import output

    output.error(message, hint)


def ui_dir() -> Path:
    """Location of the UI package (override with ``MACHINA_UI_DIR``)."""
    override = os.environ.get("MACHINA_UI_DIR")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[1] / "ui"


def _node_version(node: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
        return tuple(int(part) for part in out.stdout.strip().lstrip("v").split(".")[:2])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def launch_ui(
    *,
    model: str | None = None,
    provider: str | None = None,
    session_id: str | None = None,
    mode: str | None = None,
) -> int:
    """Run the UI in the foreground and return its exit code."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        _error(
            "The interactive UI needs a terminal.",
            'For scripts and pipes use one-shot mode: machina chat "your message"',
        )
        return 1

    directory = ui_dir()
    entry = directory / "src" / "main.ts"

    node = shutil.which("node")
    if node is None:
        _error(
            "Node.js is not installed.",
            "Install Node.js 22.19 or newer from https://nodejs.org, then run `machina` again.",
        )
        return 1
    version = _node_version(node)
    if version is None or version < MIN_NODE_VERSION:
        found = ".".join(map(str, version)) if version else "unknown"
        wanted = ".".join(map(str, MIN_NODE_VERSION))
        _error(f"Node.js {wanted}+ is required (found {found}).")
        return 1
    if not entry.exists():
        _error(
            f"UI not found at {directory}.",
            "Set MACHINA_UI_DIR to the ui/ folder of an Machina checkout.",
        )
        return 1
    if not (directory / "node_modules" / "@earendil-works" / "pi-tui").exists():
        _error(
            "UI dependencies are not installed.",
            f'Run: npm install --ignore-scripts --prefix "{directory}"',
        )
        return 1

    args = [node, "--disable-warning=ExperimentalWarning", str(entry)]
    if model:
        args += ["--model", model]
    if provider:
        args += ["--provider", provider]
    if session_id:
        args += ["--session", session_id]
    if mode:
        # LP-13: propagate the chosen mode to the gateway (previously dropped:
        # process-local config never reached the Node child). The gateway
        # applies it to its own process config (non-persistent).
        args += ["--mode", mode]

    # Generate a random auth token for the gateway. The UI client reads this
    # from MACHINA_GATEWAY_TOKEN and includes it in the initialize call.
    gateway_token = secrets.token_hex(32)
    env = {**os.environ, "MACHINA_PYTHON": sys.executable, "MACHINA_GATEWAY_TOKEN": gateway_token}
    # Transparent background (persisted config): export it so the UI applies the
    # setting before its first frame instead of flashing the opaque canvas. The
    # UI also re-applies the value from config.get, so a direct `npm start`
    # launch is still correct (just later).
    try:
        from machina.core.config import get_config

        if get_config().get("transparent_background"):
            env["MACHINA_TRANSPARENT_BACKGROUND"] = "1"
    except Exception:
        # Best-effort: a config read must never block the interactive UI.
        pass
    # The TUI is a long-lived DB client. Start the installer-owned container
    # if needed and release it only after the UI process has fully exited. A
    # concurrent `machina serve` keeps its own lease, so it prevents an early
    # database stop.
    from machina.db.lifecycle import acquire as acquire_db_lease
    from machina.db.lifecycle import release as release_db_lease
    from machina.db.lifecycle import set_child_pid

    db_lease = acquire_db_lease("tui")
    child = subprocess.Popen(args, env=env)
    set_child_pid(db_lease, child.pid)
    previous_handlers: dict[int, object] = {}

    def _request_stop(_signum: int, _frame: object) -> None:
        if child.poll() is None:
            child.terminate()

    for signum in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if signum is not None:
            try:
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, _request_stop)
            except (OSError, ValueError):
                pass
    # Config-gated Firecrawl/SearXNG stack lifecycle (off by default): the
    # interactive session brings the local stack up in the background and the
    # last live session stops containers + VM on exit. Import it only after the
    # UI child has started so its optional dependencies never delay the first
    # frame. Fail-open by contract: nothing here may raise into the CLI.
    stop_firecrawl = None
    try:
        from machina.core.firecrawl_lifecycle import (
            start_if_enabled,
            stop_if_enabled,
        )

        atexit.register(stop_if_enabled)
        start_if_enabled()
        stop_firecrawl = stop_if_enabled
    except Exception:
        # The optional local stack is never worth blocking the UI for.
        pass
    try:
        return child.wait()
    except KeyboardInterrupt:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
        return 130
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        if stop_firecrawl is not None:
            stop_firecrawl()
        release_db_lease(db_lease)
