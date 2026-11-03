import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from machina.gateway.features.desktop_workspace import WORKSPACE_STATE_KEY, runtime_select_workspace


@pytest.mark.asyncio
async def test_select_pins_only_requested_session_and_future_gateway_sessions(
    tmp_path, monkeypatch
):
    import machina.core.turns as turns
    import machina.gateway.features.workspace as workspace
    from machina.core.session import session_manager

    session = SimpleNamespace(id="session-a", state={"keep": 1})
    gateway = SimpleNamespace(
        _config_lock=asyncio.Lock(),
        _turns={},
        require_db=lambda: None,
        get_session=AsyncMock(return_value=session),
    )
    update = AsyncMock()
    monkeypatch.setattr(session_manager, "update_state_fields", update)
    opened = AsyncMock(return_value={"resolvedRoot": str(tmp_path)})
    monkeypatch.setattr(workspace, "workspace_open", opened)

    @asynccontextmanager
    async def mutation(_):
        yield "lease"

    monkeypatch.setattr(turns, "mutation_scope", mutation)
    result = await runtime_select_workspace(
        gateway, {"root": str(tmp_path), "trust": True, "sessionId": "session-a"}
    )
    assert result["resolvedRoot"] == str(tmp_path)
    update.assert_awaited_once_with("session-a", {WORKSPACE_STATE_KEY: str(tmp_path.resolve())})
    assert gateway._desktop_workspace_root == str(tmp_path.resolve())
    assert session.state == {"keep": 1}


@pytest.mark.asyncio
async def test_selection_failure_restores_session_pin(tmp_path, monkeypatch):
    import machina.core.turns as turns
    import machina.gateway.features.workspace as workspace
    from machina.core.session import session_manager

    session = SimpleNamespace(id="a", state={WORKSPACE_STATE_KEY: "previous"})
    gateway = SimpleNamespace(
        _config_lock=asyncio.Lock(),
        _turns={},
        require_db=lambda: None,
        get_session=AsyncMock(return_value=session),
    )
    update = AsyncMock()
    monkeypatch.setattr(session_manager, "update_state_fields", update)
    monkeypatch.setattr(
        workspace, "workspace_open", AsyncMock(side_effect=ValueError("unavailable"))
    )

    @asynccontextmanager
    async def mutation(_):
        yield "lease"

    monkeypatch.setattr(turns, "mutation_scope", mutation)
    with pytest.raises(ValueError):
        await runtime_select_workspace(
            gateway, {"root": str(tmp_path), "trust": False, "sessionId": "a"}
        )
    assert update.await_args_list[-1].args == ("a", {WORKSPACE_STATE_KEY: "previous"})


@pytest.mark.asyncio
async def test_reusing_the_current_binding_keeps_editor_mirrors_and_servers(tmp_path, monkeypatch):
    import machina.gateway.features.workspace as workspace
    from machina.workspace.models import WorkspaceIdentity

    current = WorkspaceIdentity(
        workspace_id="workspace-a",
        client_id="client-a",
        epoch=3,
        resolved_root=str(tmp_path),
        root_label="project",
        trusted=False,
        capabilities=[],
    )
    gateway = SimpleNamespace(
        _config_lock=asyncio.Lock(), _turns={}, workspace_service=SimpleNamespace(identity=current)
    )
    opened = AsyncMock()
    monkeypatch.setattr(workspace, "workspace_open", opened)
    result = await runtime_select_workspace(
        gateway, {"root": str(tmp_path), "trust": False, "workspaceId": "workspace-a"}
    )
    assert result == current.as_dict()
    opened.assert_not_awaited()


@pytest.mark.asyncio
async def test_execution_and_approval_use_the_conversation_workspace(tmp_path, monkeypatch):
    import uuid

    import machina.db.connection as connection
    from machina.core import execution_context
    from machina.core.session import session_manager
    from machina.permissions.policy import build_request

    sid = uuid.uuid4()
    monkeypatch.setattr(connection, "db", SimpleNamespace(connected=True))
    fetch = AsyncMock(return_value=SimpleNamespace(state={WORKSPACE_STATE_KEY: str(tmp_path)}))
    monkeypatch.setattr(session_manager, "get_fresh", fetch)
    monkeypatch.setattr(
        execution_context, "resolve_effective_mode", AsyncMock(return_value="workspace")
    )
    context = await execution_context.resolve_execution_context(str(sid), "write_file")
    request = build_request(
        operation="file.write",
        targets=["example.py"],
        mode=context.mode,
        workspace=str(context.workspace),
    )
    assert request.workspace_root == str(tmp_path.resolve())
    assert request.cwd == str(tmp_path.resolve())
    assert request.targets == [str((tmp_path / "example.py").resolve())]
    fetch.assert_awaited_once_with(sid)


@pytest.mark.asyncio
async def test_a_missing_pinned_folder_never_falls_back_to_another_workspace(tmp_path, monkeypatch):
    import uuid

    import machina.db.connection as connection
    from machina.core import execution_context
    from machina.core.session import session_manager

    monkeypatch.setattr(connection, "db", SimpleNamespace(connected=True))
    monkeypatch.setattr(
        session_manager,
        "get_fresh",
        AsyncMock(
            return_value=SimpleNamespace(state={WORKSPACE_STATE_KEY: str(tmp_path / "missing")})
        ),
    )
    with pytest.raises(ValueError, match="unavailable"):
        await execution_context.resolve_execution_context(str(uuid.uuid4()), "terminal")


@pytest.mark.asyncio
async def test_delegated_children_inherit_the_durable_parent_workspace(tmp_path, monkeypatch):
    import uuid

    import msgpack

    import machina.core.session as module
    from machina.core.models import Session

    manager = module.SessionManager()
    parent_id = uuid.uuid4()
    fetch_parent = AsyncMock(
        return_value=Session(id=parent_id, state={WORKSPACE_STATE_KEY: str(tmp_path)})
    )
    monkeypatch.setattr(manager, "get_fresh", fetch_parent)
    insert = AsyncMock(return_value={})
    monkeypatch.setattr(module, "db", SimpleNamespace(fetchrow=insert))
    monkeypatch.setattr(manager, "_row_to_session", lambda _: Session(id=uuid.uuid4()))
    monkeypatch.setattr(manager, "_cache_put", AsyncMock())
    state = {"child_preference": "keep"}
    await manager.create(parent_session_id=parent_id, state=state)
    fetch_parent.assert_awaited_once_with(parent_id)
    written = msgpack.unpackb(insert.await_args.args[3], raw=False)
    assert written == {**state, WORKSPACE_STATE_KEY: str(tmp_path)}
    assert state == {"child_preference": "keep"}
