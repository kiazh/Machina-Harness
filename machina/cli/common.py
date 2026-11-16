"""Shared helpers for the Machina CLI command modules.

The package initializer is the composition root: it creates ``app`` and
registers every command. Helpers used by more than one command module live
here so feature modules import this module, never the package initializer.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid

import typer
from rich.markup import escape

from machina.cli import output
from machina.cli.output import console
from machina.core.config import config
from machina.core.session import session_manager
from machina.db.connection import db

# Documented headless ``chat`` exit statuses (see the command docstring).
EXIT_OK = 0
EXIT_FAILED = 1
# Click/Typer reserves 2 for usage errors; paused-for-approval is distinct.
EXIT_PAUSED = 3


def _apply_cli_mode(mode: str) -> str:
    """Apply --mode to both config and the environment shadow.

    Config.get("execution_mode") reads MACHINA_EXECUTION_MODE before the
    attribute, so setting only the attribute left a .env/TUI value winning
    over an explicit restrictive CLI flag (combined #39).
    """
    normalized = _normalize_mode(mode)
    config.set("execution_mode", normalized)
    os.environ["MACHINA_EXECUTION_MODE"] = normalized
    return normalized


def _run(coro):
    """Run an async coroutine from a sync Typer command.

    When the caller's thread has no running loop (the normal CLI case),
    the coroutine runs on that thread's loop. On the main thread that
    keeps asyncio's first-SIGINT contract active: Ctrl+C cancels the
    running task, its cleanup paths run, and the interrupt surfaces after
    them. Callers already inside a running event loop keep the previous
    worker-thread isolation; non-main threads (where asyncio cannot
    install signal handlers) simply run the loop on their own thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


VALID_MODES = ("ask", "workspace", "sandbox", "full")


def _normalize_mode(value: str, *, option: str = "--mode") -> str:
    """Validate an execution-mode option, exiting with the CLI's usage error."""
    normalized = value.strip().lower()
    if normalized not in VALID_MODES:
        output.error(f"Invalid {option}. Use ask|workspace|sandbox|full.")
        raise typer.Exit(1)
    return normalized


@contextlib.asynccontextmanager
async def _db_connected():
    """Yield with a connected pool and always close it afterwards."""
    await db.connect()
    try:
        yield
    finally:
        await db.close()


def _sync_env_shadow(key: str, value: str) -> None:
    """Mirror the gateway's ``config.set`` sync for a persisted default.

    ``Config.get`` reads ``MACHINA_<KEY>`` (and ``.env``) before
    ``config.yaml``, so a saved default would be shadowed — or a stale
    line would resurrect a previous value across restarts — without the
    sync. The durable file write already happened; a failed ``.env``
    update only warns, never fails the command.
    """
    env_key = f"MACHINA_{key.upper()}"
    try:
        from machina.security.env_file import sync_env_shadow

        sync_env_shadow(env_key, value)
    except (OSError, ValueError) as error:
        console.print(
            f"[yellow]Could not update .env ({escape(type(error).__name__)}); "
            f"edit {escape(env_key)} manually.[/yellow]"
        )


def _parse_uuid(s: str) -> uuid.UUID | None:
    """Parse a UUID string, returning None if invalid.

    Accepts canonical, braced ({...}), and URN (urn:uuid:...) forms via
    stdlib uuid.UUID; surrounding whitespace is ignored.
    """
    try:
        return uuid.UUID(s.strip() if isinstance(s, str) else s)
    except (ValueError, AttributeError, TypeError):
        return None


async def _resolve_session(session_id: str | None):
    """Return the named session, or the last active one; exit with a message otherwise."""
    if session_id:
        sid = _parse_uuid(session_id)
        if sid is None:
            output.error(f"Invalid session ID: {session_id}")
            raise typer.Exit(1)
        session = await session_manager.get(sid)
        if session is None:
            output.error(
                f"Session {session_id} not found", "Use `machina sessions` to list sessions."
            )
            raise typer.Exit(1)
        return session
    session = await session_manager.get_last_active()
    if session is None:
        output.error("No active sessions.", "Start one with `machina`.")
        raise typer.Exit(1)
    return session
