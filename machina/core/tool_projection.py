"""Wire-level tool schema projection.

The registry keeps complete, validated schemas and descriptions for execution,
tests, and the capability catalog. Provider requests do not need every
sentence of that documentation in every turn: the model needs the tool name,
the action, required arguments, and constraints. This projection trims prose
at the wire boundary while preserving every parameter, type, required field,
enum, range, and default.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from machina.core.models import ToolDefinition

__all__ = [
    "CORE_TOOL_NAMES",
    "active_tool_definitions",
    "compact_description",
    "compact_tool_definitions",
    "mark_tools_loaded",
    "reset_dynamic_tool_state",
]

# The small always-on surface. These are the tools needed for orientation,
# local inspection/edit/test loops, memory, and interactive clarification.
# Everything else is loaded on demand so a 45-tool registry does not consume
# the stable prompt on every turn.
CORE_TOOL_NAMES = frozenset(
    {
        "read_file",
        "write_file",
        "list_files",
        "search_files",
        "terminal",
        "terminal_job",
        "ask_user",
        "tool_load",
    }
)

_loaded_tool_names: ContextVar[frozenset[str]] = ContextVar(
    "machina_loaded_tool_names", default=frozenset()
)
_allowed_tool_names: ContextVar[frozenset[str] | None] = ContextVar(
    "machina_allowed_tool_names", default=None
)


def reset_dynamic_tool_state() -> None:
    """Start a turn with only the core schemas; tool_load adds more."""
    _loaded_tool_names.set(frozenset())
    _allowed_tool_names.set(None)


def set_dynamic_allowed_tools(names: list[str] | None) -> None:
    _allowed_tool_names.set(None if names is None else frozenset(names))


def mark_tools_loaded(names: list[str], available: set[str]) -> tuple[list[str], list[str]]:
    """Record requested schemas; never widen an explicit allow-list."""
    allowed = _allowed_tool_names.get()
    loaded: list[str] = []
    rejected: list[str] = []
    for name in names:
        if name in available and (allowed is None or name in allowed):
            loaded.append(name)
        else:
            rejected.append(name)
    if loaded:
        _loaded_tool_names.set(_loaded_tool_names.get() | frozenset(loaded))
    return loaded, rejected


def active_tool_definitions(definitions: list[ToolDefinition]) -> list[ToolDefinition]:
    """Project a registry definition list to the current turn's tool surface."""
    loaded = _loaded_tool_names.get()
    selected = [
        definition
        for definition in definitions
        if definition.name in CORE_TOOL_NAMES
        or definition.name in loaded
        or definition.name == "tool_load"
    ]
    # Fail open for tiny/custom registries (tests and embedders) so a registry
    # that has no core names is not silently emptied.
    return selected or definitions


_DEFAULT_TOOL_CHARS = 160
_DEFAULT_PARAM_CHARS = 80


def compact_description(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    cut = text[: max(1, limit - 3)]
    for separator in (". ", "; ", ", "):
        index = cut.rfind(separator)
        if index >= limit // 2:
            return cut[: index + 1].rstrip()
    return cut.rstrip() + "..."


def _compact_schema(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        for child_key, child in value.items():
            if child_key == "description" and isinstance(child, str):
                # A description that merely repeats the parameter name adds no
                # information; drop it and keep meaningful prose bounded.
                if child.strip().lower().replace("_", " ") == key.lower().replace("_", " "):
                    continue
                projected[child_key] = compact_description(child, _DEFAULT_PARAM_CHARS)
            else:
                projected[child_key] = _compact_schema(child, child_key)
        return projected
    if isinstance(value, list):
        return [_compact_schema(item, key) for item in value]
    return value


def compact_tool_definitions(definitions: list[ToolDefinition]) -> list[ToolDefinition]:
    """Return provider-safe copies with bounded prose and full JSON constraints."""
    return [
        ToolDefinition(
            name=definition.name,
            # tool_load carries the discoverable catalog; truncating it would
            # hide the names the model needs to request. It is one schema, and
            # the catalog is still far smaller than the eager 46-tool surface.
            description=(
                definition.description
                if definition.name == "tool_load"
                else compact_description(definition.description, _DEFAULT_TOOL_CHARS)
            ),
            parameters=_compact_schema(definition.parameters),
        )
        for definition in definitions
    ]
