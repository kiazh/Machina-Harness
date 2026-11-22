"""Machina CLI package with lazy, explicit re-exports.

Importing :mod:`machina.cli` must stay cheap for the console entry point and
for one-shot commands. The eager composition root lives in
:mod:`machina.cli._app`; this module exposes the historical public names via
PEP 562 so ``from machina.cli import app`` still builds the full Typer app,
while ``from machina.cli import output`` imports only the small output module.

The console entry point uses :mod:`machina.cli.lazy`, which maps command names
to modules and imports exactly one command (or command group) on invocation.
"""

from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # Composition root
    "app": ("machina.cli._app", "app"),
    "main": ("machina.cli._app", "main"),
    # Shared helpers and constants
    "EXIT_FAILED": ("machina.cli.common", "EXIT_FAILED"),
    "EXIT_OK": ("machina.cli.common", "EXIT_OK"),
    "EXIT_PAUSED": ("machina.cli.common", "EXIT_PAUSED"),
    "VALID_MODES": ("machina.cli.common", "VALID_MODES"),
    "_apply_cli_mode": ("machina.cli.common", "_apply_cli_mode"),
    "_db_connected": ("machina.cli.common", "_db_connected"),
    "_normalize_mode": ("machina.cli.common", "_normalize_mode"),
    "_parse_uuid": ("machina.cli.common", "_parse_uuid"),
    "_resolve_session": ("machina.cli.common", "_resolve_session"),
    "_run": ("machina.cli.common", "_run"),
    "_sync_env_shadow": ("machina.cli.common", "_sync_env_shadow"),
    "_report_terminal_response": ("machina.cli.chat", "_report_terminal_response"),
    # Commands
    "audit": ("machina.cli.audit", "audit"),
    "chat": ("machina.cli.chat", "chat"),
    "api": ("machina.cli.api", "api"),
    "run": ("machina.cli.parity", "run"),
    "models": ("machina.cli.parity", "models"),
    "update": ("machina.cli.parity", "update"),
    "upgrade": ("machina.cli.parity", "update"),
    "undo": ("machina.cli.parity", "undo"),
    "session_app": ("machina.cli.parity", "session_app"),
    "snapshots_app": ("machina.cli.snapshots", "app"),
    "mcp_app": ("machina.cli.mcp", "app"),
    "plugin_app": ("machina.cli.plugin", "app"),
    "debug_app": ("machina.cli.debug", "app"),
    "repl": ("machina.cli.chat", "repl"),
    "compress": ("machina.cli.sessions", "compress"),
    "context": ("machina.cli.sessions", "context"),
    "delete": ("machina.cli.sessions", "delete"),
    "export": ("machina.cli.sessions", "export"),
    "fork": ("machina.cli.sessions", "fork"),
    "list_sessions": ("machina.cli.sessions", "list_sessions"),
    "route_command": ("machina.cli.sessions", "route_command"),
    "routes_command": ("machina.cli.sessions", "routes_command"),
    "sessions_search": ("machina.cli.sessions", "sessions_search"),
    "usage_command": ("machina.cli.sessions", "usage_command"),
    "curator": ("machina.cli.skills", "curator"),
    "hub": ("machina.cli.skills", "hub"),
    "learn": ("machina.cli.skills", "learn"),
    "skills": ("machina.cli.skills", "skills"),
    "skills_app": ("machina.cli.skills", "skills_app"),
    "skills_enrich": ("machina.cli.skills", "skills_enrich"),
    "skills_list": ("machina.cli.skills", "skills_list"),
    "config_set": ("machina.cli.maintenance", "config_set"),
    "config_show": ("machina.cli.maintenance", "config_show"),
    "doctor": ("machina.cli.maintenance", "doctor"),
    "init": ("machina.cli.maintenance", "init"),
    "serve": ("machina.cli.maintenance", "serve"),
    "set_mode": ("machina.cli.maintenance", "set_mode"),
    "setup": ("machina.cli.maintenance", "setup"),
    "status": ("machina.cli.maintenance", "status"),
    "stop_all": ("machina.cli.maintenance", "stop_all"),
    "uninstall": ("machina.cli.maintenance", "uninstall"),
    "version": ("machina.cli.maintenance", "version"),
    "memory_approve": ("machina.cli.memory", "memory_approve"),
    "memory_approve_all": ("machina.cli.memory", "memory_approve_all"),
    "memory_forget": ("machina.cli.memory", "memory_forget"),
    "memory_list": ("machina.cli.memory", "memory_list"),
    "memory_pending": ("machina.cli.memory", "memory_pending"),
    "memory_reject": ("machina.cli.memory", "memory_reject"),
    "memory_reject_all": ("machina.cli.memory", "memory_reject_all"),
    "memory_search": ("machina.cli.memory", "memory_search"),
    "memory_stats": ("machina.cli.memory", "memory_stats"),
    "user_profile_cmd": ("machina.cli.memory", "user_profile_cmd"),
    "user_profile_list": ("machina.cli.memory", "user_profile_list"),
    "user_profile_update": ("machina.cli.memory", "user_profile_update"),
    # Command groups
    "host_app": ("machina.cli.host", "host_app"),
    # Modules and shared objects
    "console": ("machina.cli.output", "console"),
    "db": ("machina.db.connection", "db"),
    "interop_mod": ("machina.interop", ""),
    "output": ("machina.cli.output", ""),
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    spec = _LAZY_EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = spec
    module = import_module(module_name)
    value: Any = module if not attribute else getattr(module, attribute)
    globals()[name] = value
    return value


class _LazyModule(ModuleType):
    """Return the public callable even after Python binds a submodule name.

    ``machina.cli.chat`` names both a command callback and its feature module;
    importing the module would otherwise make the module object shadow the
    historical callback re-export.
    """

    def __getattribute__(self, name: str) -> Any:
        if name in _LAZY_EXPORTS:
            module_name, attribute = _LAZY_EXPORTS[name]
            module = import_module(module_name)
            value: Any = module if not attribute else getattr(module, attribute)
            globals()[name] = value
            return value
        return object.__getattribute__(self, name)


# PEP 562 plus a module subclass: the subclass handles names that the import
# system has already populated with a submodule object.
sys.modules[__name__].__class__ = _LazyModule


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
