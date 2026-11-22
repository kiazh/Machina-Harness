"""Machina CLI — the ``machina`` command.

``machina`` with no subcommand (also ``machina repl`` and ``machina chat -i``) launches the
TypeScript terminal UI in ``ui/``, which drives the agent through the Python
gateway (``python -m machina.gateway``). Every other subcommand is a one-shot admin
command rendered with Rich.

This package is the composition root: it creates ``app``, registers every
command in its original order (help output and the Click command list depend on
it), and re-exports the command functions and shared helpers. The commands
themselves live in sibling modules: :mod:`~machina.cli.chat`,
:mod:`~machina.cli.memory`, :mod:`~machina.cli.sessions`,
:mod:`~machina.cli.skills`, and :mod:`~machina.cli.maintenance`.
"""

from __future__ import annotations

import typer

from machina import interop as interop_mod
from machina.cli import output
from machina.cli.api import api
from machina.cli.audit import audit
from machina.cli.auth import app as auth_app
from machina.cli.chat import _report_terminal_response, chat, repl
from machina.cli.common import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PAUSED,
    VALID_MODES,
    _apply_cli_mode,
    _db_connected,
    _normalize_mode,
    _parse_uuid,
    _resolve_session,
    _run,
    _sync_env_shadow,
)
from machina.cli.debug import app as debug_app
from machina.cli.growth import app as growth_app
from machina.cli.host import host_app
from machina.cli.interop import app as harnesses_app
from machina.cli.maintenance import (
    config_set,
    config_show,
    doctor,
    init,
    serve,
    set_mode,
    setup,
    status,
    stop_all,
    uninstall,
    version,
)
from machina.cli.mcp import app as mcp_app
from machina.cli.memory import (
    memory_approve,
    memory_approve_all,
    memory_forget,
    memory_list,
    memory_pending,
    memory_reject,
    memory_reject_all,
    memory_search,
    memory_stats,
    user_profile_cmd,
    user_profile_list,
    user_profile_update,
)
from machina.cli.optimization import app as optimization_app
from machina.cli.output import console
from machina.cli.parity import models, run, session_app, undo, update
from machina.cli.providers import app as providers_app
from machina.cli.sessions import (
    compress,
    context,
    delete,
    export,
    fork,
    list_sessions,
    route_command,
    routes_command,
    sessions_search,
    usage_command,
)
from machina.cli.skills import (
    curator,
    hub,
    learn,
    skills,
    skills_app,
    skills_enrich,
    skills_list,
)
from machina.cli.snapshots import app as snapshots_app
from machina.db.connection import db
from machina.ui_launcher import launch_ui

__all__ = [
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_PAUSED",
    "VALID_MODES",
    "_apply_cli_mode",
    "_db_connected",
    "_normalize_mode",
    "_parse_uuid",
    "_report_terminal_response",
    "_resolve_session",
    "_run",
    "_sync_env_shadow",
    "app",
    "audit",
    "chat",
    "compress",
    "config_set",
    "config_show",
    "console",
    "context",
    "curator",
    "db",
    "delete",
    "doctor",
    "export",
    "fork",
    "host_app",
    "hub",
    "init",
    "interop_mod",
    "learn",
    "list_sessions",
    "main",
    "memory_approve",
    "memory_approve_all",
    "memory_forget",
    "memory_list",
    "memory_pending",
    "memory_reject",
    "memory_reject_all",
    "memory_search",
    "memory_stats",
    "output",
    "repl",
    "route_command",
    "routes_command",
    "serve",
    "sessions_search",
    "set_mode",
    "setup",
    "skills",
    "skills_app",
    "skills_enrich",
    "skills_list",
    "status",
    "stop_all",
    "uninstall",
    "usage_command",
    "user_profile_cmd",
    "user_profile_list",
    "user_profile_update",
    "version",
]

app = typer.Typer(
    name="machina",
    help="Machina — self-hosted AI agent framework. Run `machina` to open the interactive UI.",
    invoke_without_command=True,
)
app.add_typer(auth_app, name="auth")
app.add_typer(growth_app, name="growth")
app.add_typer(host_app, name="host")
# Primary name is ``harnesses``; ``interop`` stays registered as a kept alias
# of the same app so existing scripts, the installer and docs keep working.
app.add_typer(harnesses_app, name="harnesses")
app.add_typer(harnesses_app, name="interop")
app.add_typer(providers_app, name="providers")
app.add_typer(optimization_app, name="optimization")
app.add_typer(skills_app, name="skills")
app.add_typer(session_app, name="session")
app.add_typer(snapshots_app, name="snapshots")
app.add_typer(mcp_app, name="mcp")
app.add_typer(debug_app, name="debug")


@app.callback()
def main(
    ctx: typer.Context,
    mode: str = typer.Option(None, "--mode", help="Execution mode: ask|workspace|sandbox|full"),
) -> None:
    """Open the interactive UI when no subcommand is given."""
    if mode is not None:
        _apply_cli_mode(mode)
    if ctx.invoked_subcommand is None:
        from machina.update import auto_update

        auto_update()
        raise typer.Exit(launch_ui(mode=mode))


# Registration order is part of the CLI's public surface: Click lists commands
# in insertion order, so this block mirrors the original one-module definition
# order exactly. Keep new commands in the feature module and register here.
app.command(name="chat")(chat)
app.command(name="api")(api)
app.command(name="run")(run)
app.command(name="repl")(repl)
app.command(name="models")(models)
app.command(name="update")(update)
app.command(name="upgrade")(update)
app.command(name="undo")(undo)
app.command(name="status")(status)
app.command(name="audit")(audit)
app.command(name="mode")(set_mode)
app.command(name="sessions")(list_sessions)
app.command(name="sessions-search")(sessions_search)
app.command(name="usage")(usage_command)
app.command(name="route")(route_command)
app.command(name="routes")(routes_command)
app.command(name="export")(export)
app.command(name="fork")(fork)
app.command(name="delete")(delete)
app.command(name="context")(context)
app.command(name="compress")(compress)
app.command(name="learn")(learn)
app.command(name="curator")(curator)
app.command(name="hub")(hub)
app.command(name="doctor")(doctor)
app.command(name="init")(init)
app.command(name="setup")(setup)
app.command(name="version")(version)
app.command(name="serve")(serve)
app.command(name="stop")(stop_all)
app.command(name="config")(config_show)
app.command(name="config-set")(config_set)
app.command(name="memory-list")(memory_list)
app.command(name="memory-search")(memory_search)
app.command(name="memory-forget")(memory_forget)
app.command(name="memory-pending")(memory_pending)
app.command(name="memory-approve")(memory_approve)
app.command(name="memory-reject")(memory_reject)
app.command(name="memory-approve-all")(memory_approve_all)
app.command(name="memory-reject-all")(memory_reject_all)
app.command(name="memory-stats")(memory_stats)
app.command(name="user-profile")(user_profile_cmd)
app.command(name="user-profile-update")(user_profile_update)
app.command(name="user-profile-list")(user_profile_list)
app.command(name="uninstall")(uninstall)


if __name__ == "__main__":
    app()
