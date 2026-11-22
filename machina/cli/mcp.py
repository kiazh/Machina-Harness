"""Human MCP server administration for the CLI.

The TUI owns OAuth browser flows. The CLI can inspect, configure, connect, and
remove a server through the same ConnectorService used by the gateway; it does
not launch an implicit OAuth flow.
"""

from __future__ import annotations

import json

import typer
from rich.markup import escape
from rich.table import Table

from machina.cli import output
from machina.cli.common import EXIT_FAILED, _run
from machina.cli.output import console

app = typer.Typer(help="Manage MCP servers.")


def _service():
    from machina.core.runtime import runtime_services

    return runtime_services.get_connector_service()


@app.command("list")
def list_servers(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """List configured MCP servers without connecting to them."""
    try:
        service = _service()
        rows = _run(service.list())
    except Exception as error:
        output.error(f"Could not load MCP servers: {type(error).__name__}")
        raise typer.Exit(EXIT_FAILED) from None
    if json_output:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        console.print("[yellow]No MCP servers configured.[/yellow]")
        return
    table = Table(title=f"MCP servers ({len(rows)})")
    for column in ("Name", "Transport", "Endpoint / command", "Trust", "State"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            escape(str(row.get("id") or "")),
            escape(str(row.get("transport") or "")),
            escape(str(row.get("endpoint") or row.get("command") or "")),
            escape(str(row.get("trust") or "")),
            escape(str(row.get("state") or "")),
        )
    console.print(table)


@app.command("show")
def show_server(
    name: str = typer.Argument(..., help="Server name"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show one server's public configuration and connection state."""
    try:
        row = _run(_service().status(name))
    except Exception as error:
        output.error(f"Could not load MCP server: {type(error).__name__}")
        raise typer.Exit(EXIT_FAILED) from None
    if json_output:
        typer.echo(json.dumps(row, indent=2, sort_keys=True))
    else:
        console.print(json.dumps(row, indent=2, sort_keys=True), markup=False)


@app.command("add")
def add_server(
    url: str | None = typer.Option(None, "--url", help="Streamable HTTP endpoint"),
    command: str | None = typer.Option(None, "--command", help="Local stdio executable"),
    name: str | None = typer.Option(None, "--name", help="Server id (derived when omitted)"),
    trust: str = typer.Option("untrusted", "--trust", help="untrusted|trusted_read"),
    connect: bool = typer.Option(False, "--connect", help="Connect and discover immediately"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Add an HTTP or stdio MCP server configuration."""
    if bool(url) == bool(command):
        output.error("Provide exactly one of --url or --command.")
        raise typer.Exit(EXIT_FAILED)
    from machina.connectors.management import derive_connector_name

    server_name = name or derive_connector_name(url=url or "", command=command or "")
    if not yes:
        target = url or command or ""
        typer.confirm(f"Add MCP server '{server_name}' at {target}?", abort=True)
    fields = {
        "id": server_name,
        "display_name": server_name,
        "transport": "streamable_http" if url else "stdio",
        "endpoint": url or "",
        "command": command or "",
        "argv": [],
        "trust": trust,
        "tool_allowlist": [],
        "env_allowlist": [],
        "header_refs": {},
        "local_access": False,
    }
    try:
        service = _service()
        record = _run(service.configure(fields))
        if connect:
            connected = _run(service.connect_and_discover(server_name))
            record = {**record, "connection": connected}
    except Exception as error:
        output.error(f"MCP add failed: {type(error).__name__}: {escape(str(error))}")
        raise typer.Exit(EXIT_FAILED) from None
    output.success(f"Configured MCP server {server_name}.")
    if connect:
        console.print("[dim]Connected and discovered; use `machina mcp show` for state.[/dim]")


@app.command("remove")
def remove_server(
    name: str = typer.Argument(..., help="Server name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove an MCP server and disconnect its live process."""
    if not yes:
        typer.confirm(f"Remove MCP server '{name}'?", abort=True)
    try:
        removed = _run(_service().remove(name))
    except Exception as error:
        output.error(f"MCP remove failed: {type(error).__name__}")
        raise typer.Exit(EXIT_FAILED) from None
    if removed:
        output.success(f"Removed MCP server {name}.")
    else:
        output.error(f"No MCP server named {name}.")
        raise typer.Exit(EXIT_FAILED) from None
