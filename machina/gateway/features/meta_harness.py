"""Call-free meta-harness route preview and capability listing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from machina.core.meta_harness import harness_cards, route_prompt
from machina.gateway.errors import INVALID_PARAMS, RpcError

if TYPE_CHECKING:
    from machina.gateway.server import Gateway

_MAX_PROMPT_CHARS = 20_000


async def meta_route(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic local route for one prompt. Never calls a provider."""
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise RpcError(INVALID_PARAMS, "prompt must be a non-empty string")
    if len(prompt) > _MAX_PROMPT_CHARS:
        raise RpcError(INVALID_PARAMS, f"prompt must be at most {_MAX_PROMPT_CHARS} characters")
    route = route_prompt(prompt)
    return {"route": route.to_dict()}


async def meta_harnesses(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    """List the local capability registry with spawn-free availability."""
    return {"harnesses": [card.to_dict() for card in harness_cards()]}
