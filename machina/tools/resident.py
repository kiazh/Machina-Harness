"""Resident-only tools: core memory and grant-scoped approval proxy."""

from __future__ import annotations

import uuid

from machina.core.exceptions import ToolError
from machina.resident import store
from machina.tools.agents import current_session_id
from machina.tools.base import registry


async def _resident_session() -> tuple[dict, str]:
    profile = await store.get_profile()
    session = current_session_id.get()
    if profile is None:
        raise ToolError("no resident profile is configured")
    if session is None or str(profile.get("session_id") or "") != str(session):
        raise ToolError("resident tools are available only to the resident session")
    return profile, str(session)


@registry.register(
    name="resident_remember",
    description=(
        "Store a stable fact or preference in Maki's core memory. "
        "Use when the user shares identity, contact, style, or standing instructions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Short stable label, e.g. 'home city'"},
            "value": {"type": "string", "description": "The fact or preference"},
            "category": {
                "type": "string",
                "enum": ["fact", "preference", "identity", "instruction"],
                "default": "fact",
            },
            "pinned": {"type": "boolean", "default": True},
        },
        "required": ["key", "value"],
    },
)
async def resident_remember(
    key: str,
    value: str,
    category: str = "fact",
    pinned: bool = True,
) -> str:
    await _resident_session()
    await store.upsert_core_memory(
        key,
        value,
        category=category,
        source="resident",
        pinned=pinned,
    )
    return f"Remembered {key}."


@registry.register(
    name="resident_pending_approvals",
    description="List approvals currently waiting for the user.",
    parameters={"type": "object", "properties": {}},
)
async def resident_pending_approvals() -> str:
    _, _session = await _resident_session()
    rows = await store.pending_approvals()
    if not rows:
        return "No pending approvals."
    return "\n".join(
        f"{row['request_id']}  {row['operation']}  {row['target']}  session={row['session_id']}"
        for row in rows
    )


@registry.register(
    name="resident_proxy_resolve",
    description=(
        "Resolve a pending approval on the user's behalf. Only usable inside an "
        "active autonomy grant whose scope covers the operation, session, and target."
    ),
    parameters={
        "type": "object",
        "properties": {
            "request_id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["approved", "denied"]},
        },
        "required": ["request_id", "verdict"],
    },
)
async def resident_proxy_resolve(request_id: str, verdict: str) -> str:
    _, session_id = await _resident_session()
    from machina.permissions import store as permissions

    record = await permissions.get_approval(request_id)
    if record is None:
        raise ToolError("unknown approval")
    if str(record.get("session_id") or "") != session_id:
        # The resident may also proxy a delegated child session when the grant
        # explicitly names it.
        pass
    grants = await store.list_grants(active_only=True)
    matching = [
        grant
        for grant in grants
        if store.grant_allows(
            grant,
            operation=str(record.get("operation") or ""),
            session_id=str(record.get("session_id") or ""),
            target=str(record.get("target") or ""),
        )
    ]
    if not matching:
        raise ToolError("no active grant covers this approval")
    resolved = await store.resolve_approval(
        request_id,
        verdict,
        session_id=str(record.get("session_id") or ""),
        grant_id=str(matching[0]["id"]),
    )
    if resolved is None:
        raise ToolError("approval already consumed or not permitted")
    return f"{verdict} {request_id} under grant {matching[0]['id'][:8]}"


@registry.register(
    name="resident_batch_create",
    description="Create durable batch work. Items are JSON objects; duplicates are skipped by content hash.",
    parameters={
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object"}},
            "key_prefix": {"type": "string", "default": ""},
        },
        "required": ["goal", "items"],
    },
)
async def resident_batch_create(goal: str, items: list[dict], key_prefix: str = "") -> str:
    _, _session = await _resident_session()
    from machina.resident import batches

    batch = await batches.create_batch(goal, items, key_prefix=key_prefix)
    return f"Batch {batch['id']} created with {batch['total']} item(s)."


@registry.register(
    name="resident_batch_claim",
    description="Claim up to N queued items from a batch for processing.",
    parameters={
        "type": "object",
        "properties": {
            "batch_id": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["batch_id"],
    },
)
async def resident_batch_claim(batch_id: str, limit: int = 10) -> str:
    _, _session = await _resident_session()
    from machina.resident import batches

    items = await batches.claim_items(batch_id, limit=max(1, min(50, int(limit))))
    return "\n".join(f"{item['id']}  {item['payload']}" for item in items) or "No queued items."


@registry.register(
    name="resident_batch_finish",
    description="Record the terminal status of a claimed batch item.",
    parameters={
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "status": {"type": "string", "enum": ["done", "failed", "skipped"]},
            "error": {"type": "string", "default": ""},
        },
        "required": ["item_id", "status"],
    },
)
async def resident_batch_finish(item_id: str, status: str, error: str = "") -> str:
    _, _session = await _resident_session()
    from machina.resident import batches

    ok = await batches.finish_item(item_id, status=status, error=error)
    return f"{status} {item_id}" if ok else f"unknown item {item_id}"


@registry.register(
    name="resident_batch_status",
    description="Summarize one batch and its latest items.",
    parameters={
        "type": "object",
        "properties": {"batch_id": {"type": "string"}},
        "required": ["batch_id"],
    },
)
async def resident_batch_status(batch_id: str) -> str:
    _, _session = await _resident_session()
    from machina.resident import batches

    batch = await batches.batch_status(batch_id)
    if batch is None:
        return "unknown batch"
    return f"{batch['id']}  {batch['status']}  {batch['done']}/{batch['total']}  {batch['goal']}"
