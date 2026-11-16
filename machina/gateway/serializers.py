"""Shared presentation helpers for the gateway and HTTP API."""

from __future__ import annotations

import base64
from typing import Any

from machina.core.models import ContextChunk, Session
from machina.core.transcript import TranscriptProjector


def json_safe_payload(value: Any) -> Any:
    """Preserve MessagePack binary values in JSON-RPC responses."""
    if isinstance(value, bytes):
        return {"$base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): json_safe_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_payload(item) for item in value]
    return value


def session_to_dict(session: Session) -> dict[str, Any]:
    return {
        "id": str(session.id),
        "title": session.title or "",
        "model": session.model or "",
        "provider": session.provider or "",
        "status": session.status,
        "lastActivity": session.last_activity.isoformat() if session.last_activity else None,
        # Additive orchestration link: delegated child sessions carry the
        # session that spawned them; root sessions carry null. The UI uses it
        # to build the agent tab strip (parent family grouping).
        "parentSessionId": (
            None
            if getattr(session, "is_fork", False)
            else (
                str(session.parent_session_id)
                if getattr(session, "parent_session_id", None)
                else None
            )
        ),
        "isFork": bool(getattr(session, "is_fork", False)),
        **(
            {"workspaceRoot": session.state["desktop_workspace_root"]}
            if isinstance(getattr(session, "state", None), dict)
            and isinstance(session.state.get("desktop_workspace_root"), str)
            else {}
        ),
    }


def history_from_chunks(chunks: list[ContextChunk]) -> list[dict[str, Any]]:
    """Turn stored context chunks (newest first) into a chronological transcript."""
    projector = TranscriptProjector()
    history: list[dict[str, Any]] = []
    for chunk in reversed(chunks):
        turn = projector.chunk(chunk)
        if turn is None:
            continue
        entry: dict[str, Any] = {
            "role": turn.role,
            "content": turn.render_content(),
        }
        if turn.tool is not None:
            entry["tool"] = turn.tool.name
        history.append(entry)
    return history


def chunk_preview(chunk: ContextChunk) -> str:
    payload = chunk.payload
    for key in ("content", "text", "result_preview"):
        if payload.get(key):
            return str(payload[key])[:200]
    if payload.get("tool"):
        return f"{payload['tool']}({payload.get('args', {})})"[:200]
    return str(payload)[:200]
