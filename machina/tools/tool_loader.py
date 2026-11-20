"""On-demand tool schema loading for the default agent surface."""

from __future__ import annotations

from machina.core.tool_projection import CORE_TOOL_NAMES, mark_tools_loaded
from machina.tools.base import registry


def _available_catalog() -> str:
    names = set(registry._registered_tools)
    if not names:
        # Direct-module import (without the registry's lazy loader) still gets
        # a useful catalog from the generated capability inventory.
        from machina.core.capability_catalog import TOOL_GROUPS

        names = {name for group in TOOL_GROUPS.values() for name in group}
    names.discard("tool_load")
    return ", ".join(sorted(names - CORE_TOOL_NAMES))


@registry.register(
    description=(
        "Load full schemas for tools that are not in the current tool surface. "
        "Call with tools=[name, ...] using names from: "
        + _available_catalog()
        + ". The loaded schemas are available on the next model step."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 12,
                "description": "Tool names to load",
            }
        },
        "required": ["tools"],
        "additionalProperties": False,
    },
)
def tool_load(tools: list[str]) -> str:
    """Load schemas by name; execution permissions are still enforced later."""
    available = set(registry._tools)
    loaded, rejected = mark_tools_loaded(list(tools), available)
    parts: list[str] = []
    if loaded:
        parts.append(
            "Loaded schemas for: "
            + ", ".join(loaded)
            + ". They are available on the next model step."
        )
    if rejected:
        parts.append(
            "Not loaded (unknown or outside this agent's allow-list): " + ", ".join(rejected)
        )
    return " ".join(parts) or "No tools requested."
