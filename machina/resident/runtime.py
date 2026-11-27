"""Resident buddy runtime: profile bootstrap and status."""

from __future__ import annotations

import uuid
from typing import Any

from machina.core.session import session_manager
from machina.resident import store

__all__ = [
    "core_memory_block",
    "ensure_session",
    "profile_status",
    "resolve_approval",
]


_SESSION_MODES = {"ask": "ask", "auto-safe": "workspace", "full": "full"}


def _apply_mode(profile: dict[str, Any], session: Any) -> None:
    from machina.core.session_mode import set_session_mode

    mode = str(profile.get("autonomy_mode") or "auto-safe")
    set_session_mode(str(session.id), _SESSION_MODES.get(mode, "ask"))


async def ensure_session() -> tuple[dict[str, Any], Any]:
    """Return the resident profile and its live session, creating both."""
    profile = await store.ensure_profile()
    raw_session = profile.get("session_id")
    if raw_session:
        try:
            session = await session_manager.get(uuid.UUID(str(raw_session)))
        except (ValueError, TypeError):
            session = None
        if session is not None:
            _apply_mode(profile, session)
            return profile, session
    session = await session_manager.create(
        title="Buddy",
        agent_id="harness",
        goal="Resident buddy",
    )
    profile = await store.update_profile(session_id=str(session.id))
    _apply_mode(profile, session)
    return profile, session


async def profile_status() -> dict[str, Any]:
    profile, session = await ensure_session()
    approvals = await store.pending_approvals()
    grants = await store.list_grants()
    memory = await store.core_memory_rows()
    return {
        "profile": {
            "key": profile.get("profile_key"),
            "name": profile.get("name"),
            "persona": profile.get("persona"),
            "autonomyMode": profile.get("autonomy_mode"),
            "sharedMemory": bool(profile.get("shared_memory")),
            "paused": bool(profile.get("paused")),
        },
        "sessionId": str(session.id),
        "pendingApprovals": len(approvals),
        "grants": [store.grant_view(grant) for grant in grants],
        "coreMemoryCount": len(memory),
    }


async def core_memory_block(session_id: str | None = None) -> str:
    return await store.core_memory_block(session_id)


async def resolve_approval(
    request_id: str,
    verdict: str,
    *,
    session_id: str,
    grant_id: str | None = None,
) -> dict[str, Any] | None:
    return await store.resolve_approval(
        request_id,
        verdict,
        session_id=session_id,
        grant_id=grant_id,
    )
