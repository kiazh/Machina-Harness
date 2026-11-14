"""Read-only model routing inspection tools."""

from __future__ import annotations

import json

from machina.core.config import config
from machina.core.exceptions import ToolError
from machina.core.routing import build_inventory, route_from_session, route_task
from machina.tools.base import registry


@registry.register(
    name="route_explain",
    description=(
        "Inspect the connected-model inventory and the role-specific route "
        "Machina would choose for a prompt. Read-only: no provider call, no "
        "delegation. Useful for understanding planner/worker/supervisor selection."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Prompt to route"},
            "mode": {
                "type": "string",
                "description": "single_model or multi_model (default: config)",
            },
            "worker_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 64,
                "description": "Planned workers; 0 uses auto sizing",
            },
            "json_output": {
                "type": "boolean",
                "description": "Return the full decision JSON instead of the explanation",
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
)
async def route_explain(
    prompt: str,
    mode: str | None = None,
    worker_count: int = 0,
    json_output: bool = False,
) -> str:
    """Explain the current connected-model route for a prompt."""
    if not str(prompt or "").strip():
        raise ToolError("prompt must not be empty")
    try:
        from machina.core.session import session_manager
        from machina.tools.agents import current_session_id

        session_id = current_session_id.get()
        session = await session_manager.get(session_id) if session_id is not None else None
        decision = route_from_session(
            session,
            prompt,
            config=config,
            mode=mode,
            worker_count=worker_count or None,
        )
    except Exception as error:
        try:
            decision = route_task(
                prompt,
                config=config,
                mode=mode,
                worker_count=worker_count or None,
            )
        except Exception:
            raise ToolError(f"routing unavailable: {type(error).__name__}") from None
    if json_output:
        return json.dumps(decision.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return decision.explain()


@registry.register(
    name="model_inventory",
    description=(
        "List connected models with their role-fit scores, context, tool support, "
        "and whether the route is native/proxy/MCP. Read-only, no network call."
    ),
    parameters={
        "type": "object",
        "properties": {
            "json_output": {
                "type": "boolean",
                "description": "Return full JSON instead of a compact table",
            }
        },
        "additionalProperties": False,
    },
)
async def model_inventory(json_output: bool = False) -> str:
    """Inspect the connected-model inventory without making a model call."""
    inventory = build_inventory(config)
    if json_output:
        return json.dumps(inventory.to_dict(), ensure_ascii=False, separators=(",", ":"))
    lines = []
    for route in sorted(inventory.routes, key=lambda item: (item.profile.provider, item.profile.model)):
        profile = route.profile
        roles = ",".join(
            f"{role}:{profile.role_scores.get(role, 0):.2f}"
            for role in ("planner", "supervisor", "worker", "utility")
        )
        cost = "free" if profile.free else (
            f"${profile.input_cost_per_mtok:g}/${profile.output_cost_per_mtok:g}"
            if profile.input_cost_per_mtok is not None
            and profile.output_cost_per_mtok is not None
            else "cost?"
        )
        lines.append(
            f"- {profile.provider}/{profile.model} [{route.transport}] "
            f"{roles} tools={'yes' if profile.tools else 'no'} "
            f"ctx={profile.context_window} {cost}"
        )
    if not lines:
        lines.append("No connected models found. Configure a provider key or run /models.")
    return "\n".join(lines[:120])


__all__ = ["model_inventory", "route_explain"]
