from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from machina.core.steering import SteeringInbox


def test_steering_is_ordered_session_bound_and_consumed_once():
    session = uuid4()
    inbox = SteeringInbox(session)
    a = inbox.add("first")
    inbox.add("second")
    assert inbox.drain(uuid4()) == []
    assert [item.text for item in inbox.drain(session)] == ["first", "second"]
    assert inbox.drain(session) == []
    assert a.id


def test_pending_steering_can_be_removed_and_is_bounded():
    inbox = SteeringInbox(uuid4())
    item = inbox.add("change this")
    assert inbox.remove(item.id)
    assert not inbox.remove(item.id)
    for _ in range(32):
        inbox.add("x")
    with pytest.raises(ValueError):
        inbox.add("overflow")


@pytest.mark.asyncio
async def test_gateway_queue_edit_remove_and_idle_fallback():
    import asyncio

    from machina.core.models import Session
    from machina.gateway.server import Gateway

    session = Session(id=uuid4(), agent_id="harness")
    inbox = SteeringInbox(session.id)
    gateway = SimpleNamespace(
        require_db=lambda: None,
        get_session=AsyncMock(return_value=session),
        _turns={str(session.id): asyncio.current_task()},
        _steering={str(session.id): inbox},
    )
    result = await Gateway._prompt_steer(gateway, {"text": "first"})
    assert result["queued"]
    snapshot = await Gateway._prompt_steering(gateway, {"take": True})
    assert snapshot["items"] == inbox.snapshot()
    assert inbox.items, "reading a follow-up cannot consume it before turn acceptance"
    await Gateway._prompt_steering(gateway, {"replace": result["id"], "text": "edited"})
    assert inbox.items[0].text == "edited"
    await Gateway._prompt_steering(gateway, {"remove": result["id"]})
    assert not inbox.items
    gateway._turns.clear()
    assert await Gateway._prompt_steer(gateway, {"text": "late"}) == {"queued": False}


@pytest.mark.asyncio
async def test_queued_followup_is_consumed_when_the_new_turn_is_accepted(monkeypatch):
    from machina.core.models import Session
    from machina.gateway.server import Gateway

    session = Session(id=uuid4(), agent_id="harness")
    inbox = SteeringInbox(session.id)
    item = inbox.add("followup")
    start = AsyncMock()
    gateway = SimpleNamespace(
        require_db=lambda: None,
        get_session=AsyncMock(return_value=session),
        _turns={},
        _turn_tokens={},
        _steering={str(session.id): inbox},
        _run_turn_with_timeout=start,
    )
    monkeypatch.setattr("machina.core.turns.try_begin_turn", AsyncMock(return_value="owned"))
    await Gateway._prompt_submit(gateway, {"text": item.text, "steeringId": item.id})
    await gateway._turns[str(session.id)]
    assert not inbox.items
    assert start.await_args.args[2] == "followup"


@pytest.mark.asyncio
async def test_failed_persistence_retains_undelivered_steering(monkeypatch):
    from machina.core.steering import apply_steering, steering_inbox

    inbox = SteeringInbox(uuid4())
    inbox.add("keep me")
    monkeypatch.setattr(
        "machina.core.context.context_manager.add_chunk",
        AsyncMock(side_effect=RuntimeError("offline")),
    )
    messages = []
    token = steering_inbox.set(inbox)
    try:
        with pytest.raises(RuntimeError):
            await apply_steering(inbox.session_id, "harness", messages)
    finally:
        steering_inbox.reset(token)
    assert not messages
    assert inbox.items[0].text == "keep me"


@pytest.mark.asyncio
async def test_steering_before_dispatch_skips_announced_tool(monkeypatch):
    from machina.core import agent as module
    from machina.core.models import LLMResponse
    from machina.core.steering import steering_inbox

    session = uuid4()
    inbox = SteeringInbox(session)
    execute = AsyncMock()
    monkeypatch.setattr(module.registry, "execute", execute)
    agent = SimpleNamespace(
        agent_id="harness",
        allowed_tools=["terminal"],
        _ensure_artifact_view=AsyncMock(return_value=""),
        _record_tool_action=AsyncMock(),
        _append_tool_messages=lambda *args: None,
        _finalize_completed_tools=AsyncMock(),
    )
    response = LLMResponse(
        model="test",
        content="",
        tool_calls=[
            {"id": "tool", "function": {"name": "terminal", "arguments": '{"command":"echo test"}'}}
        ],
    )
    token = steering_inbox.set(inbox)
    try:
        stream = module.ReActAgent._execute_tool_calls_stream(agent, response, [], [], session, [])
        assert (await anext(stream)).type == "tool_call"
        inbox.add("change plan")
        events = [event async for event in stream]
    finally:
        steering_inbox.reset(token)
    execute.assert_not_awaited()
    assert any(
        "NOT executed" in event.tool_result for event in events if event.type == "tool_result"
    )
    assert len([event for event in events if event.type == "tool_result"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_steering_arriving_during_model_call_replans_before_tools(monkeypatch, stream):
    from machina.core import agent as module
    from machina.core.models import AgentResponse, LLMResponse, Session, StreamEvent
    from machina.core.steering import steering_inbox

    session = Session(id=uuid4(), agent_id="harness")
    inbox = SteeringInbox(session.id)
    messages = [{"role": "user", "content": "work"}]
    persist = AsyncMock()
    monkeypatch.setattr(module.context_manager, "add_chunk", persist)

    async def gate(msgs, **kwargs):
        return SimpleNamespace(messages=msgs, failure=None)

    monkeypatch.setattr(module, "evaluate_request", gate)
    calls = []

    async def model(**kwargs):
        calls.append(list(kwargs["messages"]))
        if len(calls) == 1:
            inbox.add("Do not run that command")
            return LLMResponse(
                model="test",
                content="",
                tool_calls=[{"id": "danger", "function": {"name": "terminal", "arguments": "{}"}}],
            )
        return LLMResponse(model="test", content="Replanned")

    execute = AsyncMock()

    async def streaming_model(**kwargs):
        yield StreamEvent(type="done", response=await model(**kwargs))

    agent = SimpleNamespace(
        agent_id="harness",
        provider=object(),
        _prepare_context=AsyncMock(return_value=(session, messages)),
        _flush_pending_chunks=AsyncMock(),
        _condense_history=lambda _: None,
        _tool_defs=lambda: [],
        _call_llm_with_retry=model,
        _execute_tool_calls=execute,
        _stream_llm_with_retry=streaming_model,
        _finish_turn_completion=AsyncMock(return_value=AgentResponse(content="Replanned")),
    )
    token = steering_inbox.set(inbox)
    try:
        if stream:
            events = [
                event
                async for event in module.ReActAgent.run_stream.__wrapped__(
                    agent, session.id, "work", False
                )
            ]
            result = events[-1].response
        else:
            result = await module.ReActAgent.run.__wrapped__(agent, session.id, "work", False)
    finally:
        steering_inbox.reset(token)
    assert result.content == "Replanned"
    assert calls[1][-1]["content"] == "Do not run that command"
    execute.assert_not_awaited()
    persist.assert_awaited_once()
