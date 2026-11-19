"""Lock the CLI command surface after the god-module split.

Command names, hierarchy and registration order are part of the public CLI
surface (help output and shell completion), so the composition root must keep
registering the same commands in the same order even though their callbacks
now live in feature modules.
"""

from __future__ import annotations

import click
from typer.main import get_command

from machina import cli
from machina.cli import common

# Original definition order: Click lists commands in insertion order.
COMMANDS = (
    "chat",
    "repl",
    "status",
    "mode",
    "sessions",
    "sessions-search",
    "usage",
    "route",
    "routes",
    "export",
    "fork",
    "delete",
    "context",
    "compress",
    "learn",
    "curator",
    "hub",
    "doctor",
    "init",
    "setup",
    "version",
    "serve",
    "stop",
    "config",
    "config-set",
    "memory-list",
    "memory-search",
    "memory-forget",
    "memory-pending",
    "memory-approve",
    "memory-reject",
    "memory-approve-all",
    "memory-reject-all",
    "memory-stats",
    "user-profile",
    "user-profile-update",
    "user-profile-list",
    "uninstall",
)
GROUPS = (
    "auth",
    "growth",
    "host",
    "harnesses",
    "interop",
    "providers",
    "optimization",
    "skills",
)


def _root_group():
    group = get_command(cli.app)
    # Click's Group and Typer's order-preserving TyperGroup both expose these.
    assert hasattr(group, "commands")
    return group


def test_cli_exposes_the_same_command_set_in_the_same_order() -> None:
    command_names = tuple(info.name for info in cli.app.registered_commands)
    group_names = tuple(info.name for info in cli.app.registered_groups)

    assert command_names == COMMANDS
    assert group_names == GROUPS

    group = _root_group()
    exposed = group.list_commands(click.Context(group))
    assert set(exposed) == set(COMMANDS) | set(GROUPS)


def test_skills_subcommand_set_is_unchanged() -> None:
    group = _root_group()
    skills = group.commands["skills"]

    assert hasattr(skills, "list_commands")
    assert skills.list_commands(click.Context(skills)) == ["enrich"]


def test_package_still_reexports_the_split_callbacks_and_helpers() -> None:
    # Growth commands import these lazily from the package, and external
    # callers/tests have historically used ``machina.cli.<command>``.
    assert cli._run is common._run
    assert cli._resolve_session is common._resolve_session
    assert callable(cli.chat)
    assert callable(cli.memory_list)
    assert callable(cli.setup)
    assert callable(cli.uninstall)
