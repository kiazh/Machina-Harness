"""A2A discovery and JSON-RPC envelope are stable and authenticated."""

from __future__ import annotations

from fastapi.testclient import TestClient

import machina.api.auth as auth
from machina.api.app import create_app
from machina.api.routes.a2a import _text_from_parts


def test_agent_card_describes_the_existing_harness() -> None:
    client = TestClient(create_app())
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    assert card["protocolVersion"] == "1.0"
    assert card["url"].endswith("/a2a")
    assert card["capabilities"] == {
        "streaming": False,
        "pushNotifications": False,
        "stateTransitionHistory": True,
    }
    assert {skill["id"] for skill in card["skills"]} == {"code", "shell", "research", "mcp"}


def test_a2a_rpc_requires_auth_and_rejects_unknown_methods(monkeypatch) -> None:
    monkeypatch.setattr(auth, "configured_key", lambda: "test-key")
    client = TestClient(create_app())
    body = {"jsonrpc": "2.0", "id": "1", "method": "unknown", "params": {}}
    assert client.post("/a2a", json=body).status_code == 401
    response = client.post("/a2a", json=body, headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "1",
        "error": {"code": -32601, "message": "Method not found"},
    }


def test_a2a_invalid_task_id_is_a_parameter_error(monkeypatch) -> None:
    monkeypatch.setattr(auth, "configured_key", lambda: "test-key")
    client = TestClient(create_app())
    response = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"id": "not-a-task"}},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602


def test_a2a_text_parts_are_bounded_to_text_kind() -> None:
    assert _text_from_parts([{"kind": "text", "text": "hello"}, {"kind": "file"}]) == "hello"
    assert _text_from_parts([{"kind": "data", "data": {}}]) == ""
