"""Per-prompt workspace changes and review runs.

The Changes screen is prompt-centric, not commit-centric. Each row comes from
the snapshot opened at the start of one agent turn and finalized at its end,
so the prompt text, the files it touched, and the review attached to it stay
together even when later prompts edit the same files.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from machina.core.execution_context import configured_workspace
from machina.gateway.errors import INVALID_PARAMS, NOT_FOUND, RpcError
from machina.gateway.features._common import _bool, _int, _str
from machina.workspace import reviews, snapshots

if TYPE_CHECKING:
    from machina.gateway.server import Gateway

_MAX_SNAPSHOT_ID = 64
_MAX_PATH = 4096


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _workspace(gw: Gateway, params: dict[str, Any]) -> Path:
    session_id = params.get("sessionId")
    if isinstance(session_id, str) and session_id.strip():
        from machina.gateway.features.desktop_workspace import WORKSPACE_STATE_KEY

        session = await gw.get_session({"sessionId": session_id.strip()})
        pinned = session.state.get(WORKSPACE_STATE_KEY)
        if isinstance(pinned, str) and pinned.strip():
            root = Path(pinned).expanduser()
            if root.is_absolute() and root.is_dir():
                return root.resolve()
    return configured_workspace()


def _snapshot(
    workspace: Path, params: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    snapshot_id = _str(params, "snapshotId", max_len=_MAX_SNAPSHOT_ID)
    found = snapshots.snapshot_by_id(snapshot_id, workspace=workspace)
    if found is None:
        raise RpcError(NOT_FOUND, "snapshot not found for this workspace")
    payload, _manifest = found
    return payload, snapshot_id


async def changes_list(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    workspace = await _workspace(gw, params)
    scope = params.get("scope", "session")
    if scope not in ("session", "workspace"):
        raise RpcError(INVALID_PARAMS, "scope must be 'session' or 'workspace'")
    session_id = params.get("sessionId")
    if scope == "session" and not (isinstance(session_id, str) and session_id.strip()):
        raise RpcError(INVALID_PARAMS, "sessionId is required when scope is 'session'")
    limit = _int(params, "limit", 50, 1, 200)
    rows = snapshots.list_snapshots(
        workspace=workspace,
        session_id=session_id.strip() if scope == "session" else None,
        limit=max(100, limit * 2),
    )
    turns: list[dict[str, Any]] = []
    for payload in rows:
        if payload.get("status") != "complete":
            continue
        summary = snapshots.summarize(payload)
        if not summary["files"]:
            continue
        turns.append(summary)
        if len(turns) >= limit:
            break
    review_map = reviews.list_for(workspace, [str(turn["id"]) for turn in turns])
    for turn in turns:
        review = review_map.get(str(turn["id"]))
        turn["review"] = reviews.compact(review) if review is not None else None
    return {
        "turns": turns,
        "scope": scope,
        "workspace": str(workspace),
        "updatedAt": _now(),
    }


async def changes_diff(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    workspace = await _workspace(gw, params)
    payload, snapshot_id = _snapshot(workspace, params)
    if payload.get("status") != "complete":
        raise RpcError(INVALID_PARAMS, "snapshot is still open; wait for the turn to finish")
    path = _str(params, "path", required=False, max_len=_MAX_PATH)
    result = snapshots.diff_files(payload)
    files = list(result.get("files") or [])
    if path:
        requested = path.replace("\\", "/")
        while requested.startswith("./"):
            requested = requested[2:]
        requested = requested.lstrip("/")
        files = [item for item in files if item.get("path") == requested]
    return {
        "snapshotId": snapshot_id,
        "files": files,
        "truncated": bool(result.get("truncated")),
        "totalChars": int(result.get("totalChars") or 0),
        "updatedAt": _now(),
    }


async def review_start(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    workspace = await _workspace(gw, params)
    payload, _snapshot_id = _snapshot(workspace, params)
    if payload.get("status") != "complete":
        raise RpcError(INVALID_PARAMS, "snapshot is still open; wait for the turn to finish")
    engine = params.get("engine", "rules")
    if engine not in reviews.ENGINES:
        raise RpcError(
            INVALID_PARAMS,
            f"engine must be one of: {', '.join(reviews.ENGINES)}",
        )
    force = _bool(params, "force", default=False)
    review = reviews.run_rules(workspace, payload, force=force)
    return {"review": review}


async def review_cancel(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    workspace = await _workspace(gw, params)
    _payload, snapshot_id = _snapshot(workspace, params)
    review = reviews.cancel(workspace, snapshot_id)
    if review is None:
        raise RpcError(NOT_FOUND, "no review run for this snapshot")
    return {"review": review}
