"""Optional OTLP/HTTP export for completed in-process turn traces.

The native trace registry remains authoritative and dependency-free. When
``MACHINA_OTLP_ENDPOINT`` is set, completed turns are projected to the
OpenTelemetry JSON wire shape and posted to an OTLP/HTTP collector. Export is
best effort and never gates or retries a turn.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any

import httpx

from machina import __version__
from machina.observability import traces

logger = logging.getLogger(__name__)

_EXPORTED_TURNS: set[str] = set()
_TASKS: set[Any] = set()
_INSTALLED = False
_OTLP_HTTP_JSON = "/v1/traces"


def _hex(value: str, length: int) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:length]


def _attribute(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        encoded = {"boolValue": value}
    elif isinstance(value, int):
        encoded = {"intValue": str(value)}
    elif isinstance(value, float):
        encoded = {"doubleValue": value}
    else:
        encoded = {"stringValue": str(value)}
    return {"key": key, "value": encoded}


def turn_to_otlp(turn: traces.TurnTrace, *, now_ns: int | None = None) -> dict[str, Any]:
    """Project one retained turn to an OTLP/HTTP JSON request body."""
    exported_at = now_ns or time.time_ns()
    root_started = turn.root.started_ms if turn.root is not None else time.monotonic() * 1000.0
    epoch_started = exported_at - int(
        max(0.0, time.monotonic() * 1000.0 - root_started) * 1_000_000
    )
    spans = []
    for span in turn.spans:
        started = epoch_started + int(max(0.0, span.started_ms - root_started) * 1_000_000)
        ended = started + int((span.duration_ms or 0) * 1_000_000)
        item: dict[str, Any] = {
            "traceId": _hex(turn.turn_id, 32),
            "spanId": _hex(f"{turn.turn_id}:{span.id}", 16),
            "name": span.name,
            "kind": 1,
            "startTimeUnixNano": str(max(0, started)),
            "endTimeUnixNano": str(max(0, ended)),
            "attributes": [_attribute(key, value) for key, value in span.attributes.items()],
            "status": {
                "code": 2
                if span.status == traces.STATUS_ERROR
                else 1
                if span.status == traces.STATUS_OK
                else 0
            },
        }
        if span.parent_id:
            item["parentSpanId"] = _hex(f"{turn.turn_id}:{span.parent_id}", 16)
        spans.append(item)
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attribute("service.name", "machina"),
                        _attribute("service.version", __version__),
                        _attribute("machina.session.id", turn.session_id),
                    ]
                },
                "scopeSpans": [{"scope": {"name": "machina.turns"}, "spans": spans}],
            }
        ]
    }


async def _post(endpoint: str, body: dict[str, Any]) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            response = await client.post(endpoint.rstrip("/") + _OTLP_HTTP_JSON, json=body)
            if response.status_code >= 400:
                logger.warning("OTLP export failed with HTTP %s", response.status_code)
    except Exception as error:
        logger.warning("OTLP export failed: %s", type(error).__name__)


def _sink(event: str, payload: dict[str, Any]) -> None:
    if event != "turn_ended":
        return
    turn = traces.get_turn(str(payload.get("turn_id") or ""))
    if turn is None or turn.turn_id in _EXPORTED_TURNS:
        return
    endpoint = os.environ.get("MACHINA_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return
    _EXPORTED_TURNS.add(turn.turn_id)
    if len(_EXPORTED_TURNS) > 512:
        _EXPORTED_TURNS.pop()
    import asyncio

    try:
        task = asyncio.get_running_loop().create_task(_post(endpoint, turn_to_otlp(turn)))
    except RuntimeError:
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def install_otlp_exporter() -> bool:
    """Install the sink when an endpoint is configured; idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return True
    if not os.environ.get("MACHINA_OTLP_ENDPOINT", "").strip():
        return False
    traces.add_sink(_sink)
    _INSTALLED = True
    return True


def shutdown_otlp_exporter() -> None:
    global _INSTALLED
    if _INSTALLED:
        traces.remove_sink(_sink)
        _INSTALLED = False
    for task in list(_TASKS):
        task.cancel()
    _TASKS.clear()
    _EXPORTED_TURNS.clear()
