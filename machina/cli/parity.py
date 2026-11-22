"""Thin daily-driver command surface shared with the existing CLI actions.

These commands add the scriptable names and grouped navigation OpenCode users
expect without duplicating the mature implementations in the existing feature
modules. Each command delegates to the same session/service code path.
"""

from __future__ import annotations

import typer

from machina.cli.chat import chat
from machina.cli.providers import models as models_command
from machina.cli.sessions import (
    compress,
    context,
    delete,
    export,
    fork,
    list_sessions,
    sessions_search,
    usage_command,
)
from machina.cli.snapshots import diff_command as snapshot_diff_command
from machina.cli.snapshots import undo_command


def run(
    prompt: str = typer.Argument(..., help="Prompt to run headlessly"),
    continue_: bool = typer.Option(False, "--continue", "-c", help="Continue the last session"),
    session: str | None = typer.Option(None, "--session", "-s", help="Resume a session"),
    model: str | None = typer.Option(None, "--model", "-m", help="Model to use"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Provider to use"),
    mode: str | None = typer.Option(None, "--mode", help="ask|workspace|sandbox|full"),
) -> None:
    """Run one prompt without the interactive UI (script/CI friendly)."""
    chat(
        message=prompt,
        continue_=continue_,
        session_id=session,
        model=model,
        provider=provider,
        verbose=False,
        interactive=False,
        mode=mode,
    )


def models(
    provider: str = typer.Argument(None, help="Provider name"),
    refresh: bool = typer.Option(False, "--refresh", help="Force a live fetch"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """List models through the top-level compatibility command."""
    models_command(provider=provider, refresh=refresh, json_output=json_output)


def update() -> None:
    """Check and fast-forward a clean source checkout explicitly."""
    from machina.update import auto_update

    auto_update(force=True)


session_app = typer.Typer(help="Manage sessions with OpenCode-style subcommands.")


@session_app.command("list")
def session_list(
    limit: int = typer.Option(10, "--limit", "-n"),
    status_filter: str | None = typer.Option(None, "--status"),
) -> None:
    """List recent sessions."""
    list_sessions(limit=limit, status_filter=status_filter)


@session_app.command("search")
def session_search(query: str = typer.Argument(...)) -> None:
    """Search session titles."""
    sessions_search(query=query)


@session_app.command("show")
def session_show(
    session_id: str = typer.Argument(None),
    limit: int = typer.Option(10, "--limit", "-n"),
) -> None:
    """Show a session and its current context."""
    context(session_id=session_id, limit=limit)


@session_app.command("export")
def session_export(
    filename: str = typer.Argument(...),
    session_id: str | None = typer.Option(None, "--session", "-s"),
) -> None:
    """Export a session to Markdown."""
    export(filename=filename, session_id=session_id)


@session_app.command("fork")
def session_fork(
    session_id: str = typer.Argument(...),
    title: str | None = typer.Option(None, "--title", "-t"),
) -> None:
    """Fork a session."""
    fork(session_id=session_id, title=title)


@session_app.command("delete")
def session_delete(
    session_id: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Delete a session."""
    delete(session_id=session_id, force=force)


@session_app.command("usage")
def session_usage(
    session_id: str | None = typer.Option(None, "--session", "-s"),
    agent: str | None = typer.Option(None, "--agent"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show durable token usage."""
    usage_command(session_id=session_id, agent=agent, json_output=json_output)


@session_app.command("compress")
def session_compress(session_id: str = typer.Argument(None)) -> None:
    """Compress a session's context."""
    compress(session_id=session_id)


@session_app.command("diff")
def session_diff(
    session_id: str | None = typer.Option(None, "--session", "-s"),
    snapshot: str | None = typer.Option(None, "--snapshot"),
    workspace: str | None = typer.Option(None, "--workspace", "-w"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show the latest workspace snapshot diff for a session."""
    snapshot_diff_command(
        snapshot=snapshot,
        session=session_id,
        workspace=workspace,
        json_output=json_output,
    )


@session_app.command("undo")
def session_undo(
    session_id: str | None = typer.Option(None, "--session", "-s"),
    snapshot: str | None = typer.Option(None, "--snapshot"),
    workspace: str | None = typer.Option(None, "--workspace", "-w"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Restore the latest workspace snapshot for a session."""
    undo_command(
        snapshot=snapshot,
        session=session_id,
        workspace=workspace,
        force=force,
    )


# A convenient top-level undo alias; the implementation is in snapshots.
undo = undo_command
