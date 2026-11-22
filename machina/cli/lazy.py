"""Lazy console entry point for the Machina CLI.

The eager Typer composition root remains available at ``machina.cli.app`` for
integration tests and embedders. The installed console script uses this group
instead: ``--help`` lists a static command table, and invoking one command
imports only that feature module (plus the Typer command conversion).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module

import click
import typer


@dataclass(frozen=True, slots=True)
class LazySpec:
    module: str
    attribute: str
    help: str


# Command order mirrors the eager composition root: Click lists commands in
# insertion order, so help output remains stable.
_SPECS: dict[str, LazySpec] = {
    "chat": LazySpec("machina.cli.chat", "chat", "Chat with the agent once or interactively."),
    "api": LazySpec("machina.cli.api", "api", "Call the running Machina HTTP API."),
    "acp": LazySpec("machina.cli.acp", "acp", "Run as an Agent Client Protocol agent."),
    "run": LazySpec("machina.cli.parity", "run", "Run one prompt headlessly."),
    "repl": LazySpec("machina.cli.chat", "repl", "Start a read-eval-print loop."),
    "models": LazySpec("machina.cli.parity", "models", "List models for a provider."),
    "update": LazySpec("machina.cli.parity", "update", "Update a source checkout."),
    "upgrade": LazySpec("machina.cli.parity", "update", "Update a source checkout."),
    "undo": LazySpec("machina.cli.parity", "undo", "Restore the last workspace snapshot."),
    "status": LazySpec(
        "machina.cli.maintenance", "status", "Show database, session, and tool status."
    ),
    "audit": LazySpec("machina.cli.audit", "audit", "Show recent sanitized audit events."),
    "mode": LazySpec("machina.cli.maintenance", "set_mode", "Show or set the execution mode."),
    "sessions": LazySpec("machina.cli.sessions", "list_sessions", "List recent sessions."),
    "session": LazySpec("machina.cli.parity", "session_app", "Manage sessions by subcommand."),
    "sessions-search": LazySpec(
        "machina.cli.sessions", "sessions_search", "Search session titles."
    ),
    "usage": LazySpec("machina.cli.sessions", "usage_command", "Show durable LLM usage."),
    "route": LazySpec("machina.cli.sessions", "route_command", "Explain a routing decision."),
    "routes": LazySpec("machina.cli.sessions", "routes_command", "List model routes."),
    "export": LazySpec("machina.cli.sessions", "export", "Export a session."),
    "fork": LazySpec("machina.cli.sessions", "fork", "Fork a session."),
    "delete": LazySpec("machina.cli.sessions", "delete", "Delete a session."),
    "context": LazySpec("machina.cli.sessions", "context", "Inspect session context."),
    "compress": LazySpec("machina.cli.sessions", "compress", "Compress session context."),
    "learn": LazySpec("machina.cli.skills", "learn", "Learn a skill from a trajectory."),
    "curator": LazySpec("machina.cli.skills", "curator", "Run the skill curator."),
    "hub": LazySpec("machina.cli.skills", "hub", "Manage the skill hub."),
    "doctor": LazySpec("machina.cli.maintenance", "doctor", "Diagnose the installation."),
    "init": LazySpec("machina.cli.maintenance", "init", "Initialize configuration."),
    "setup": LazySpec("machina.cli.maintenance", "setup", "Run first-launch setup."),
    "version": LazySpec("machina.cli.maintenance", "version", "Show the version."),
    "serve": LazySpec("machina.cli.maintenance", "serve", "Serve the HTTP API."),
    "stop": LazySpec("machina.cli.maintenance", "stop_all", "Stop Machina clients and services."),
    "config": LazySpec("machina.cli.maintenance", "config_show", "Show configuration."),
    "config-set": LazySpec("machina.cli.maintenance", "config_set", "Set a configuration value."),
    "memory-list": LazySpec("machina.cli.memory", "memory_list", "List memories."),
    "memory-search": LazySpec("machina.cli.memory", "memory_search", "Search memories."),
    "memory-forget": LazySpec("machina.cli.memory", "memory_forget", "Forget a memory."),
    "memory-pending": LazySpec("machina.cli.memory", "memory_pending", "List pending memories."),
    "memory-approve": LazySpec("machina.cli.memory", "memory_approve", "Approve a memory."),
    "memory-reject": LazySpec("machina.cli.memory", "memory_reject", "Reject a memory."),
    "memory-approve-all": LazySpec(
        "machina.cli.memory", "memory_approve_all", "Approve all pending memories."
    ),
    "memory-reject-all": LazySpec(
        "machina.cli.memory", "memory_reject_all", "Reject all pending memories."
    ),
    "memory-stats": LazySpec("machina.cli.memory", "memory_stats", "Show memory statistics."),
    "user-profile": LazySpec("machina.cli.memory", "user_profile_cmd", "Show or create a profile."),
    "user-profile-update": LazySpec(
        "machina.cli.memory", "user_profile_update", "Update the user profile."
    ),
    "user-profile-list": LazySpec("machina.cli.memory", "user_profile_list", "List user profiles."),
    "uninstall": LazySpec("machina.cli.maintenance", "uninstall", "Uninstall Machina."),
    "auth": LazySpec("machina.cli.auth", "app", "Manage provider authentication."),
    "growth": LazySpec("machina.cli.growth", "app", "Inspect growth and memory learning."),
    "host": LazySpec("machina.cli.host", "host_app", "Manage remote host access."),
    "harnesses": LazySpec("machina.cli.interop", "app", "Register external harnesses."),
    "interop": LazySpec("machina.cli.interop", "app", "Register external harnesses."),
    "providers": LazySpec("machina.cli.providers", "app", "Manage providers."),
    "optimization": LazySpec("machina.cli.optimization", "app", "Inspect optimization data."),
    "skills": LazySpec("machina.cli.skills", "skills_app", "Manage skills."),
    "snapshots": LazySpec("machina.cli.snapshots", "app", "Inspect and undo workspace snapshots."),
    "mcp": LazySpec("machina.cli.mcp", "app", "Manage MCP servers."),
    "plugin": LazySpec("machina.cli.plugin", "app", "Inspect opt-in Machina plugins."),
    "plugins": LazySpec("machina.cli.plugin", "app", "Inspect opt-in Machina plugins."),
    "service": LazySpec("machina.cli.service", "app", "Manage the background HTTP service."),
    "debug": LazySpec("machina.cli.debug", "app", "Debugging and path inspection tools."),
}


class LazyGroup(click.Group):
    """A Click group that imports command implementations on first use."""

    def __init__(self, *args, specs: Mapping[str, LazySpec] | None = None, **kwargs) -> None:
        self._specs = dict(specs or {})
        super().__init__(*args, **kwargs)

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self._specs)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        spec = self._specs.get(cmd_name)
        if spec is None:
            return None
        if cmd_name not in self.commands:
            self.commands[cmd_name] = _load_command(cmd_name, spec)
        return self.commands[cmd_name]

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rows = [(name, spec.help) for name, spec in self._specs.items()]
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


def _load_command(name: str, spec: LazySpec) -> click.Command:
    target = getattr(import_module(spec.module), spec.attribute)
    if isinstance(target, typer.Typer):
        command = typer.main.get_command(target)
    else:
        temporary = typer.Typer()
        temporary.command(name=name, help=spec.help)(target)
        root = typer.main.get_command(temporary)
        command = root.commands[name] if isinstance(root, click.Group) else root
    command.name = name
    return command


@click.group(
    cls=LazyGroup,
    name="machina",
    invoke_without_command=True,
    help="Machina - self-hosted AI agent framework. Run `machina` to open the interactive UI.",
)
@click.option("--mode", help="Execution mode: ask|workspace|sandbox|full")
@click.pass_context
def lazy_app(ctx: click.Context, mode: str | None) -> None:
    """Open the interactive UI when no subcommand is given."""
    if mode is not None:
        from machina.cli.common import _apply_cli_mode

        _apply_cli_mode(mode)
    if ctx.invoked_subcommand is None:
        from machina.ui_launcher import launch_ui
        from machina.update import auto_update

        auto_update()
        raise SystemExit(launch_ui(mode=mode))


# Click's decorator creates a class instance without the ``specs`` constructor
# argument; bind the static table once at module import. Keeping one stable
# dict lets the per-command import cache live on the group.
lazy_app._specs = dict(_SPECS)  # type: ignore[attr-defined]
