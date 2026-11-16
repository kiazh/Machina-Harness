"""Session creation is owned by one shared service, not by each transport."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import machina.core.session_service as session_service_module
import machina.gateway.features.mode as mode
from machina.api.routes.models import CreateSessionRequest
from machina.api.routes.sessions import create_session
from machina.core.models import Session
from machina.core.session_service import (
    INVALID_MODEL,
    UNKNOWN_AGENT,
    UNKNOWN_EMOTION,
    UNKNOWN_PROVIDER,
    SessionCreateError,
    SessionCreateSpec,
    session_service,
)
from machina.gateway.errors import INVALID_PARAMS, RpcError
from machina.gateway.server import Gateway


def _install_service(
    monkeypatch,
) -> tuple[list[dict[str, Any]], SimpleNamespace, AsyncMock, AsyncMock]:
    """Replace the service's side effects with in-memory fakes."""
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> Session:
        calls.append(kwargs)
        return Session(
            id=uuid.uuid4(),
            title=kwargs.get("title"),
            agent_id=kwargs.get("agent_id") or "harness",
            state=kwargs.get("state") or {},
            goal=kwargs.get("goal"),
            model=kwargs.get("model"),
            provider=kwargs.get("provider"),
        )

    manager = SimpleNamespace(create=AsyncMock(side_effect=create))
    monkeypatch.setattr(session_service_module, "session_manager", manager)
    registry = SimpleNamespace(get=AsyncMock(return_value=None))
    monkeypatch.setattr(session_service_module, "agent_registry", registry)
    monkeypatch.setattr(
        session_service_module,
        "config",
        SimpleNamespace(get=lambda key: 4321 if key == "context_budget" else None),
    )
    grant = AsyncMock(return_value=True)
    monkeypatch.setattr(mode, "grant_full_default", grant)
    return calls, registry, manager.create, grant


def _fake_gateway(
    *,
    model: str = "gateway-model",
    provider: str = "openrouter",
    workspace: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        require_db=lambda: None,
        _config_lock=asyncio.Lock(),
        model=model,
        provider=provider,
        _desktop_workspace_root=workspace,
        _opened_session_id=None,
    )


@pytest.mark.asyncio
async def test_create_owns_resolution_state_and_persistence(monkeypatch):
    calls, _, manager_create, _ = _install_service(monkeypatch)

    session = await session_service.create(
        SessionCreateSpec(
            title="Keep this title",
            agent_id="harness",
            model="test-model",
            provider="openrouter",
            goal="Ship it",
            emotion="joy",
            desktop_workspace_root="C:/work",
        )
    )

    assert session.agent_id == "harness"
    manager_create.assert_awaited_once_with(
        title="Keep this title",
        agent_id="harness",
        model="test-model",
        provider="openrouter",
        goal="Ship it",
        context_budget=4321,
        state={"emotion": "joy", "desktop_workspace_root": "C:/work"},
    )
    assert calls == [manager_create.await_args.kwargs]


@pytest.mark.asyncio
async def test_absent_optional_fields_do_not_create_state(monkeypatch):
    calls, _, _, _ = _install_service(monkeypatch)

    await session_service.create(SessionCreateSpec(model="test-model", provider="openrouter"))

    assert calls[0]["state"] is None
    assert calls[0]["title"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", ["missing", "raises"])
async def test_unknown_agent_is_rejected_by_the_shared_service(monkeypatch, lookup):
    _, registry, manager_create, _ = _install_service(monkeypatch)
    if lookup == "raises":
        registry.get.side_effect = RuntimeError("lookup failed")

    with pytest.raises(SessionCreateError) as caught:
        await session_service.create(SessionCreateSpec(agent_id="ghost"))

    assert caught.value.code == UNKNOWN_AGENT
    assert caught.value.message == "unknown agent: ghost"
    assert str(caught.value) == "unknown agent: ghost"
    manager_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_harness_definition_lookup_failure_keeps_the_permitted_fallback(monkeypatch):
    calls, registry, manager_create, _ = _install_service(monkeypatch)
    registry.get.side_effect = RuntimeError("database unavailable")

    await session_service.create(
        SessionCreateSpec(agent_id="harness", model="test-model", provider="openrouter")
    )

    manager_create.assert_awaited_once()
    assert calls[0]["agent_id"] == "harness"


@pytest.mark.asyncio
async def test_known_agent_keeps_its_identity(monkeypatch):
    calls, registry, _, _ = _install_service(monkeypatch)
    registry.get.return_value = SimpleNamespace(provider=None, model=None)

    await session_service.create(
        SessionCreateSpec(agent_id="critic", model="test-model", provider="openrouter")
    )

    assert calls[0]["agent_id"] == "critic"


@pytest.mark.asyncio
async def test_emotion_is_validated_and_stored_by_the_shared_service(monkeypatch):
    calls, _, _, _ = _install_service(monkeypatch)

    await session_service.create(
        SessionCreateSpec(
            model="test-model",
            provider="openrouter",
            emotion="joy",
        )
    )

    assert calls[0]["state"] == {"emotion": "joy"}

    with pytest.raises(SessionCreateError) as caught:
        await session_service.create(
            SessionCreateSpec(model="test-model", provider="openrouter", emotion="rage")
        )

    assert caught.value.code == UNKNOWN_EMOTION
    assert caught.value.message == "unknown emotion"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("spec", "code", "message"),
    [
        ({"provider": "not-a-provider"}, UNKNOWN_PROVIDER, "unknown provider: not-a-provider"),
        ({"provider": ""}, UNKNOWN_PROVIDER, "unknown provider: "),
        ({"model": ""}, INVALID_MODEL, "model must be a non-empty string"),
        ({"model": "   "}, INVALID_MODEL, "model must be a non-empty string"),
    ],
)
async def test_provider_and_model_messages_are_preserved(monkeypatch, spec, code, message):
    _, _, manager_create, _ = _install_service(monkeypatch)

    with pytest.raises(SessionCreateError) as caught:
        await session_service.create(SessionCreateSpec(**spec))

    assert caught.value.code == code
    assert caught.value.message == message
    manager_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_emotion_validation_precedes_the_other_shared_checks(monkeypatch):
    """The HTTP path used to report the emotion error first; keep that order."""
    _, _, manager_create, _ = _install_service(monkeypatch)

    with pytest.raises(SessionCreateError) as caught:
        await session_service.create(
            SessionCreateSpec(emotion="rage", provider="not-a-provider", model=" ")
        )

    assert caught.value.code == UNKNOWN_EMOTION
    manager_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_rpc_and_rest_use_the_same_creation_and_response_shape(monkeypatch):
    calls, _, manager_create, grant = _install_service(monkeypatch)
    gateway = _fake_gateway(workspace="C:/workspace")

    rpc_result = await Gateway._session_create(gateway, {"title": "Shared", "agent": "harness"})
    rest_result = await create_session(
        CreateSessionRequest(
            title="Shared",
            model="test-model",
            provider="openrouter",
            goal="goal",
            emotion="joy",
        )
    )

    assert manager_create.await_count == 2
    rpc_call, rest_call = calls
    assert rpc_call["agent_id"] == rest_call["agent_id"] == "harness"
    # Desktop workspace state is supplied only by the gateway transport; the
    # shared service is what turns it into session state.
    assert rpc_call["state"] == {"desktop_workspace_root": "C:/workspace"}
    # Emotion is supplied only by the HTTP transport; the same service owns
    # its validation and persistence.
    assert rest_call["state"] == {"emotion": "joy"}
    assert rpc_call["context_budget"] == rest_call["context_budget"] == 4321
    assert set(rpc_result) == set(rest_result) == {"session"}
    # Both projections are the same serializer; the gateway's additive
    # workspaceRoot is the documented transport-specific state field.
    assert set(rpc_result["session"]) - set(rest_result["session"]) == {"workspaceRoot"}
    assert rpc_result["session"]["workspaceRoot"] == "C:/workspace"
    assert set(rest_result["session"]) <= set(rpc_result["session"])
    assert gateway._opened_session_id is not None
    assert grant.await_count == 2


@pytest.mark.asyncio
async def test_rpc_title_default_and_truncation_stay_in_the_transport(monkeypatch):
    calls, _, _, _ = _install_service(monkeypatch)

    await Gateway._session_create(_fake_gateway(), {})
    await Gateway._session_create(_fake_gateway(), {"title": "x" * 100})

    assert calls[0]["title"] == "New session"
    assert calls[1]["title"] == "x" * 80


@pytest.mark.asyncio
async def test_rest_keeps_an_absent_title_absent(monkeypatch):
    calls, _, _, _ = _install_service(monkeypatch)

    await create_session(CreateSessionRequest(model="test-model", provider="openrouter"))

    assert calls[0]["title"] is None


@pytest.mark.asyncio
async def test_rpc_unknown_agent_maps_to_invalid_params_without_rewriting(monkeypatch):
    _, _, manager_create, grant = _install_service(monkeypatch)
    gateway = _fake_gateway()

    with pytest.raises(RpcError) as caught:
        await Gateway._session_create(gateway, {"agent": "ghost"})

    assert caught.value.code == INVALID_PARAMS
    assert caught.value.message == "unknown agent: ghost"
    assert isinstance(caught.value.__cause__, SessionCreateError)
    manager_create.assert_not_awaited()
    grant.assert_not_awaited()
    assert gateway._opened_session_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_kwargs", "message"),
    [
        ({"emotion": "rage"}, "unknown emotion"),
        ({"provider": "not-a-provider"}, "unknown provider: not-a-provider"),
        ({"model": "   "}, "model must be a non-empty string"),
    ],
)
async def test_rest_validation_maps_to_http_400_without_rewriting(
    monkeypatch, request_kwargs, message
):
    _, _, manager_create, grant = _install_service(monkeypatch)

    with pytest.raises(HTTPException) as caught:
        await create_session(CreateSessionRequest(**request_kwargs))

    assert caught.value.status_code == 400
    assert caught.value.detail == message
    manager_create.assert_not_awaited()
    grant.assert_not_awaited()
