"""Machina tools — registry and built-in tools.

Importing this package exposes the public ``Tool``/``ToolRegistry``/``registry``
contract without importing every built-in module. Tool modules register lazily
on the first registry access (or explicit submodule import), so lightweight
CLI/package imports do not pay for RAG, browser, embeddings, or other optional
dependencies they never use.
"""

from __future__ import annotations

import importlib
from typing import Any

from machina.tools.base import Tool, ToolRegistry, registry, set_tool_loader

_TOOL_MODULES = (
    "agents",  # delegate, list_agents
    "artifacts",  # artifact_read, artifact_search, artifact_describe
    "browser",  # browser_verify (local headless checks)
    "builtins",  # web_search, web_extract, search_files
    "code_intelligence",  # read-only workspace code intelligence
    "file",  # read_file, write_file, list_files
    "harness",  # read-only feature/configuration/learning inspection
    "mcp",  # lazy connector catalogs and exact remote calls
    "memory",  # remember, recall
    "questions",  # ask_user (interactive multiple-choice pause)
    "meta_harness",  # approval-gated external harness execution
    "rag",  # index_document, search_documents
    "repo",  # repo_stage, repo_activate
    "repomap",  # learn_directory
    "routing",  # route_explain, model_inventory
    "session_recall",  # transcript recall
    "skills",  # skill discovery and reading
    "terminal",  # terminal
    "web_crawl",  # bounded multi-page crawl into session RAG
    "web_research",  # session-scoped public web research
)

__all__ = [
    "Tool",
    "ToolRegistry",
    "registry",
    *(_TOOL_MODULES),
]


def _load_tool_modules() -> None:
    """Import every built-in tool module once (idempotent)."""
    for name in _TOOL_MODULES:
        importlib.import_module(f"{__name__}.{name}")


def __getattr__(name: str) -> Any:
    """Resolve tool module attributes lazily (``machina.tools.rag``, ...)."""
    if name in _TOOL_MODULES:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Advertise lazy module attributes without importing them."""
    return sorted(set(globals()) | set(_TOOL_MODULES))


# The registry owns the lazy load; direct submodule imports (`machina.tools.file`)
# still register just that module, and any registry access loads the rest.
set_tool_loader(
    _load_tool_modules,
    tuple(f"{__name__}.{name}" for name in _TOOL_MODULES),
)
