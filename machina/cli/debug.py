"""Low-risk diagnostic commands that do not start the agent or database."""

from __future__ import annotations

import collections
import json
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import typer

from machina.cli import output
from machina.cli.common import EXIT_FAILED
from machina.core.config import config, default_config_path

app = typer.Typer(help="Debugging and path inspection tools.")

_SELECTORS = ("home", "config", "data", "log", "tmp", "workspace", "db")


def _paths() -> dict[str, str]:
    home = Path(config.get("machina_home") or Path.home() / ".machina").expanduser()
    data = home
    log = home / "logs"
    tmp = Path(config.get("tmpdir") or tempfile.gettempdir()).expanduser()
    workspace = Path(
        config.get("workspace_root") or config.get("machina_home") or Path.cwd()
    ).expanduser()
    database = str(config.get("database_url") or "")
    db_label = "(unset)"
    if database:
        try:
            parts = urlsplit(database)
            db_label = (
                f"{parts.hostname or '?'}:{parts.port or 5432}/{parts.path.lstrip('/') or '?'}"
            )
        except ValueError:
            db_label = "(configured)"
    return {
        "home": str(home),
        "config": str(default_config_path()),
        "data": str(data),
        "log": str(log),
        "tmp": str(tmp),
        "workspace": str(workspace.resolve()),
        "db": db_label,
    }


@app.command("info")
def info(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Print runtime and platform diagnostics."""
    import platform
    import sys

    from machina import __version__

    values = {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "executable": sys.executable,
        "workspace": _paths()["workspace"],
    }
    if json_output:
        typer.echo(json.dumps(values, indent=2, sort_keys=True))
        return
    for key, value in values.items():
        output.status_line(key, value)


@app.command("logs")
def logs(
    lines: int = typer.Option(
        80, "--lines", "-n", min=1, max=2000, help="Number of trailing lines to print"
    ),
    name: str = typer.Option(
        "gateway.log", "--name", help="Log file name under the Machina log directory"
    ),
) -> None:
    """Print the tail of a Machina log file.

    Turn failures log their full traceback to ``gateway.log`` while the UI
    shows only a short, redacted message. This is the supported way to read
    that traceback on Windows, macOS, and Linux.
    """
    log_dir = Path(_paths()["log"])
    if not name or Path(name).name != name:
        output.error("--name must be a file name, not a path")
        raise typer.Exit(EXIT_FAILED)
    path = log_dir / name
    if not path.is_file():
        output.error(f"No log file at {path}")
        raise typer.Exit(EXIT_FAILED)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            tail = collections.deque(handle, maxlen=lines)
    except OSError as error:
        output.error(f"Could not read {path}: {error}")
        raise typer.Exit(EXIT_FAILED) from None
    for line in tail:
        typer.echo(line.rstrip("\n"))


@app.command("paths")
def paths(
    selector: str | None = typer.Argument(None, help="One of: " + ", ".join(_SELECTORS)),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Print local Machina paths (never credentials)."""
    values = _paths()
    if selector:
        key = selector.strip().lower()
        if key not in values:
            output.error(f"Unknown selector. Use one of: {', '.join(_SELECTORS)}")
            raise typer.Exit(EXIT_FAILED)
        values = {key: values[key]}
    if json_output:
        typer.echo(json.dumps(values, indent=2, sort_keys=True))
        return
    for key, value in values.items():
        output.status_line(key, value)
