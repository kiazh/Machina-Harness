"""On-demand tool schema loading for the default agent surface."""

from __future__ import annotations

from machina.core.tool_projection import (
    CORE_TOOL_NAMES,
    currently_visible_tool_names,
    mark_tools_loaded,
)
from machina.tools.base import registry


def _available_catalog() -> str:
    # Merge the live registry with the generated inventory. Some import orders
    # (and lazy tool registration) leave the registry partially populated at
    # module import; the catalog must still advertise every loadable tool.
    from machina.core.capability_catalog import TOOL_GROUPS

    names = set(registry._registered_tools)
    names |= {name for group in TOOL_GROUPS.values() for name in group}
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
    requested = list(dict.fromkeys(tools))
    visible = currently_visible_tool_names(available)
    already = [name for name in requested if name in visible]
    pending = [name for name in requested if name not in visible]
    loaded, rejected = mark_tools_loaded(pending, available)
    parts: list[str] = []
    if loaded:
        parts.append(
            "Loaded schemas for: "
            + ", ".join(loaded)
            + ". They are callable on this next model step; call them directly."
        )
    if already:
        parts.append(
            "Already available in the current tool surface: "
            + ", ".join(already)
            + ". No reload is needed; call them directly."
        )
    if rejected:
        parts.append(
            "Not loaded (unknown or outside this agent's allow-list): " + ", ".join(rejected)
        )
    return " ".join(parts) or "No tools requested."
