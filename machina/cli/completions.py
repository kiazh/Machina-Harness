"""Generate shell completion scripts for the CLI."""

from __future__ import annotations

import click
import typer
from click.shell_completion import get_completion_class

SUPPORTED = ("bash", "zsh", "fish")


def completion_script(shell: str, cli: click.Command, prog_name: str = "machina") -> str:
    """Return a Click completion script for *shell*."""
    completion_class = get_completion_class(shell)
    if completion_class is None:
        raise ValueError(f"unsupported shell: {shell}")
    completion = completion_class(cli, {}, prog_name, "_MACHINA_COMPLETE")
    return completion.source()


def completions(
    shell: str = typer.Argument(..., help="bash, zsh, or fish"),
) -> None:
    """Print a shell completion script to stdout."""
    normalized = shell.strip().lower()
    if normalized not in SUPPORTED:
        typer.echo(
            f"Unsupported shell {shell!r}; choose one of: {', '.join(SUPPORTED)}",
            err=True,
        )
        raise typer.Exit(2)
    root = click.get_current_context().find_root().command
    try:
        script = completion_script(normalized, root)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from None
    typer.echo(script)
