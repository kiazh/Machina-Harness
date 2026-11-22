"""Inspect, diff, and undo per-turn workspace snapshots."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.markup import escape
from rich.table import Table

from machina.cli import output
from machina.cli.common import EXIT_FAILED
from machina.cli.output import console
from machina.workspace import snapshots

app = typer.Typer(help="Inspect and undo per-turn workspace snapshots.")


def _workspace(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else snapshots.workspace_root()


def _select(
    snapshot_id: str | None,
    workspace: Path,
    session_id: str | None,
) -> tuple[dict, Path]:
    found = (
        snapshots.snapshot_by_id(snapshot_id, workspace=workspace, session_id=session_id)
        if snapshot_id
        else snapshots.latest_snapshot(workspace=workspace, session_id=session_id)
    )
    if found is None:
        output.error("No matching snapshot found.")
        raise typer.Exit(EXIT_FAILED)
    return found


@app.command("list")
def list_command(
    session: str | None = typer.Option(None, "--session", "-s", help="Session ID filter"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace root"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum rows"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """List completed snapshots for this workspace."""
    rows = snapshots.list_snapshots(
        workspace=_workspace(workspace), session_id=session, limit=limit
    )
    rows = [row for row in rows if row.get("status") in {"complete", "undone"}]
    if json_output:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        console.print("[yellow]No snapshots found.[/yellow]")
        return
    table = Table(title=f"Workspace snapshots ({len(rows)})")
    table.add_column("Snapshot")
    table.add_column("Session")
    table.add_column("Created")
    table.add_column("Files", justify="right")
    table.add_column("Status")
    for row in rows:
        table.add_row(
            escape(str(row.get("snapshot_id") or "")),
            escape(str(row.get("session_id") or "")),
            escape(str(row.get("created_at") or "")),
            str(len(row.get("files") or [])),
            escape(str(row.get("status") or "")),
        )
    console.print(table)


@app.command("diff")
def diff_command(
    snapshot: str | None = typer.Option(None, "--snapshot", help="Snapshot ID (default: latest)"),
    session: str | None = typer.Option(None, "--session", "-s", help="Session ID filter"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace root"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show the unified diff for a completed snapshot."""
    payload, _ = _select(snapshot, _workspace(workspace), session)
    text = snapshots.diff_snapshot(payload)
    if json_output:
        typer.echo(json.dumps({"snapshot": payload, "diff": text}, indent=2, sort_keys=True))
        return
    if not text:
        console.print("[yellow]No textual diff (binary, missing, or empty files).[/yellow]")
        return
    console.print(text, markup=False, highlight=False, soft_wrap=True)


@app.command("undo")
def undo_command(
    snapshot: str | None = typer.Option(None, "--snapshot", help="Snapshot ID (default: latest)"),
    session: str | None = typer.Option(None, "--session", "-s", help="Session ID filter"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace root"),
    force: bool = typer.Option(False, "--force", help="Overwrite files changed after the turn"),
) -> None:
    """Restore the files changed by a completed snapshot."""
    root = _workspace(workspace)
    payload, manifest = _select(snapshot, root, session)
    try:
        restored = snapshots.undo_snapshot(payload, force=force)
    except snapshots.SnapshotConflict as conflict:
        output.error(str(conflict))
        output.muted("Re-run with --force only if you intend to discard those later edits.")
        raise typer.Exit(EXIT_FAILED) from None
    snapshots.mark_undone(payload, manifest)
    output.success(f"Restored {len(restored)} file(s) from snapshot {payload['snapshot_id']}.")
    for path in restored:
        console.print(f"  [dim]{escape(path)}[/dim]")
