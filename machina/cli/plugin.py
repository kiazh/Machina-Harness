"""Inspect opt-in entry-point plugins."""

from __future__ import annotations

import json
from importlib.metadata import entry_points

import typer
from rich.table import Table

from machina.cli import output
from machina.cli.output import console
from machina.plugins.loader import ENTRY_POINT_GROUP
from machina.plugins.registry import plugin_registry

app = typer.Typer(help="Inspect opt-in Machina plugins.")


def _catalog() -> list[dict[str, object]]:
    available = {
        entry.name: getattr(entry, "value", "")
        for entry in entry_points(group=ENTRY_POINT_GROUP)
    }
    loaded = set(plugin_registry.list())
    return [
        {
            "name": name,
            "entry": available.get(name, ""),
            "loaded": name in loaded,
        }
        for name in sorted(set(available) | loaded)
    ]


@app.command("list")
def list_plugins(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """List installed entry points and plugins loaded in this process."""
    rows = _catalog()
    if json_output:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        console.print("[yellow]No plugins installed or loaded.[/yellow]")
        return
    table = Table(title=f"Machina plugins ({len(rows)})")
    table.add_column("Name")
    table.add_column("Entry point")
    table.add_column("Loaded")
    for row in rows:
        table.add_row(
            str(row["name"]),
            str(row["entry"]),
            "yes" if row["loaded"] else "no",
        )
    console.print(table)


@app.command("loaded")
def loaded_plugins(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List plugins loaded in the current process."""
    rows = [row for row in _catalog() if row["loaded"]]
    if json_output:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
    elif not rows:
        console.print("[yellow]No plugins loaded in this process.[/yellow]")
    else:
        for row in rows:
            output.status_line(str(row["name"]), str(row["entry"]))
