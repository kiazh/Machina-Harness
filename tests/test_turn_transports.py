"""Focused transport tests for the shared turn lifecycle seam."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import pytest_asyncio

from machina.api.routes.models import PromptRequest
from machina.api.routes.streaming import _stream_session
from machina.core import agent_factory, turns
from machina.core.models import AgentResponse, Session, StreamEvent
from machina.core.runtime import RuntimeServices
from machina.core.session import session_manager
from machina.core.turn_lifecycle import TurnLifecycle
from machina.gateway.server import Gateway


class StreamingAgent:
    def __init__(self, events, gate: asyncio.Event | None = None):
        self.events = list(events)
        self.gate = gate
        self.prompts: list[str] = []

    async def run_stream(self, session_id, text, *, verbose=False, images=None):
        self.prompts.append(text)
        for event in self.events:
            yield event
        if self.gate is not None:
            await self.gate.wait()


def _pause(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "operation": "tool.exec",
        "target": "ls",
    }


def _done(content: str, **kwargs) -> StreamEvent:
    return StreamEvent(type="done", response=AgentResponse(content=content, **kwargs))


async def _frame_types(chunks: list[str]) -> list[dict]:
    frames = []
    for chunk in chunks:
        if not chunk.startswith("data: {"):
            continue
        frames.append(json.loads(chunk[len("data: ") :].strip()))
    return frames


@pytest_asyncio.fixture
async def gateway(monkeypatch, tmp_path):
    from machina.core.config import config

    target = Session(id=uuid.uuid4(), agent_id="harness")
    monkeypatch.setitem(config._session_overrides, "machina_home", str(tmp_path / "home"))

    async def get_fresh(session_id):
        return target if session_id == target.id else None

    monkeypatch.setattr(session_manager, "get_fresh", get_fresh)
    frames: list[dict] = []
    instance = Gateway(
        frames.append, owns_db=False, runtime=RuntimeServices(), inherit_env_token=False
    )
    instance.db_ready = True
    instance.test_session = target
    instance.test_frames = frames
    try:
        yield instance
    finally:
        await instance.close(timeout=0.2)


@pytest.mark.asyncio
async def test_gateway_interactive_turn_keeps_pause_out_of_stream_but_in_terminal(
    gateway, monkeypatch
):
    pause = _pause("req-gateway")
    agent = StreamingAgent(
        [
            StreamEvent(type="text", content="hello"),
            StreamEvent(
                type="needs_approval",
                content="req-gateway",
                tool_name="tool.exec",
                tool_args={"target": "ls"},
            ),
            _done("hello", needs_approval=[pause]),
        ]
    )
    gateway._agent_factory = lambda _model, _provider: agent
    target = gateway.test_session
    token = await TurnLifecycle.acquire_claim(target.id, turn_id="gw")
    gateway._turn_tokens[str(target.id)] = token
    gateway._turns[str(target.id)] = asyncio.current_task()

    await gateway._run_turn(target.id, "gw", "hello")

    types = [frame.get("params", {}).get("type") for frame in gateway.test_frames]
    assert "needs_approval" not in types
    complete = next(
        frame for frame in gateway.test_frames if frame["params"]["type"] == "message.complete"
    )
    assert complete["params"]["needsApproval"][0]["requestId"] == "req-gateway"
    assert str(target.id) not in gateway._turn_tokens

    next_token = await TurnLifecycle.acquire_claim(target.id, turn_id="next")
    assert next_token
    await turns.end_turn(target.id, next_token)


@pytest.mark.asyncio
async def test_gateway_ownership_loss_cancels_turn_and_releases_claim(gateway, monkeypatch):
    from machina.gateway import server as gateway_server

    gate = asyncio.Event()
    agent = StreamingAgent([StreamEvent(type="text", content="working")], gate=gate)
    gateway._agent_factory = lambda _model, _provider: agent
    target = gateway.test_session
    token = await TurnLifecycle.acquire_claim(target.id, turn_id="lost")
    gateway._turn_tokens[str(target.id)] = token
    gateway._turns[str(target.id)] = asyncio.current_task()

    async def lose(*_args, **_kwargs):
        return False

    monkeypatch.setattr(turns, "renew_turn", lose)
    monkeypatch.setattr(gateway_server, "CLAIM_RENEW_INTERVAL_SECONDS", 0.01)
    try:
        await asyncio.wait_for(gateway._run_turn(target.id, "lost", "hello"), timeout=5)
    finally:
        gate.set()

    types = [frame.get("params", {}).get("type") for frame in gateway.test_frames]
    assert "turn.ownership_lost" in types
    assert "message.stopping" in types
    assert "message.complete" in types
    assert str(target.id) not in gateway._turn_tokens

    next_token = await TurnLifecycle.acquire_claim(target.id, turn_id="next")
    assert next_token
    await turns.end_turn(target.id, next_token)


@pytest.mark.asyncio
async def test_http_headless_stream_emits_one_pause_frame_and_terminal_summary(
    monkeypatch, tmp_path
):
    from machina.core.config import config

    target = Session(id=uuid.uuid4(), agent_id="harness")
    monkeypatch.setitem(config._session_overrides, "machina_home", str(tmp_path / "home"))

    async def get_fresh(session_id):
        return target if session_id == target.id else None

    pause = _pause("req-http")
    agent = StreamingAgent(
        [
            StreamEvent(type="text", content="hello"),
            StreamEvent(
                type="needs_approval",
                content="req-http",
                tool_name="tool.exec",
                tool_args={"target": "ls"},
            ),
            _done("hello", needs_approval=[pause]),
        ]
    )

    async def build_agent(_session):
        return agent

    monkeypatch.setattr(session_manager, "get_fresh", get_fresh)
    monkeypatch.setattr(agent_factory, "build_agent_for_session", build_agent)

    response = await _stream_session(str(target.id), PromptRequest(text="hello"))
    chunks = [chunk async for chunk in response.body_iterator]
    frames = await _frame_types(chunks)

    types = [frame["type"] for frame in frames]
    assert types.count("needs_approval") == 1
    assert types[-1] == "done"
    assert chunks[-1] == "data: [DONE]\n\n"
    done = frames[-1]
    assert done["content"] == "hello"
    assert done["needsApproval"][0]["requestId"] == "req-http"

    next_token = await TurnLifecycle.acquire_claim(target.id, turn_id="next")
    assert next_token
    await turns.end_turn(target.id, next_token)


@pytest.mark.asyncio
async def test_http_ownership_loss_emits_loss_frame_and_releases_claim(monkeypatch, tmp_path):
    import machina.api.app as api_app
    from machina.core.config import config

    target = Session(id=uuid.uuid4(), agent_id="harness")
    monkeypatch.setitem(config._session_overrides, "machina_home", str(tmp_path / "home"))
    gate = asyncio.Event()
    agent = StreamingAgent([StreamEvent(type="text", content="working")], gate=gate)

    async def get_fresh(session_id):
        return target if session_id == target.id else None

    async def build_agent(_session):
        return agent

    async def lose(*_args, **_kwargs):
        return False

    monkeypatch.setattr(session_manager, "get_fresh", get_fresh)
    monkeypatch.setattr(agent_factory, "build_agent_for_session", build_agent)
    monkeypatch.setattr(turns, "renew_turn", lose)
    monkeypatch.setattr(api_app, "CLAIM_RENEW_INTERVAL_SECONDS", 0.01)

    try:
        response = await _stream_session(str(target.id), PromptRequest(text="hello"))
        chunks = await asyncio.wait_for(
            _collect(response.body_iterator),
            timeout=5,
        )
    finally:
        gate.set()
    frames = await _frame_types(chunks)

    types = [frame["type"] for frame in frames]
    assert "turn.ownership_lost" in types
    assert chunks[-1] == "data: [DONE]\n\n"

    next_token = await TurnLifecycle.acquire_claim(target.id, turn_id="next")
    assert next_token
    await turns.end_turn(target.id, next_token)


async def _collect(iterator) -> list[str]:
    return [chunk async for chunk in iterator]
