"""Minimal A2A 1.0 task bridge over Machina sessions and turns.

This is an HTTP adapter, not a second orchestration engine. A2A tasks map to
durable Machina sessions; ``message/send`` starts the existing turn lifecycle;
``tasks/get`` reads the same retained transcript; ``tasks/cancel`` uses the
existing prompt-cancel path. Streaming and push notifications are advertised
as unsupported rather than implemented as separate, divergent transports.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from machina import __version__
from machina.api.auth import require_api_key
from machina.core.context import context_manager
from machina.core.session import session_manager
from machina.core.turns import turn_active
from machina.gateway.errors import RpcError
from machina.gateway.serializers import history_from_chunks
from machina.memory.redaction import redact_secrets

router = APIRouter()

_A2A_STATE_KEY = "a2a"
_A2A_TIMEOUT_SECONDS = 300.0
_A2A_POLL_SECONDS = 0.25


def _now() -> str:
    return datetime.now(UTC).isoformat()


def agent_card(base_url: str) -> dict[str, Any]:
    return {
        "protocolVersion": "1.0",
        "name": "Machina",
        "description": "Self-hosted coding-agent harness with durable sessions, approvals, tools, and memory.",
        "url": f"{base_url.rstrip('/')}/a2a",
        "version": __version__,
        "preferredTransport": "JSONRPC",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {"id": "code", "name": "Code", "description": "Inspect, edit, and verify a workspace."},
            {
                "id": "shell",
                "name": "Shell",
                "description": "Run approved local commands and jobs.",
            },
            {"id": "research", "name": "Research", "description": "Search and verify the web."},
            {"id": "mcp", "name": "MCP", "description": "Use authorized MCP providers and tools."},
        ],
    }


def _text_from_parts(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    values: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("kind") == "text" and isinstance(part.get("text"), str):
            values.append(part["text"])
    return "\n".join(value.strip() for value in values if value.strip())


def _message_id() -> str:
    return uuid.uuid4().hex


async def _task_state(session_id: uuid.UUID) -> tuple[str, dict[str, Any]]:
    session = await session_manager.get_fresh(session_id)
    if session is None:
        raise HTTPException(404, "task not found")
    marker = session.state.get(_A2A_STATE_KEY) if isinstance(session.state, dict) else None
    marker = marker if isinstance(marker, dict) else {}
    if await turn_active(session_id):
        return "working", marker
    state = str(marker.get("state") or "completed")
    # A non-blocking send stores "working" before the turn starts. Once the
    # durable claim is gone, the task is terminal; leaving the stale marker
    # would make tasks/get report "working" forever.
    return ("completed" if state == "working" else state), marker


def _task_payload(
    session_id: uuid.UUID, state: str, marker: dict[str, Any], history: list[dict[str, Any]]
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": str(session_id),
        "contextId": str(session_id),
        "status": {
            "state": state,
            "timestamp": str(marker.get("updatedAt") or _now()),
        },
    }
    if history:
        task["history"] = history[-20:]
        latest = next((entry for entry in reversed(history) if entry["role"] == "assistant"), None)
        if latest:
            payload = latest["content"]
            task["artifacts"] = [
                {
                    "artifactId": hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[
                        :16
                    ],
                    "name": "answer",
                    "parts": [{"kind": "text", "text": payload}],
                }
            ]
    return task


async def _history(session_id: uuid.UUID) -> list[dict[str, Any]]:
    chunks = await context_manager.get_chunks(session_id, limit=100, include_embedding=False)
    history = history_from_chunks(chunks)
    return [
        {
            "role": "agent" if entry.get("role") == "assistant" else "user",
            "messageId": hashlib.sha256(
                f"{index}:{entry.get('role')}:{entry.get('content')}".encode("utf-8", "replace")
            ).hexdigest()[:16],
            "parts": [
                {
                    "kind": "text",
                    "text": redact_secrets(str(entry.get("content") or "")).text,
                }
            ],
        }
        for index, entry in enumerate(history)
        if entry.get("content")
    ]


async def _update_marker(session_id: uuid.UUID, state: str, message_id: str = "") -> None:
    await session_manager.update_state_fields(
        session_id,
        {
            _A2A_STATE_KEY: {
                "state": state,
                "messageId": message_id,
                "updatedAt": _now(),
            }
        },
    )


async def _wait_for_terminal(session_id: uuid.UUID, timeout: float) -> bool:
    deadline = time.monotonic() + min(max(timeout, 1.0), _A2A_TIMEOUT_SECONDS)
    while time.monotonic() < deadline:
        if not await turn_active(session_id):
            return True
        await asyncio.sleep(_A2A_POLL_SECONDS)
    return False


def _request_id(body: dict[str, Any]) -> str | int | None:
    value = body.get("id")
    return value if isinstance(value, (str, int)) else None


def _error(request_id: str | int | None, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def _send(request: Request, params: dict[str, Any]) -> dict[str, Any]:
    message = params.get("message")
    if not isinstance(message, dict):
        raise ValueError("message is required")
    text = _text_from_parts(message.get("parts"))
    if not text:
        raise ValueError("message must contain at least one text part")
    if len(text) > 20_000:
        raise ValueError("message text is too long")
    message_id = str(message.get("messageId") or _message_id())
    gateway = getattr(request.app.state, "gateway", None)
    if gateway is None:
        raise RuntimeError("A2A gateway not initialized")

    task_ref = params.get("taskId") or message.get("taskId")
    session_id: uuid.UUID
    if task_ref:
        try:
            session_id = uuid.UUID(str(task_ref))
        except ValueError:
            raise ValueError("taskId must be a Machina session id") from None
        if await session_manager.get_fresh(session_id) is None:
            raise ValueError("task not found")
    else:
        created = await gateway.call(
            "session.create",
            {"title": f"A2A: {text[:60]}", "agent": "harness"},
        )
        session_id = uuid.UUID(str(created["session"]["id"]))
    await _update_marker(session_id, "working", message_id)
    result = await gateway.call(
        "prompt.submit",
        {"sessionId": str(session_id), "text": text, "protocolVersion": 2},
    )
    blocking = bool((params.get("configuration") or {}).get("blocking", False))
    if blocking:
        finished = await _wait_for_terminal(session_id, _A2A_TIMEOUT_SECONDS)
        state, marker = await _task_state(session_id)
        if finished and state == "working":
            await _update_marker(session_id, "completed", message_id)
        state, marker = await _task_state(session_id)
    else:
        state, marker = "working", {"state": "working", "updatedAt": _now()}
    history = await _history(session_id)
    task = _task_payload(session_id, state, marker, history)
    task["metadata"] = {"turnId": result.get("turnId", "")}
    return task


async def _get(params: dict[str, Any]) -> dict[str, Any]:
    task_ref = params.get("id") or params.get("taskId")
    try:
        session_id = uuid.UUID(str(task_ref))
    except (TypeError, ValueError):
        raise ValueError("id must be a Machina session id") from None
    state, marker = await _task_state(session_id)
    return _task_payload(session_id, state, marker, await _history(session_id))


async def _cancel(request: Request, params: dict[str, Any]) -> dict[str, Any]:
    task_ref = params.get("id") or params.get("taskId")
    try:
        session_id = uuid.UUID(str(task_ref))
    except (TypeError, ValueError):
        raise ValueError("id must be a Machina session id") from None
    gateway = getattr(request.app.state, "gateway", None)
    if gateway is None:
        raise RuntimeError("A2A gateway not initialized")
    await gateway.call("prompt.cancel", {"sessionId": str(session_id)})
    await _update_marker(session_id, "canceled")
    state, marker = await _task_state(session_id)
    return _task_payload(session_id, "canceled", marker, await _history(session_id))


@router.get("/.well-known/agent-card.json", include_in_schema=False)
async def a2a_agent_card(request: Request) -> dict[str, Any]:
    return agent_card(str(request.base_url))


@router.post("/a2a", dependencies=[Depends(require_api_key)])
async def a2a_dispatch(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return _error(None, -32700, "Parse error")
    if not isinstance(body, dict):
        return _error(None, -32600, "Invalid Request")
    request_id = _request_id(body)
    method = body.get("method")
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    try:
        if method == "message/send":
            return {"jsonrpc": "2.0", "id": request_id, "result": await _send(request, params)}
        if method == "tasks/get":
            return {"jsonrpc": "2.0", "id": request_id, "result": await _get(params)}
        if method == "tasks/cancel":
            return {"jsonrpc": "2.0", "id": request_id, "result": await _cancel(request, params)}
        return _error(request_id, -32601, "Method not found")
    except ValueError as error:
        return _error(request_id, -32602, str(error))
    except RpcError as error:
        return _error(request_id, -32000, error.message)
    except HTTPException:
        raise
    except Exception:
        return _error(request_id, -32603, "Internal error")
