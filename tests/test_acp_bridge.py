"""ACP wire-model checks that do not require a database or an editor."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from acp.schema import TextContentBlock

from machina.acp_bridge import MachinaACPAgent, _mode_state, _prompt_text


def test_prompt_text_keeps_text_and_resource_references() -> None:
    blocks = [
        TextContentBlock(text="fix the parser"),
        SimpleNamespace(uri="file:///repo/src/parser.py"),
        TextContentBlock(text="and run tests"),
    ]
    assert _prompt_text(blocks) == (
        "fix the parser\n[resource: file:///repo/src/parser.py]\nand run tests"
    )


def test_mode_state_advertises_all_durable_modes() -> None:
    state = _mode_state("workspace")
    assert state.current_mode_id == "workspace"
    assert {mode.id for mode in state.available_modes} == {
        "ask",
        "workspace",
        "sandbox",
        "full",
    }


def test_initialize_advertises_loadable_sessions(monkeypatch) -> None:
    monkeypatch.setenv("MACHINA_EXECUTION_MODE", "ask")
    agent = MachinaACPAgent(default_mode="ask")
    response = asyncio.run(agent.initialize(1))
    assert response.protocol_version == 1
    assert response.agent_capabilities.load_session is True
    assert response.agent_capabilities.session_capabilities.list is not None


class _FakeConnection:
    def __init__(self, option_id: str) -> None:
        self.option_id = option_id
        self.requests: list[dict] = []

    async def request_permission(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(outcome=SimpleNamespace(option_id=self.option_id))


def test_approval_maps_allow_once_to_durable_decision(monkeypatch) -> None:
    from machina.permissions import store

    resolved: list[tuple[str, str, str]] = []

    async def fake_resolve(request_id: str, verdict: str, principal: str):
        resolved.append((request_id, verdict, principal))
        return {"request_id": request_id, "status": verdict}

    async def fake_get(request_id: str):
        return {
            "request_id": request_id,
            "session_id": "machina-session",
            "turn_id": "",
            "status": "pending",
        }

    monkeypatch.setattr(store, "resolve_decision", fake_resolve)
    monkeypatch.setattr(store, "get_approval", fake_get)
    agent = MachinaACPAgent(default_mode="ask")
    fake = _FakeConnection("allow_once")
    agent._conn = fake  # type: ignore[assignment]
    agent._acp_for_session["machina-session"] = "acp-session"
    result = asyncio.run(
        agent._approve(
            {
                "request_id": "req-1",
                "session_id": "machina-session",
                "operation": "file.write",
                "targets": ["src/parser.py"],
            }
        )
    )
    assert result == "approved"
    assert resolved == [("req-1", "approved", "acp")]
    assert fake.requests[0]["session_id"] == "acp-session"
    assert {option.option_id for option in fake.requests[0]["options"]} == {
        "allow_once",
        "allow_always",
        "reject_once",
    }


def test_approval_rejects_when_editor_declines(monkeypatch) -> None:
    from machina.permissions import store

    resolved: list[tuple[str, str]] = []

    async def fake_resolve(request_id: str, verdict: str, principal: str):
        resolved.append((request_id, verdict))
        return {"request_id": request_id, "status": verdict}

    async def fake_get(request_id: str):
        return {
            "request_id": request_id,
            "session_id": "machina-session",
            "turn_id": "",
            "status": "pending",
        }

    monkeypatch.setattr(store, "resolve_decision", fake_resolve)
    monkeypatch.setattr(store, "get_approval", fake_get)
    agent = MachinaACPAgent(default_mode="ask")
    agent._conn = _FakeConnection("reject_once")  # type: ignore[assignment]
    agent._acp_for_session["machina-session"] = "acp-session"
    result = asyncio.run(
        agent._approve(
            {
                "request_id": "req-2",
                "session_id": "machina-session",
                "operation": "terminal",
                "targets": ["pytest"],
            }
        )
    )
    assert result == "denied"
    assert resolved == [("req-2", "denied")]


def test_approval_refuses_a_record_from_another_session(monkeypatch) -> None:
    from machina.permissions import store

    async def fake_get(request_id: str):
        return {
            "request_id": request_id,
            "session_id": "other-session",
            "turn_id": "",
            "status": "pending",
        }

    monkeypatch.setattr(store, "get_approval", fake_get)
    agent = MachinaACPAgent(default_mode="ask")
    fake = _FakeConnection("allow_once")
    agent._conn = fake  # type: ignore[assignment]
    agent._acp_for_session["machina-session"] = "acp-session"
    result = asyncio.run(
        agent._approve(
            {
                "request_id": "req-3",
                "session_id": "machina-session",
                "operation": "file.write",
            }
        )
    )
    assert result == "denied"
    assert fake.requests == []


def test_approval_cancels_an_unaddressable_owned_child(monkeypatch) -> None:
    from machina.permissions import store

    resolved: list[tuple[str, str]] = []

    async def fake_get(request_id: str):
        return {
            "request_id": request_id,
            "session_id": "child-session",
            "turn_id": "",
            "status": "pending",
        }

    async def fake_resolve(request_id: str, verdict: str, principal: str):
        resolved.append((request_id, verdict))
        return {"request_id": request_id, "status": verdict}

    monkeypatch.setattr(store, "get_approval", fake_get)
    monkeypatch.setattr(store, "resolve_decision", fake_resolve)
    agent = MachinaACPAgent(default_mode="ask")
    agent._conn = _FakeConnection("allow_once")  # type: ignore[assignment]
    # The child session is intentionally not in the ACP session map.
    result = asyncio.run(
        agent._approve(
            {
                "request_id": "req-child",
                "session_id": "child-session",
                "operation": "file.write",
            }
        )
    )
    assert result == "denied"
    assert resolved == [("req-child", "cancelled")]
