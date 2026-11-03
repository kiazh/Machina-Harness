"""Human desktop workspace selection, pinned per conversation without globals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from machina.gateway.errors import INVALID_PARAMS, TURN_IN_PROGRESS, RpcError
from machina.gateway.features._common import _str

WORKSPACE_STATE_KEY = "desktop_workspace_root"


async def runtime_select_workspace(gw, params: dict[str, Any]) -> dict[str, Any]:
    from machina.core.session import session_manager
    from machina.core.turns import mutation_scope
    from machina.gateway.features.workspace import workspace_open

    if params.get("sessionId"):
        gw.require_db()
    raw = _str(params, "root", max_len=4096)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise RpcError(INVALID_PARAMS, "Choose an absolute workspace folder")
    try:
        root = path.resolve(strict=True)
    except OSError:
        raise RpcError(INVALID_PARAMS, "Workspace folder is unavailable") from None
    if not root.is_dir():
        raise RpcError(INVALID_PARAMS, "Workspace must be a folder")
    if "trust" not in params:
        raise RpcError(INVALID_PARAMS, "An explicit workspace trust choice is required")
    from machina.gateway.features._common import _bool

    trust = _bool(params, "trust")
    async with gw._config_lock:
        if any(not task.done() for task in getattr(gw, "_turns", {}).values()):
            raise RpcError(TURN_IN_PROGRESS, "Stop agent work before selecting a workspace")
        session = await gw.get_session(params) if params.get("sessionId") else None

        async def select():
            old = session.state.get(WORKSPACE_STATE_KEY) if session else None
            if session:
                await session_manager.update_state_fields(
                    session.id, {WORKSPACE_STATE_KEY: str(root)}
                )
            try:
                service = getattr(gw, "workspace_service", None)
                current = service.identity if service else None
                if (
                    current is not None
                    and current.workspace_id == params.get("workspaceId")
                    and Path(current.resolved_root) == root
                    and current.trusted == trust
                ):
                    identity = current.as_dict()
                else:
                    identity = await workspace_open(gw, {"root": str(root), "trust": trust})
            except BaseException:
                if session:
                    await session_manager.update_state_fields(
                        session.id, {WORKSPACE_STATE_KEY: old}
                    )
                raise
            gw._desktop_workspace_root = str(root)
            return identity

        if session:
            async with mutation_scope(session.id) as token:
                if token is None:
                    raise RpcError(
                        TURN_IN_PROGRESS, "Stop this conversation before selecting a workspace"
                    )
                return await select()
        return await select()
