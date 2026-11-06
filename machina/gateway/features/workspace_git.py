"""Read-only Git inspection fenced by the live workspace/client/epoch binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from machina.gateway.errors import INVALID_PARAMS, RpcError
from machina.gateway.features.workspace import _client_id, _epoch, _raw_str, _require
from machina.workspace.git_review import GitReview
from machina.workspace.models import MAX_PATH_CHARS, WorkspaceError

if TYPE_CHECKING:
    from machina.gateway.server import Gateway
    from machina.workspace.service import WorkspaceService


def _capture(gw: Gateway, params: dict[str, Any]) -> tuple[WorkspaceService, Any, GitReview]:
    service = _require(gw, params)
    service._check_epoch(_epoch(params), required=True)
    service._check_client(_client_id(params))
    identity = service.identity
    assert identity is not None
    return service, identity, GitReview(service._require_files())


def _check_live(service: WorkspaceService, identity: Any) -> None:
    if service.identity is not identity or service.epoch != identity.epoch:
        raise WorkspaceError("denied", "stale workspace epoch; the workspace was reopened")


async def workspace_changes(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    try:
        service, identity, review = _capture(gw, params)
        result = await review.changes()
        _check_live(service, identity)
        return {"workspaceId": identity.workspace_id, "workspaceEpoch": identity.epoch, **result}
    except WorkspaceError as error:
        return error.as_refusal()


async def workspace_diff(gw: Gateway, params: dict[str, Any]) -> dict[str, Any]:
    path = _raw_str(params, "path", max_len=MAX_PATH_CHARS)
    target = _raw_str(params, "target", max_len=16)
    if target not in ("working", "staged"):
        raise RpcError(INVALID_PARAMS, "target must be working or staged")
    try:
        service, identity, review = _capture(gw, params)
        result = await review.diff(path, target)
        _check_live(service, identity)
        return {"workspaceId": identity.workspace_id, "workspaceEpoch": identity.epoch, **result}
    except WorkspaceError as error:
        return error.as_refusal()
