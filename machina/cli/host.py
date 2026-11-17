"""`machina host`: a persistent tmux shell with an optional browser terminal."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import typer

from machina import host as host_mod
from machina.cli import output

host_app = typer.Typer(
    name="host",
    help="Run Machina as a persistent host shell (tmux + ttyd).",
    no_args_is_help=True,
)


def _ssh_forward(state: host_mod.HostState) -> str:
    return f"ssh -L {state.port}:127.0.0.1:{state.port} <user>@<this-host>"


def _browser_url(state: host_mod.HostState) -> str:
    return f"http://127.0.0.1:{state.port}"


def _print_started(state: host_mod.HostState, created: bool) -> None:
    if created:
        output.success("Machina host is running.")
    else:
        output.success("Machina host is already running.")
    output.status_line("Session", state.session)
    if state.machina_session:
        output.status_line("Machina session", state.machina_session)
    output.status_line("Browser", _browser_url(state))
    output.status_line("Workspace", state.workspace)
    output.status_line("Mode", state.mode)
    if created:
        output.status_line("Username", state.username)
        output.status_line("Password", state.password)
        output.muted(
            "The browser password is stored in ~/.machina/host.json with 0600 permissions."
        )
    output.muted("Attach from SSH:")
    output.console.print("  machina host attach", style="dim")
    output.muted("Forward the browser port over SSH from another computer:")
    output.console.print(f"  {_ssh_forward(state)}", style="dim")
    output.console.print(f"  then open {_browser_url(state)}", style="dim")
    output.muted("Keep it private on your tailnet:")
    output.console.print(f"  tailscale serve --bg {state.port}", style="dim")
    output.muted("Or expose it through a Cloudflare Tunnel with Cloudflare Access in front:")
    output.console.print(f"  cloudflared tunnel --url http://127.0.0.1:{state.port}", style="dim")


@host_app.command("start")
def start(
    workspace: str = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Directory the persistent TUI opens (default: current directory).",
    ),
    bind: str = typer.Option(
        host_mod.DEFAULT_BIND,
        "--bind",
        "-b",
        help="Browser bridge interface; loopback only.",
    ),
    port: int = typer.Option(
        host_mod.DEFAULT_PORT,
        "--port",
        "-p",
        help="Browser bridge port.",
    ),
    mode: str = typer.Option(
        host_mod.DEFAULT_MODE,
        "--mode",
        "-m",
        help="Execution mode: ask|workspace|sandbox|full.",
    ),
    session: str = typer.Option(
        host_mod.DEFAULT_SESSION,
        "--session",
        help="tmux session name; an existing session is reused.",
    ),
    machina_session: str = typer.Option(
        None,
        "--machina-session",
        help="Machina conversation UUID to resume in a newly created tmux shell.",
    ),
    username: str = typer.Option(
        host_mod.DEFAULT_USER,
        "--user",
        help="Browser terminal username.",
    ),
    allow_full: bool = typer.Option(
        False,
        "--allow-full",
        help="Allow full host mode for the remote shell (not recommended).",
    ),
) -> None:
    """Create or reuse the persistent shell and browser bridge."""
    directory = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
    try:
        state, created = host_mod.start_host(
            workspace=directory,
            bind=bind,
            port=port,
            mode=mode,
            session=session,
            username=username,
            allow_full=allow_full,
            machina_session=machina_session,
        )
    except host_mod.HostError as error:
        output.error(str(error))
        raise typer.Exit(1) from None
    _print_started(state, created)


@host_app.command("status")
def status() -> None:
    """Show whether the persistent shell and browser bridge are alive."""
    try:
        state, tmux_alive, ttyd_alive = host_mod.status_host()
    except host_mod.HostError as error:
        output.error(str(error))
        raise typer.Exit(1) from None
    if state is None:
        output.muted("Machina host is not running.")
        raise typer.Exit(1)
    running = tmux_alive and ttyd_alive
    stopped = not tmux_alive and not ttyd_alive
    if running:
        output.success("Machina host is running.")
    elif stopped:
        output.muted("Machina host is stopped.")
    else:
        output.success("Machina host is degraded.")
    output.status_line("Session", state.session)
    output.status_line(
        "tmux", "running" if tmux_alive else "stopped", "success" if tmux_alive else "error"
    )
    output.status_line(
        "ttyd", "running" if ttyd_alive else "stopped", "success" if ttyd_alive else "error"
    )
    output.status_line("Browser", _browser_url(state))
    output.status_line("Workspace", state.workspace)
    output.status_line("Mode", state.mode)
    if not running:
        output.muted(
            "Run `machina host start` to " + ("start" if stopped else "repair") + " the host."
        )
        raise typer.Exit(1)


@host_app.command("url")
def url(
    show_credentials: bool = typer.Option(
        False,
        "--show-credentials",
        help="Print the browser username and password.",
    ),
) -> None:
    """Print the local URL and SSH access command."""
    try:
        state, _tmux_alive, _ttyd_alive = host_mod.status_host()
    except host_mod.HostError as error:
        output.error(str(error))
        raise typer.Exit(1) from None
    if state is None:
        output.error("Machina host is not running.")
        raise typer.Exit(1)
    output.status_line("Browser", _browser_url(state))
    output.status_line("SSH", _ssh_forward(state))
    if show_credentials:
        output.status_line("Username", state.username)
        output.status_line("Password", state.password)


@host_app.command("attach")
def attach() -> None:
    """Attach this terminal to the persistent tmux session."""
    try:
        state = host_mod.load_state()
    except host_mod.HostError as error:
        output.error(str(error))
        raise typer.Exit(1) from None
    session = state.session if state is not None else host_mod.DEFAULT_SESSION
    tmux = shutil.which("tmux")
    if not tmux:
        output.error("tmux is not installed.", "Install tmux, then run `machina host start`.")
        raise typer.Exit(1)
    completed = subprocess.run(
        [tmux, "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        output.error(
            f"tmux session {session!r} is not running.",
            "Run `machina host start` first.",
        )
        raise typer.Exit(1)
    os.execvp(tmux, [tmux, "attach-session", "-t", session])


@host_app.command("stop")
def stop(
    keep_session: bool = typer.Option(
        False,
        "--keep-session",
        help="Stop the browser bridge but leave the tmux shell running.",
    ),
) -> None:
    """Stop the browser bridge and, unless asked, the tmux shell."""
    try:
        state = host_mod.stop_host(keep_session=keep_session)
    except host_mod.HostError as error:
        output.error(str(error))
        raise typer.Exit(1) from None
    if state is None:
        output.muted("Machina host is not running.")
        return
    if keep_session:
        output.success(
            f"Stopped the browser bridge; tmux session {state.session!r} is still running."
        )
    else:
        output.success(f"Stopped Machina host and tmux session {state.session!r}.")
