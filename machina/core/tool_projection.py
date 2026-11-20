"""Wire-level tool schema projection.

The registry keeps complete, validated schemas and descriptions for execution,
tests, and the capability catalog. Provider requests do not need every
sentence of that documentation in every turn: the model needs the tool name,
the action, required arguments, and constraints. This projection trims prose
at the wire boundary while preserving every parameter, type, required field,
enum, range, and default.
"""

from __future__ import annotations

from typing import Any

from machina.core.models import ToolDefinition

__all__ = ["compact_tool_definitions", "compact_description"]

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
            description=compact_description(definition.description, _DEFAULT_TOOL_CHARS),
            parameters=_compact_schema(definition.parameters),
        )
        for definition in definitions
    ]
