"""Small, dependency-light helpers shared by the turn loop.

These are the leaf pieces of ``core/agent.py``: instrumentation wrappers,
growth-progress fan-out, HTTP error classification, tool outcome labels,
pending assistant chunks, and the loop terminal marker. They have no turn
policy and can be tested independently of the agent class.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

from machina.core.assembler import get_token_count
from machina.core.background_tasks import joined_teardown
from machina.core.metrics import metrics
from machina.core.models import AgentResponse
from machina.core.session import session_manager
from machina.core.session_mode import turn_scope
from machina.observability.diagnostics import record_failure
from machina.observability.tracing import span
from machina.plugins.registry import plugin_registry

__all__ = [
    "instrument_run",
    "instrument_stream",
    "add_growth_listener",
    "remove_growth_listener",
    "notify_growth",
    "is_rate_limit_error",
    "is_transient_error",
    "tool_outcome",
    "assistant_chunk",
    "LoopTerminal",
]

_GrowthListener = Callable[[str, str, str], None]
_growth_listeners: list[_GrowthListener] = []


def instrument_run(func):
    @wraps(func)
    async def wrapper(self, session_id, user_message, verbose=True, **kwargs):
        start = time.monotonic()
        with span("agent.run", agent_id=self.agent_id, session_id=str(session_id)):
            try:
                with turn_scope():
                    response = await func(self, session_id, user_message, verbose, **kwargs)
            except Exception:
                metrics.record_error("agent.run")
                raise
        if not response.needs_approval:
            await self._complete_growth(response, session_id=session_id)
        metrics.record_latency("agent.run", (time.monotonic() - start) * 1000)
        metrics.increment_counter("agent.run.calls")
        session = await session_manager.get(session_id)
        await plugin_registry.dispatch("post_agent_run", session, response)
        return response

    return wrapper


def instrument_stream(func):
    @wraps(func)
    async def wrapper(self, session_id, user_message, verbose=True, **kwargs):
        start = time.monotonic()
        # R4-010/R4-064: the turn scope and the OTel stream span are owned by
        # the stream's driving transport (see TurnEngine.stream). Drivers that
        # pull each `__anext__` in its own task need both set before those
        # tasks are created: a contextvar/span entered inside this generator
        # would neither survive across steps nor detach in its own context.
        try:
            async with joined_teardown(
                stream=func(self, session_id, user_message, verbose, **kwargs)
            ) as events:
                async for event in events:
                    if event.type == "done":
                        if event.response is not None and not event.response.needs_approval:
                            await self._complete_growth(event.response, session_id=session_id)
                        metrics.record_latency("agent.run", (time.monotonic() - start) * 1000)
                        metrics.increment_counter("agent.run.calls")
                        session = await session_manager.get(session_id)
                        await plugin_registry.dispatch("post_agent_run", session, event.response)
                    yield event
        except Exception:
            metrics.record_error("agent.run")
            raise

    return wrapper


def add_growth_listener(fn: _GrowthListener) -> None:
    """Register a growth-progress callback (transports unregister per turn)."""
    _growth_listeners.append(fn)


def remove_growth_listener(fn: _GrowthListener) -> None:
    """Unregister a growth-progress callback."""
    try:
        _growth_listeners.remove(fn)
    except ValueError:
        pass


def notify_growth(session_id: uuid.UUID | str, stage: str, detail: str = "") -> None:
    """Fan out one growth-progress notice; never raises."""
    for fn in list(_growth_listeners):
        try:
            fn(str(session_id), stage, detail)
        except Exception as _boundary_error:
            record_failure("agent.growth.notify", _boundary_error)


def is_rate_limit_error(exc: BaseException) -> bool:
    """True for HTTP 429 failures (duck-typed so no httpx import is needed)."""
    return getattr(getattr(exc, "response", None), "status_code", None) == 429


def is_transient_error(exc: BaseException) -> bool:
    """True for HTTP 502, 503, 504 failures (transient server errors)."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (502, 503, 504)


def tool_outcome(item: dict[str, Any]) -> str:
    """Truthful per-call outcome label: a needs-approval pause is ``"paused"``."""
    preview = str(item.get("result") or "")
    if item.get("_needs_approval") or item.get("_needs_approvals"):
        return "paused"
    if item.get("_delegation_status") == "waiting_approval":
        return "paused"
    if item.get("_delegation_status") in {"queued", "running", "interrupted", "pending"}:
        return item["_delegation_status"]
    if item.get("_denied") or preview.startswith("Permission denied:"):
        return "denied"
    if item.get("_outcome_unknown"):
        return "outcome_unknown"
    if item.get("_failed") or preview.startswith("Error:"):
        return "failed"
    return "completed"


def assistant_chunk(
    session_id: uuid.UUID, agent_id: str, content: str, *, interrupted: bool = False
) -> dict[str, Any]:
    """Pending-chunk record for one assistant message (all loops)."""
    payload: dict[str, Any] = {"content": content}
    if interrupted:
        payload["interrupted"] = True
    return {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "agent_id": agent_id,
        "chunk_type": "assistant_message",
        "payload": payload,
        "token_count": get_token_count(content),
    }


@dataclass
class LoopTerminal:
    """Loop-internal terminal marker; adapters turn it into ``done``."""

    response: AgentResponse
