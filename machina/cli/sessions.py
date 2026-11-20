"""Session inspection, routing and lifecycle commands for the Machina CLI."""

from __future__ import annotations

import json

import typer
from rich.markup import escape
from rich.table import Table

from machina import services
from machina.cli import output
from machina.cli.common import (
    EXIT_FAILED,
    _db_connected,
    _parse_uuid,
    _resolve_session,
    _run,
)
from machina.cli.output import console
from machina.core.config import config
from machina.core.context import context_manager
from machina.core.session import session_manager


def list_sessions(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of sessions to show"),
    status_filter: str | None = typer.Option(
        None, "--status", "-s", help="Filter by status (active, idle, archived)"
    ),
):
    """List recent sessions."""

    async def _sessions():
        async with _db_connected():
            sessions = await session_manager.list_sessions(status=status_filter, limit=limit)

            if not sessions:
                console.print("[yellow]No sessions found.[/yellow]")
                return

            output.sessions_table(sessions)

    _run(_sessions())


def sessions_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of results"),
):
    """Full-text search over session titles."""

    async def _search():
        async with _db_connected():
            sessions = await session_manager.search(query, limit=limit)

            if not sessions:
                console.print(f"[yellow]No sessions found for '{escape(query)}'[/yellow]")
                return

            output.sessions_table(sessions)

    _run(_search())


def usage_command(
    session_id: str | None = typer.Option(
        None, "--session", "-s", help="Session ID (default: last active)"
    ),
    agent: str | None = typer.Option(None, "--agent", help="Agent ID (default: session agent)"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
):
    """Show durable LLM usage and remaining budgets."""

    async def _usage():
        from machina.core.usage import usage_store

        async with _db_connected():
            session = await _resolve_session(session_id)
            result = await usage_store.summary(session.id, agent or session.agent_id)
            if json_output:
                typer.echo(json.dumps(result, sort_keys=True, separators=(",", ":")))
                return
            table = Table(title=f"Usage for {session.id}")
            table.add_column("Scope")
            table.add_column("Calls", justify="right")
            table.add_column("Accounted tokens", justify="right")
            table.add_column("Unknown calls", justify="right")
            table.add_column("Calls remaining", justify="right")
            table.add_column("Tokens remaining", justify="right")
            for scope in ("session", "agent"):
                item = result[scope]
                table.add_row(
                    scope,
                    str(item["requests"]),
                    str(item["accountedTokens"]),
                    str(item["unknownCalls"]),
                    str(item["requestsRemaining"])
                    if item["requestsRemaining"] is not None
                    else "unlimited",
                    str(item["tokensRemaining"])
                    if item["tokensRemaining"] is not None
                    else "unlimited",
                )
            console.print(table)
            cache = result.get("cache")
            if isinstance(cache, dict):
                state = "supported" if cache.get("supported") else "unsupported"
                console.print(
                    f"[dim]Prompt cache: {escape(str(cache.get('label') or state))} "
                    f"({escape(str(cache.get('provider') or ''))}"
                    f"{'/' + escape(str(cache.get('model'))) if cache.get('model') else ''})[/dim]"
                )
                note = str(cache.get("note") or "").strip()
                if note and not cache.get("supported"):
                    console.print(f"[yellow]{escape(note)}[/yellow]")

    _run(_usage())


def route_command(
    prompt: str = typer.Argument(..., help="Prompt to route"),
    mode: str | None = typer.Option(
        None, "--mode", help="single_model or multi_model (default: config)"
    ),
    workers: int = typer.Option(0, "--workers", "-w", help="Planned worker count; 0=auto"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable decision JSON"),
):
    """Explain the connected-model route for a prompt without calling a model."""
    from machina.core.routing import RouteDecision, RoutingError, route_task

    try:
        decision: RouteDecision = route_task(
            prompt,
            config=config,
            mode=mode,
            worker_count=workers or None,
        )
    except RoutingError as error:
        console.print(f"[red]{escape(str(error))}[/red]")
        raise typer.Exit(1) from None
    if json_output:
        typer.echo(json.dumps(decision.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        console.print(decision.explain(), markup=False, highlight=False)


def routes_command(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable inventory JSON"),
):
    """List connected models and their role-fit scores."""
    from machina.core.routing import build_inventory

    inventory = build_inventory(config)
    if json_output:
        typer.echo(json.dumps(inventory.to_dict(), ensure_ascii=False, sort_keys=True))
        return
    for route in sorted(inventory.routes, key=lambda item: item.profile.key):
        profile = route.profile
        roles = " ".join(
            f"{role}={profile.role_scores.get(role, 0):.2f}"
            for role in ("planner", "supervisor", "worker", "utility")
        )
        console.print(
            f"{escape(profile.provider)}/{escape(profile.model)} "
            f"[{escape(route.transport)}] {roles}",
            markup=True,
            highlight=False,
        )


def export(
    filename: str = typer.Argument(..., help="Output markdown filename"),
    session_id: str | None = typer.Option(
        None, "--session", "-s", help="Session ID to export (default: last active)"
    ),
):
    """Export a conversation as markdown."""

    async def _export():
        async with _db_connected():
            session = await _resolve_session(session_id)
            text = await services.export_markdown(session)
            from machina.tools.file import resolve_path

            try:
                output_path = resolve_path(filename)
            except ValueError as e:
                output.error(f"Path traversal blocked: {e}")
                raise typer.Exit(1) from None
            output_path.write_text(text, encoding="utf-8")
            output.success(f"Exported session {session.id} to {filename}")

    _run(_export())


def fork(
    session_id: str = typer.Argument(..., help="Session ID to fork"),
    title: str | None = typer.Option(None, "--title", "-t", help="Title for the forked session"),
):
    """Fork a session — create a new session with copied state and context."""

    async def _fork():
        sid = _parse_uuid(session_id)
        if sid is None:
            output.error(f"Invalid session ID: {session_id}")
            raise typer.Exit(1)
        async with _db_connected():
            try:
                new_session = await session_manager.fork(sid, title=title)
            except ValueError as error:
                # Unknown session (and other validation failures) are a
                # typed error, never a traceback.
                output.error(str(error))
                raise typer.Exit(1) from None
            except OSError as error:
                output.error(f"Could not fork session ({type(error).__name__}).")
                raise typer.Exit(1) from None
            output.success(f"Forked session {session_id} → {new_session.id}")
            output.muted(f"Title: {new_session.title or '(untitled)'}")

    _run(_fork())


def delete(
    session_id: str = typer.Argument(..., help="Session ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Permanently delete a session and all its context chunks.

    Refused while another owner (gateway/REST/job/CLI turn) holds the session:
    the same mutation-claim protocol as gateway and REST deletion applies.
    """

    async def _delete():
        async with _db_connected():
            sid = _parse_uuid(session_id)
            if sid is None:
                output.error(f"Invalid session ID: {session_id}")
                raise typer.Exit(1)
            session = await session_manager.get(sid)
            if not session:
                output.error(f"Session {session_id} not found")
                raise typer.Exit(1)

            if not force:
                console.print(
                    f"[yellow]About to delete session {session_id} "
                    f"({escape(session.title or 'untitled')})[/yellow]"
                )
                console.print("[yellow]This will permanently remove all context chunks.[/yellow]")
                confirm = typer.confirm("Are you sure?")
                if not confirm:
                    console.print("[dim]Cancelled.[/dim]")
                    return

            # R05: deletion is a mutation on a shared session — use the same
            # protocol as gateway/REST (gateway/features/sessions.py,
            # api/routes/sessions.py): refuse while a turn is live, claim a
            # mutation slot, re-check under the claim, delete, then release
            # owner-matched so a foreign owner is never unlocked.
            from machina.core.turns import begin_mutation, end_mutation, turn_active

            if await turn_active(sid):
                output.error(
                    f"Session {session_id} has an active turn.",
                    "Stop it before deleting, then retry.",
                )
                raise typer.Exit(EXIT_FAILED)
            token = await begin_mutation(sid)
            if not token:
                output.error(
                    f"Session {session_id} is busy: another owner holds it.",
                    "Wait for it to finish, then retry.",
                )
                raise typer.Exit(EXIT_FAILED)
            try:
                if await turn_active(sid):
                    output.error(
                        f"Session {session_id} has an active turn.",
                        "Stop it before deleting, then retry.",
                    )
                    raise typer.Exit(EXIT_FAILED)
                deleted = await session_manager.delete(sid)
            finally:
                await end_mutation(sid, token)
            if deleted:
                output.success(f"Session {session_id} deleted.")
            else:
                output.error(f"Failed to delete session {session_id}.")
                raise typer.Exit(1)

    _run(_delete())


def context(
    session_id: str = typer.Argument(None, help="Session ID to inspect"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of chunks to show"),
):
    """View context chunks for a session."""

    async def _context():
        async with _db_connected():
            if session_id:
                sid = _parse_uuid(session_id)
                if sid is None:
                    output.error(f"Invalid session ID: {session_id}")
                    raise typer.Exit(1)
            else:
                session = await session_manager.get_last_active()
                if not session:
                    console.print("[yellow]No active sessions.[/yellow]")
                    return
                sid = session.id

            session = await session_manager.get(sid)
            if session:
                console.print(f"[bold]Session:[/bold] {session.id}")
                console.print(f"[bold]Title:[/bold] {escape(session.title or '(untitled)')}")
                console.print(f"[bold]Status:[/bold] {escape(str(session.status))}")
                console.print(f"[bold]Goal:[/bold] {escape(session.goal or '(none)')}")
                from machina.services import session_context_window

                window = await session_context_window(session)
                console.print(f"[bold]Context window:[/bold] {window} tokens")
                console.print()

            chunks = await context_manager.get_chunks(sid, limit=limit, include_embedding=False)
            if not chunks:
                console.print("[yellow]No context chunks found.[/yellow]")
                return

            output.context_table(chunks, sid)

            total_tokens = await context_manager.get_token_usage(sid)
            console.print(f"\n[dim]Total tokens: {total_tokens}[/dim]")

    _run(_context())


def compress(
    session_id: str = typer.Argument(None, help="Session ID to compress (default: last active)"),
):
    """Compress context for a session. Reduces token usage while preserving key information."""

    async def _compress():
        async with _db_connected():
            session = await _resolve_session(session_id)
            total_tokens = await context_manager.get_token_usage(session.id)
            output.muted(f"Session: {session.id} ({total_tokens} tokens)")
            with output.spinner("Compressing context..."):
                result = await services.compress_session(session)
            if result is None:
                console.print("[yellow]Nothing to compress (not enough chunks).[/yellow]")
                return
            output.success(
                f"Context compressed: "
                f"{result.original_count} chunks → {len(result.compressed_chunks)} chunks, "
                f"{result.original_tokens} → {result.compressed_tokens} tokens "
                f"({result.compression_ratio:.1%} ratio, method: {result.method})"
            )

    _run(_compress())
