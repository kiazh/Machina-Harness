"""Maki feature handlers (``resident.*``).

These methods are local-terminal only. Grants, proxy approval resolution, and
core-memory edits must never be reachable through the shared HTTP surface.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from machina.gateway.errors import INVALID_PARAMS, RpcError
from machina.gateway.features._common import (
    _bool,
    _int,
    _require_local_transport,
    _str,
)
from machina.observability.diagnostics import record_failure
from machina.resident import runtime as resident_runtime
from machina.resident import store as resident_store

if TYPE_CHECKING:
    from machina.gateway.server import Gateway


def _str_list(params: dict[str, Any], key: str) -> list[str] | None:
    raw = params.get(key)
    if raw is None:
        return None
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return [item.strip() for item in raw if item.strip()]
    raise RpcError(INVALID_PARAMS, f"{key} must be a string or list of strings")


async def resident_status(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.status")
    gw.require_db()
    payload = await resident_runtime.profile_status()
    daemon = getattr(gw, "_maki_daemon", None)
    payload["daemon"] = daemon.status() if daemon is not None else {
        "running": False,
        "intervalSeconds": 0,
        "lastTick": None,
        "lastError": "",
    }
    return payload


async def resident_start(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.start")
    gw.require_db()
    from machina.resident.daemon import ResidentDaemon

    profile, session = await resident_runtime.ensure_session()
    await resident_store.update_profile(paused=False)
    daemon = getattr(gw, "_maki_daemon", None)
    if daemon is None:
        daemon = ResidentDaemon(gw)
        setattr(gw, "_maki_daemon", daemon)
    interval = params.get("intervalSeconds")
    if interval is not None:
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            raise RpcError(INVALID_PARAMS, "intervalSeconds must be an integer") from None
    daemon.start(interval=interval)
    launched = _launch_notch()
    return {
        "sessionId": str(session.id),
        "profile": profile.get("name"),
        "daemon": daemon.status(),
        "notchLaunched": launched,
    }


def _launch_notch() -> bool:
    """Open the local notch app when /agent start is used from the terminal."""
    if not shutil.which("open"):
        return False
    override = os.environ.get("MACHINA_NOTCH_APP", "").strip()
    candidates = [
        Path(override).expanduser() if override else None,
        Path(__file__).resolve().parents[3] / "notch" / "build" / "MachinaNotch.app",
        Path.home() / "Applications" / "MachinaNotch.app",
        Path("/Applications/MachinaNotch.app"),
    ]
    app = next((path for path in candidates if path and path.exists()), None)
    if app is None:
        return False
    try:
        subprocess.Popen(
            ["open", str(app)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


async def resident_stop(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.stop")
    gw.require_db()
    daemon = getattr(gw, "_maki_daemon", None)
    if daemon is not None:
        daemon.stop()
    await resident_store.update_profile(paused=True)
    return {"stopped": True, "daemon": daemon.status() if daemon is not None else None}


async def resident_goal_create(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.goal.create")
    gw.require_db()
    try:
        goal = await resident_store.create_goal(
            _str(params, "title", max_len=200),
            _str(params, "detail", required=False, max_len=2_000) or "",
        )
    except resident_store.ResidentError as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    return {"goal": goal}


async def resident_goal_list(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.goal.list")
    gw.require_db()
    status = _str(params, "status", required=False, max_len=20) or None
    return {"goals": await resident_store.list_goals(status=status)}


async def resident_goal_set(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.goal.set")
    gw.require_db()
    try:
        changed = await resident_store.set_goal_status(
            _str(params, "goalId", max_len=64),
            _str(params, "status", max_len=20),
        )
    except resident_store.ResidentError as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    return {"changed": changed}


async def resident_prompt(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.prompt")
    gw.require_db()
    profile, session = await resident_runtime.ensure_session()
    if profile.get("paused"):
        raise RpcError(INVALID_PARAMS, "the resident is paused; resume it before prompting")
    text = _str(params, "text", max_len=20_000)
    payload: dict[str, Any] = {"sessionId": str(session.id), "text": text}
    images = params.get("images")
    if images is not None:
        payload["images"] = images
    return await gw._prompt_submit(payload)


async def resident_context(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.context")
    gw.require_db()
    payload = {
        "app": _str(params, "app", required=False, max_len=200),
        "bundle": _str(params, "bundle", required=False, max_len=200),
        "window": _str(params, "window", required=False, max_len=300),
        "selection": _str(params, "selection", required=False, max_len=2_000),
        "url": _str(params, "url", required=False, max_len=2_000),
    }
    if not any(payload.values()):
        raise RpcError(INVALID_PARAMS, "context is empty")
    await resident_store.log_event("screen", "frontmost", payload)
    return {"recorded": True}


async def resident_events(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.events")
    gw.require_db()
    limit = _int(params, "limit", 20, 1, 100)
    return {"events": await resident_store.recent_events(limit=limit)}


async def resident_profile_set(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.profile.set")
    gw.require_db()
    mode = _str(params, "autonomyMode", required=False, max_len=20) or None
    shared = params.get("sharedMemory")
    paused = params.get("paused")
    if shared is not None and not isinstance(shared, bool):
        raise RpcError(INVALID_PARAMS, "sharedMemory must be a boolean")
    if paused is not None and not isinstance(paused, bool):
        raise RpcError(INVALID_PARAMS, "paused must be a boolean")
    try:
        profile = await resident_store.update_profile(
            name=_str(params, "name", required=False, max_len=120) or None,
            persona=_str(params, "persona", required=False, max_len=20_000) or None,
            autonomy_mode=mode,
            shared_memory=shared,
            paused=paused,
        )
    except resident_store.ResidentError as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    if mode:
        _, session = await resident_runtime.ensure_session()
        resident_runtime._apply_mode(profile, session)
    return {"profile": profile}


async def resident_memory_get(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.memory.get")
    gw.require_db()
    rows = await resident_store.core_memory_rows(
        limit=_int(params, "limit", resident_store.CORE_MEMORY_MAX_ROWS, 1, 100)
    )
    return {"memory": rows}


async def resident_memory_set(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.memory.set")
    gw.require_db()
    key = _str(params, "key", max_len=120)
    value = _str(params, "value", max_len=2_000)
    confidence_raw = params.get("confidence", 1.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        raise RpcError(INVALID_PARAMS, "confidence must be a number") from None
    try:
        row = await resident_store.upsert_core_memory(
            key,
            value,
            category=_str(params, "category", required=False, max_len=40) or "fact",
            confidence=confidence,
            source=_str(params, "source", required=False, max_len=80) or "user",
            pinned=_bool(params, "pinned"),
        )
    except resident_store.ResidentError as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    return {"entry": row}


async def resident_memory_forget(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.memory.forget")
    gw.require_db()
    key = _str(params, "key", max_len=120)
    return {"removed": await resident_store.forget_core_memory(key)}


async def resident_grant_create(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.grant.create")
    gw.require_db()
    operations = _str_list(params, "operations") or []
    sessions = _str_list(params, "sessions")
    targets = _str_list(params, "targets")
    max_actions = params.get("maxActions")
    if max_actions is not None:
        try:
            max_actions = int(max_actions)
        except (TypeError, ValueError):
            raise RpcError(INVALID_PARAMS, "maxActions must be an integer") from None
    hours = params.get("hours")
    if hours is not None:
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            raise RpcError(INVALID_PARAMS, "hours must be a number") from None
    try:
        grant = await resident_store.create_grant(
            operations=operations,
            sessions=sessions,
            targets=targets,
            max_actions=max_actions,
            hours=hours,
            reason=_str(params, "reason", required=False, max_len=1_000) or "",
        )
    except resident_store.ResidentError as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    return {"grant": resident_store.grant_view(grant)}


async def resident_grant_list(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.grant.list")
    gw.require_db()
    grants = await resident_store.list_grants(active_only=not _bool(params, "all"))
    return {"grants": [resident_store.grant_view(grant) for grant in grants]}


async def resident_grant_revoke(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.grant.revoke")
    gw.require_db()
    grant_id = _str(params, "grantId", max_len=64)
    return {"revoked": await resident_store.revoke_grant(grant_id)}


async def resident_approval_list(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.approval.list")
    gw.require_db()
    session_id = _str(params, "sessionId", required=False, max_len=64) or None
    return {"approvals": await resident_store.pending_approvals(session_id=session_id)}


async def resident_approval_resolve(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.approval.resolve")
    gw.require_db()
    request_id = _str(params, "requestId", max_len=64)
    verdict = _str(params, "verdict", max_len=16).lower()
    session_id = _str(params, "sessionId", max_len=64)
    grant_id = _str(params, "grantId", required=False, max_len=64) or None
    try:
        resolved = await resident_runtime.resolve_approval(
            request_id,
            verdict,
            session_id=session_id,
            grant_id=grant_id,
        )
    except resident_store.ResidentError as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    if resolved is None:
        raise RpcError(INVALID_PARAMS, "approval already consumed or not permitted")
    _wake_awaiting_turn(gw, request_id, verdict, resolved)
    return {"approval": {"requestId": request_id, "status": verdict}}


def _wake_awaiting_turn(
    gw: Gateway,
    request_id: str,
    verdict: str,
    record: dict[str, Any],
) -> None:
    try:
        entry = gw._pending_approvals.pop(request_id, None)
        fut = entry[0] if isinstance(entry, tuple) else entry
        if fut is not None and not fut.done():
            fut.set_result(verdict)
    except Exception as error:  # optional fallback, never masks the decision
        record_failure("resident.approval.wake", error)
    try:
        gw._write(
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": gw.event_payload(
                    "approval.resolved",
                    str(record.get("session_id") or ""),
                    str(record.get("turn_id") or ""),
                    requestId=request_id,
                    status=verdict,
                ),
            }
        )
    except Exception as error:
        record_failure("resident.approval.notify", error)


async def resident_rsi_list(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.rsi.list")
    gw.require_db()
    from machina.resident import rsi

    status = _str(params, "status", required=False, max_len=20) or None
    if status is not None and status not in ("proposed", "active", "retired"):
        raise RpcError(INVALID_PARAMS, "status must be proposed|active|retired")
    return {"rules": await rsi.list_rules(status=status)}


async def resident_rsi_propose(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.rsi.propose")
    gw.require_db()
    from machina.resident import rsi

    rule = _str(params, "rule", max_len=2_000)
    evidence = params.get("evidence")
    if evidence is not None and not isinstance(evidence, dict):
        raise RpcError(INVALID_PARAMS, "evidence must be an object")
    try:
        row = await rsi.propose_rule(
            rule,
            category=_str(params, "category", required=False, max_len=40) or "style",
            confidence=float(params.get("confidence", 0.5)),
            evidence=evidence,
        )
    except (TypeError, ValueError) as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    return {"rule": row}


async def resident_rsi_review(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.rsi.review")
    gw.require_db()
    from machina.resident import review

    created = await review.review(days=_int(params, "days", 30, 1, 365))
    return {"created": created}


async def resident_rsi_set(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.rsi.set")
    gw.require_db()
    from machina.resident import rsi

    rule_id = _str(params, "ruleId", max_len=64)
    status = _str(params, "status", max_len=20)
    try:
        row = await rsi.set_rule_status(rule_id, status, approved_by="user")
    except ValueError as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    if row is None:
        raise RpcError(INVALID_PARAMS, "unknown rule")
    return {"rule": row}


async def resident_batch_create(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.batch.create")
    gw.require_db()
    from machina.resident import batches

    items = params.get("items")
    if not isinstance(items, list) or not items:
        raise RpcError(INVALID_PARAMS, "items must be a non-empty list")
    try:
        batch = await batches.create_batch(
            _str(params, "goal", max_len=1_000),
            items,
            grant_id=_str(params, "grantId", required=False, max_len=64) or None,
            key_prefix=_str(params, "keyPrefix", required=False, max_len=80) or "",
        )
    except ValueError as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    return {"batch": batch}


async def resident_batch_list(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.batch.list")
    gw.require_db()
    from machina.resident import batches

    return {"batches": await batches.list_batches(limit=_int(params, "limit", 50, 1, 100))}


async def resident_batch_status(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.batch.status")
    gw.require_db()
    from machina.resident import batches

    batch = await batches.batch_status(_str(params, "batchId", max_len=64))
    if batch is None:
        raise RpcError(INVALID_PARAMS, "unknown batch")
    return {"batch": batch}


async def resident_batch_cancel(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.batch.cancel")
    gw.require_db()
    from machina.resident import batches

    return {"cancelled": await batches.cancel_batch(_str(params, "batchId", max_len=64))}


async def resident_kill(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    _require_local_transport(gw, "resident.kill")
    gw.require_db()
    daemon = getattr(gw, "_maki_daemon", None)
    if daemon is not None:
        daemon.stop()
    revoked = 0
    for grant in await resident_store.list_grants(active_only=True):
        if await resident_store.revoke_grant(str(grant.get("id") or "")):
            revoked += 1
    await resident_store.update_profile(paused=True)
    await resident_store.log_action("resident.kill", detail={"revoked_grants": revoked})
    return {"revokedGrants": revoked, "paused": True}
