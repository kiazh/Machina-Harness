"""Gateway route preview and connected-model inventory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from machina.core.config import config
from machina.core.routing import build_inventory, route_from_session
from machina.core.routing.types import COST_TIERS, ORCHESTRATION_MODES, RoutingError
from machina.gateway.errors import INVALID_PARAMS, RpcError

if TYPE_CHECKING:
    from machina.gateway.server import Gateway

_MAX_PROMPT_CHARS = 20_000


def _mode(params: dict[str, Any]) -> str | None:
    value = params.get("mode")
    if value is None or value == "":
        return None
    mode = str(value).strip().lower()
    if mode not in ORCHESTRATION_MODES:
        raise RpcError(INVALID_PARAMS, f"mode must be {'|'.join(ORCHESTRATION_MODES)}")
    return mode


async def model_route(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic connected-model route for one prompt."""
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise RpcError(INVALID_PARAMS, "prompt must be a non-empty string")
    if len(prompt) > _MAX_PROMPT_CHARS:
        raise RpcError(INVALID_PARAMS, f"prompt must be at most {_MAX_PROMPT_CHARS} characters")
    worker_count = params.get("workerCount")
    if worker_count is not None:
        try:
            worker_count = int(worker_count)
        except (TypeError, ValueError):
            raise RpcError(INVALID_PARAMS, "workerCount must be an integer") from None
        if not 0 <= worker_count <= 64:
            raise RpcError(INVALID_PARAMS, "workerCount must be between 0 and 64")
    cost_tier = params.get("costTier")
    if cost_tier is not None and str(cost_tier) not in COST_TIERS:
        raise RpcError(INVALID_PARAMS, f"costTier must be {'|'.join(COST_TIERS)}")
    session = None
    session_id = params.get("sessionId")
    if session_id:
        from machina.core.session import session_manager

        try:
            session = await session_manager.get(session_id)
        except Exception:
            session = None
    try:
        route = route_from_session(
            session,
            prompt,
            config=config,
            mode=_mode(params),
            worker_count=worker_count or None,
            cost_tier=cost_tier,
        )
    except RoutingError as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    return {"route": route.to_dict(), "explanation": route.explain()}


async def model_inventory(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Return the connected-model inventory without any provider call."""
    inventory = build_inventory(config)
    return {"inventory": inventory.to_dict()}


__all__ = ["model_inventory", "model_route"]
