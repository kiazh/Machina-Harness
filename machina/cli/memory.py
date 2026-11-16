"""Memory, approval-queue and user-profile commands for the Machina CLI."""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.table import Table

from machina.cli import output
from machina.cli.common import _db_connected, _parse_uuid, _run
from machina.cli.output import console


def memory_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of memories to show"),
    category: str | None = typer.Option(None, "--category", "-c", help="Filter by category"),
):
    """List all memories."""

    async def _memory_list():
        async with _db_connected():
            from machina.memory.store import memory_store

            memories = await memory_store.search(category=category, limit=limit)

            if not memories:
                console.print("[yellow]No memories found.[/yellow]")
                return

            output.memory_table(memories)

    _run(_memory_list())


def memory_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, "--limit", "-n", help="Number of results"),
):
    """Search memories by relevance."""

    async def _memory_search():
        async with _db_connected():
            from machina.memory.retriever import MemoryRetriever

            retriever = MemoryRetriever(top_k=limit)
            results = await retriever.retrieve(query=query)

            if not results:
                console.print(f"[yellow]No memories found for '{escape(query)}'[/yellow]")
                return

            console.print(f"[bold]Search results for '{escape(query)}'[/bold]")
            for i, rm in enumerate(results, 1):
                m = rm.memory
                console.print(
                    f"\n  [{i}] [cyan]({escape(m.category)}, importance={m.importance:.2f}, score={rm.score:.3f})[/cyan]"
                )
                console.print(f"      {escape(m.content[:200])}")
                if rm.persona_interpretation:
                    console.print(
                        f"      [dim][persona] {escape(rm.persona_interpretation[:200])}[/dim]"
                    )
            console.print()

    _run(_memory_search())


def memory_forget(
    memory_id: str = typer.Argument(..., help="Memory ID to delete"),
):
    """Delete a memory by ID."""

    async def _memory_forget():
        async with _db_connected():
            from machina.memory.store import memory_store

            mid = _parse_uuid(memory_id)
            if mid is None:
                output.error(f"Invalid memory ID: {memory_id}")
                raise typer.Exit(1)
            deleted = await memory_store.delete(mid)
            if deleted:
                output.success(f"Memory {memory_id} deleted.")
            else:
                console.print(f"[yellow]Memory {escape(memory_id)} not found.[/yellow]")

    _run(_memory_forget())


def memory_pending(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of pending memories to show"),
):
    """List pending memories awaiting approval."""

    async def _memory_pending():
        async with _db_connected():
            from machina.memory.approval import ApprovalStatus, memory_approval_gate

            pending = await memory_approval_gate.list_pending(
                status=ApprovalStatus.PENDING,
                limit=limit,
            )

            if not pending:
                console.print("[yellow]No pending memories.[/yellow]")
                return

            table = Table(title=f"Pending Memories ({len(pending)})")
            table.add_column("ID", style="cyan", no_wrap=True)
            table.add_column("Category", style="green")
            table.add_column("Importance", style="yellow")
            table.add_column("Content", style="white")
            table.add_column("Redactions", style="red")

            for p in pending:
                table.add_row(
                    escape(str(p.id))[:8],
                    escape(p.category),
                    f"{p.importance:.2f}",
                    escape(p.content[:60]),
                    escape(", ".join(p.redactions)) if p.redactions else "—",
                )

            console.print(table)

    _run(_memory_pending())


def memory_approve(
    pending_id: str = typer.Argument(..., help="Pending memory ID to approve"),
    note: str = typer.Option("", "--note", "-n", help="Review note"),
):
    """Approve a pending memory."""

    async def _memory_approve():
        async with _db_connected():
            from machina.memory.approval import memory_approval_gate

            pid = _parse_uuid(pending_id)
            if pid is None:
                output.error(f"Invalid pending memory ID: {pending_id}")
                raise typer.Exit(1)
            memory = await memory_approval_gate.approve(pid, review_note=note)
            if memory:
                console.print(
                    f"[green]Pending memory {escape(pending_id)} approved → memory {memory.id}[/green]"
                )
            else:
                console.print(
                    f"[yellow]Pending memory {pending_id} not found or already reviewed.[/yellow]"
                )

    _run(_memory_approve())


def memory_reject(
    pending_id: str = typer.Argument(..., help="Pending memory ID to reject"),
    note: str = typer.Option("", "--note", "-n", help="Review note"),
):
    """Reject a pending memory."""

    async def _memory_reject():
        async with _db_connected():
            from machina.memory.approval import memory_approval_gate

            pid = _parse_uuid(pending_id)
            if pid is None:
                output.error(f"Invalid pending memory ID: {pending_id}")
                raise typer.Exit(1)
            rejected = await memory_approval_gate.reject(pid, review_note=note)
            if rejected:
                console.print(f"[green]Pending memory {escape(pending_id)} rejected.[/green]")
            else:
                console.print(
                    f"[yellow]Pending memory {pending_id} not found or already reviewed.[/yellow]"
                )

    _run(_memory_reject())


def memory_approve_all(
    agent_id: str | None = typer.Option(None, "--agent", "-a", help="Filter by agent ID"),
):
    """Approve all pending memories."""

    async def _memory_approve_all():
        async with _db_connected():
            from machina.memory.approval import memory_approval_gate

            count = await memory_approval_gate.approve_all(agent_id=agent_id)
            console.print(f"[green]Approved {count} pending memories.[/green]")

    _run(_memory_approve_all())


def memory_reject_all(
    agent_id: str | None = typer.Option(None, "--agent", "-a", help="Filter by agent ID"),
    note: str = typer.Option("", "--note", "-n", help="Review note"),
):
    """Reject all pending memories."""

    async def _memory_reject_all():
        async with _db_connected():
            from machina.memory.approval import memory_approval_gate

            count = await memory_approval_gate.reject_all(agent_id=agent_id, review_note=note)
            console.print(f"[green]Rejected {count} pending memories.[/green]")

    _run(_memory_reject_all())


def memory_stats():
    """Show memory approval statistics."""

    async def _memory_stats():
        async with _db_connected():
            from machina.memory.approval import memory_approval_gate

            stats = await memory_approval_gate.get_stats()

            table = Table(title="Memory Approval Stats")
            table.add_column("Status", style="cyan")
            table.add_column("Count", style="white")

            for status, count in stats.items():
                table.add_row(status, str(count))

            console.print(table)

    _run(_memory_stats())


def user_profile_cmd(
    user_id: str = typer.Argument(..., help="User ID to look up or create"),
    display_name: str = typer.Option("", "--name", "-n", help="Display name for new profiles"),
):
    """Show or create a user profile."""

    async def _user_profile():
        async with _db_connected():
            from machina.memory.user_profile import user_profile_store

            profile = await user_profile_store.get_or_create(
                user_id=user_id,
                display_name=display_name,
            )

            console.print("[bold]User Profile[/bold]")
            console.print(f"  ID: {profile.id}")
            console.print(f"  User ID: {escape(profile.user_id)}")
            console.print(
                f"  Display Name: {escape(profile.display_name) if profile.display_name else '(none)'}"
            )
            console.print(f"  Interactions: {profile.interaction_count}")
            console.print(f"  Created: {profile.created_at.strftime('%Y-%m-%d %H:%M')}")

            if profile.preferences:
                console.print("\n  [bold]Preferences:[/bold]")
                for key, value in profile.preferences.items():
                    console.print(f"    {escape(str(key))}: {escape(str(value))}")

            if profile.topics:
                console.print("\n  [bold]Top Topics:[/bold]")
                for topic, count in profile.get_top_topics():
                    console.print(f"    {escape(str(topic))}: {count}")

            if profile.last_topics:
                console.print("\n  [bold]Recent Topics:[/bold]")
                console.print(f"    {escape(', '.join(profile.last_topics[:5]))}")

    _run(_user_profile())


def user_profile_update(
    user_id: str = typer.Argument(..., help="User ID"),
    key: str = typer.Argument(..., help="Preference key"),
    value: str = typer.Argument(..., help="Preference value"),
):
    """Update a user profile preference."""

    async def _user_profile_update():
        async with _db_connected():
            from machina.memory.user_profile import user_profile_store

            profile = await user_profile_store.get_by_user_id(user_id)
            if not profile:
                console.print(f"[red]User profile not found for '{escape(user_id)}'[/red]")
                raise typer.Exit(1)

            profile.set_preference(key, value)
            updated = await user_profile_store.update_preferences(profile.id, profile.preferences)
            if updated:
                console.print(
                    f"[green]Updated {escape(key)} = {escape(value)} for {escape(user_id)}[/green]"
                )
            else:
                console.print("[red]Failed to update profile[/red]")
                raise typer.Exit(1)

    _run(_user_profile_update())


def user_profile_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of profiles to show"),
):
    """List all user profiles."""

    async def _user_profile_list():
        async with _db_connected():
            from machina.memory.user_profile import user_profile_store

            profiles = await user_profile_store.list_all(limit=limit)

            if not profiles:
                console.print("[yellow]No user profiles found.[/yellow]")
                return

            table = Table(title=f"User Profiles ({len(profiles)})")
            table.add_column("User ID", style="cyan")
            table.add_column("Display Name", style="white")
            table.add_column("Interactions", style="green")
            table.add_column("Topics", style="dim")

            for p in profiles:
                table.add_row(
                    escape(p.user_id),
                    escape(p.display_name or "—"),
                    escape(str(p.interaction_count)),
                    escape(str(len(p.topics))),
                )

            console.print(table)

    _run(_user_profile_list())
