"""Per-prompt workspace changes and review runs.

The Changes screen is prompt-centric, not commit-centric. Each row comes from
the snapshot opened at the start of one agent turn and finalized at its end,
so the prompt text, the files it touched, and the review attached to it stay
together even when later prompts edit the same files.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from machina.core.execution_context import configured_workspace
from machina.gateway.errors import INVALID_PARAMS, NOT_FOUND, RpcError
from machina.gateway.features._common import _bool, _int, _str
from machina.workspace import reviews, snapshots

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from machina.gateway.server import Gateway

_MAX_SNAPSHOT_ID = 64
_MAX_PATH = 4096
_REVIEW_TASKS: dict[tuple[str, str], asyncio.Task[Any]] = {}


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
    logger.debug(
        "changes.list session=%s scope=%s turns=%d reviews=%d",
        session_id if scope == "session" else "-",
        scope,
        len(turns),
        len(review_map),
    )
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
    requested = ""
    if path:
        requested = path.replace("\\", "/")
        while requested.startswith("./"):
            requested = requested[2:]
        requested = requested.lstrip("/")
    result = snapshots.diff_files(payload, path=requested or None)
    files = list(result.get("files") or [])
    logger.debug(
        "changes.diff snapshot=%s path=%r files=%d truncated=%s",
        snapshot_id,
        requested or None,
        len(files),
        bool(result.get("truncated")),
    )
    return {
        "snapshotId": snapshot_id,
        "files": files,
        "truncated": bool(result.get("truncated")),
        "totalChars": int(result.get("totalChars") or 0),
        "updatedAt": _now(),
    }


async def _model_route(
    gw: Gateway, params: dict[str, Any], payload: dict[str, Any]
) -> tuple[str, str | None]:
    session = None
    session_id = params.get("sessionId")
    if isinstance(session_id, str) and session_id.strip():
        try:
            session = await gw.get_session({"sessionId": session_id.strip()})
        except Exception:
            session = None
    from machina.core.agent_factory import resolve_provider_model

    provider, model = resolve_provider_model(
        session,
        default_provider=getattr(gw, "provider", None),
        default_model=getattr(gw, "model", None),
    )
    if session is None and not getattr(gw, "provider", None):
        provider = str(payload.get("provider") or provider)
        model = str(payload.get("model") or "") or model
    return provider, model


async def _complete_model_review(
    workspace: Path,
    payload: dict[str, Any],
    review: dict[str, Any],
    provider: Any,
    model: str | None,
) -> None:
    try:
        await reviews.run_model(
            workspace,
            payload,
            review,
            complete=provider.complete,
            model=model,
        )
    finally:
        with contextlib.suppress(Exception):
            await provider.close()


def _review_done(key: tuple[str, str], task: asyncio.Task[Any]) -> None:
    if _REVIEW_TASKS.get(key) is task:
        _REVIEW_TASKS.pop(key, None)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("review.task.failed snapshot=%s error=%s", key[1], error)


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
    if engine == "rules":
        review = reviews.run_rules(workspace, payload, force=force)
        if review.get("engine") == "rules":
            task = _REVIEW_TASKS.get(
                (str(workspace), str(payload.get("snapshot_id") or ""))
            )
            if task is not None and not task.done():
                task.cancel()
    else:
        provider_name, model = await _model_route(gw, params, payload)
        try:
            from machina.core.provider import get_provider

            provider = await asyncio.to_thread(
                get_provider, provider=provider_name, model=model
            )
        except Exception as error:
            review = reviews.failed_model(
                workspace,
                payload,
                provider=provider_name,
                model=model or "",
                error=f"{type(error).__name__}: {error}",
            )
        else:
            resolved_model = str(getattr(provider, "model", "") or model or "")
            review, _queued = reviews.begin_model(
                workspace,
                payload,
                provider=provider_name,
                model=resolved_model,
                force=force,
            )
            key = (str(workspace), str(payload.get("snapshot_id") or ""))
            task = _REVIEW_TASKS.get(key)
            if review.get("status") in {"queued", "running"} and (
                task is None or task.done()
            ):
                task = asyncio.create_task(
                    _complete_model_review(workspace, payload, review, provider, model),
                    name=f"review-model-{payload.get('snapshot_id')}",
                )
                _REVIEW_TASKS[key] = task
                task.add_done_callback(lambda done, key=key: _review_done(key, done))
            else:
                with contextlib.suppress(Exception):
                    await provider.close()
    logger.debug(
        "review.start snapshot=%s engine=%s provider=%s model=%s status=%s findings=%d",
        payload.get("snapshot_id"),
        engine,
        review.get("provider"),
        review.get("model"),
        review.get("status"),
        len(review.get("findings") or []),
    )
    return {"review": review}


async def review_get(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    workspace = await _workspace(gw, params)
    _payload, snapshot_id = _snapshot(workspace, params)
    review = reviews.load(workspace, snapshot_id)
    if review is None:
        raise RpcError(NOT_FOUND, "no review run for this snapshot")
    logger.debug(
        "review.get snapshot=%s status=%s findings=%d",
        snapshot_id,
        review.get("status"),
        len(review.get("findings") or []),
    )
    return {"review": review}


async def review_cancel(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    workspace = await _workspace(gw, params)
    _payload, snapshot_id = _snapshot(workspace, params)
    review = reviews.cancel(workspace, snapshot_id)
    if review is None:
        raise RpcError(NOT_FOUND, "no review run for this snapshot")
    task = _REVIEW_TASKS.get((str(workspace), snapshot_id))
    if task is not None and not task.done():
        task.cancel()
    logger.debug("review.cancel snapshot=%s status=%s", snapshot_id, review.get("status"))
    return {"review": review}
