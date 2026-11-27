from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from machina.gateway.errors import RpcError
from machina.gateway.features import resident as feature
from machina.permissions import store as permissions
from machina.resident import runtime as resident_runtime
from machina.resident import store


def _grant(**overrides):
    grant = {
        "id": "grant-1",
        "scope": {"operations": ["send_email", "calendar.*"], "sessions": ["*"], "targets": ["*"]},
        "max_actions": None,
        "actions_used": 0,
        "expires_at": None,
        "revoked_at": None,
    }
    grant.update(overrides)
    return grant


def test_grant_matching_is_scoped_and_deterministic() -> None:
    grant = _grant()

    assert store.grant_allows(grant, operation="send_email", session_id="s1", target="mail")
    assert store.grant_allows(grant, operation="calendar.create", session_id="s1", target="cal")
    assert not store.grant_allows(grant, operation="delete_file", session_id="s1", target="x")
    assert not store.grant_allows(
        _grant(scope={"operations": ["send_email"], "sessions": ["s1"]}),
        operation="send_email",
        session_id="s2",
        target="x",
    )
    assert not store.grant_allows(
        _grant(scope={"operations": ["send_email"], "targets": ["gmail:*"]}),
        operation="send_email",
        session_id="s1",
        target="outlook:inbox",
    )


def test_expired_revoked_and_exhausted_grants_fail_closed() -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    future = datetime.now(UTC) + timedelta(minutes=1)

    assert not store.grant_allows(
        _grant(expires_at=past), operation="send_email", session_id="s1", target="x"
    )
    assert store.grant_allows(
        _grant(expires_at=future), operation="send_email", session_id="s1", target="x"
    )
    assert not store.grant_allows(
        _grant(revoked_at=past), operation="send_email", session_id="s1", target="x"
    )
    assert not store.grant_allows(
        _grant(max_actions=2, actions_used=2), operation="send_email", session_id="s1", target="x"
    )
    assert store.grant_allows(
        _grant(max_actions=2, actions_used=1), operation="send_email", session_id="s1", target="x"
    )


def test_grant_without_operations_denies_everything() -> None:
    assert not store.grant_allows(
        _grant(scope={"sessions": ["*"]}), operation="send_email", session_id="s1", target="x"
    )


def test_core_memory_block_has_a_hard_character_bound() -> None:
    rows = [
        {"key": f"fact-{index}", "value": "x" * 500, "pinned": index == 0}
        for index in range(30)
    ]

    block = store.render_core_memory(rows)

    assert block.startswith("Buddy core memory")
    assert len(block) <= store.CORE_MEMORY_MAX_CHARS
    assert "fact-0 (pinned)" in block


@pytest.mark.asyncio
async def test_create_grant_normalizes_a_jsonb_scope(monkeypatch) -> None:
    seen: dict = {}

    class FakeDb:
        async def fetchrow(self, query, *args):
            seen["args"] = args
            return {
                "id": "11111111-1111-1111-1111-111111111111",
                "scope": json.dumps({"operations": ["send_email"], "sessions": ["*"], "targets": ["*"]}),
                "max_actions": 5,
                "actions_used": 0,
                "expires_at": None,
                "revoked_at": None,
                "created_by": "user",
                "reason": "test",
                "created_at": datetime.now(UTC),
            }

    monkeypatch.setattr(store, "db", FakeDb())
    grant = await store.create_grant(operations=["send_email"], max_actions=5, reason="test")

    assert grant["scope"]["operations"] == ["send_email"]
    assert store.grant_view(grant)["maxActions"] == 5
    assert seen["args"][0] == json.dumps(
        {"operations": ["send_email"], "sessions": ["*"], "targets": ["*"]}
    )


@pytest.mark.asyncio
async def test_proxy_resolution_requires_a_matching_grant(monkeypatch) -> None:
    calls: dict = {}

    async def fake_get_approval(request_id):
        return {
            "request_id": request_id,
            "session_id": "session-1",
            "turn_id": "turn-1",
            "agent_id": "harness",
            "operation": "send_email",
            "target": "gmail:draft",
            "status": "pending",
        }

    async def fake_resolve_decision(request_id, verdict, principal):
        calls["principal"] = principal
        return {"request_id": request_id, "status": verdict}

    async def fake_get_grant(grant_id):
        return _grant(scope={"operations": ["send_email"], "targets": ["gmail:*"]})

    async def fake_consume(grant_id):
        calls["consumed"] = grant_id
        return True

    async def fake_log(*args, **kwargs):
        calls["logged"] = kwargs

    monkeypatch.setattr(permissions, "get_approval", fake_get_approval)
    monkeypatch.setattr(permissions, "resolve_decision", fake_resolve_decision)
    monkeypatch.setattr(store, "get_grant", fake_get_grant)
    monkeypatch.setattr(store, "consume_grant", fake_consume)
    monkeypatch.setattr(store, "log_action", fake_log)

    resolved = await store.resolve_approval(
        "req-1", "approved", session_id="session-1", grant_id="grant-1"
    )

    assert resolved["status"] == "approved"
    assert calls["principal"] == "resident:grant-1"
    assert calls["consumed"] == "grant-1"
    assert calls["logged"]["approval_id"] == "req-1"


@pytest.mark.asyncio
async def test_proxy_resolution_refuses_a_mismatched_target(monkeypatch) -> None:
    async def fake_get_approval(request_id):
        return {
            "request_id": request_id,
            "session_id": "session-1",
            "operation": "send_email",
            "target": "outlook:inbox",
            "status": "pending",
        }

    async def fake_get_grant(grant_id):
        return _grant(scope={"operations": ["send_email"], "targets": ["gmail:*"]})

    monkeypatch.setattr(permissions, "get_approval", fake_get_approval)
    monkeypatch.setattr(store, "get_grant", fake_get_grant)

    with pytest.raises(store.ResidentError):
        await store.resolve_approval(
            "req-1", "approved", session_id="session-1", grant_id="grant-1"
        )


class _Gateway:
    _local_only = True

    def require_db(self) -> None:
        pass


@pytest.mark.asyncio
async def test_resident_status_is_local_only_and_returns_the_profile(monkeypatch) -> None:
    async def fake_status():
        return {
            "profile": {
                "key": "default",
                "name": "Buddy",
                "persona": "",
                "autonomyMode": "auto-safe",
                "sharedMemory": True,
                "paused": False,
            },
            "sessionId": "session-1",
            "pendingApprovals": 0,
            "grants": [],
            "coreMemoryCount": 2,
        }

    monkeypatch.setattr(resident_runtime, "profile_status", fake_status)

    payload = await feature.resident_status(_Gateway(), {})
    assert payload["profile"]["autonomyMode"] == "auto-safe"

    class RemoteGateway(_Gateway):
        _local_only = False

    with pytest.raises(RpcError):
        await feature.resident_status(RemoteGateway(), {})


@pytest.mark.asyncio
async def test_resident_grant_create_returns_the_wire_view(monkeypatch) -> None:
    async def fake_create(**kwargs):
        assert kwargs["operations"] == ["send_email"]
        return _grant(
            scope={"operations": kwargs["operations"], "sessions": ["*"], "targets": ["*"]},
            max_actions=3,
            reason="created",
        )

    monkeypatch.setattr(store, "create_grant", fake_create)

    payload = await feature.resident_grant_create(
        _Gateway(), {"operations": ["send_email"], "hours": 1, "maxActions": 3}
    )
    assert payload["grant"]["maxActions"] == 3
    assert payload["grant"]["scope"]["operations"] == ["send_email"]


@pytest.mark.asyncio
async def test_resident_memory_set_rejects_empty_values(monkeypatch) -> None:
    with pytest.raises(RpcError):
        await feature.resident_memory_set(_Gateway(), {"key": "name", "value": ""})


@pytest.mark.asyncio
async def test_resident_resolve_refuses_a_session_mismatch(monkeypatch) -> None:
    async def fake_get_approval(request_id):
        return {"request_id": request_id, "session_id": "session-a", "operation": "x", "target": "y"}

    monkeypatch.setattr(permissions, "get_approval", fake_get_approval)

    with pytest.raises(RpcError):
        await feature.resident_approval_resolve(
            _Gateway(),
            {"requestId": "req-1", "sessionId": "session-b", "verdict": "approved"},
        )


@pytest.mark.asyncio
async def test_resident_prompt_starts_a_turn(monkeypatch) -> None:
    from types import SimpleNamespace

    async def fake_ensure():
        return {"paused": False}, SimpleNamespace(id="session-1")

    async def fake_prompt(params):
        assert params == {"sessionId": "session-1", "text": "hello"}
        return {"turnId": "turn-1"}

    monkeypatch.setattr(resident_runtime, "ensure_session", fake_ensure)

    class PromptGateway(_Gateway):
        async def _prompt_submit(self, params):
            return await fake_prompt(params)

    payload = await feature.resident_prompt(PromptGateway(), {"text": "hello"})
    assert payload["turnId"] == "turn-1"


@pytest.mark.asyncio
async def test_resident_context_records_an_event(monkeypatch) -> None:
    seen: dict = {}

    async def fake_log(source, kind, payload):
        seen.update({"source": source, "kind": kind, "payload": payload})

    monkeypatch.setattr(store, "log_event", fake_log)
    payload = await feature.resident_context(
        _Gateway(), {"app": "Safari", "window": "Inbox", "selection": "hello"}
    )
    assert payload["recorded"] is True
    assert seen["payload"]["app"] == "Safari"
    assert seen["payload"]["selection"] == "hello"
