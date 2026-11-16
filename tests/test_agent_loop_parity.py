"""The shared ReAct loop keeps run() and run_stream() behavior in lockstep."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from machina.core.models import AgentResponse, LLMResponse, Session, StreamEvent

WORK = "Build the UI page; write the browser test"


def _fake_agent(session, messages, **overrides):
    agent = SimpleNamespace(
        agent_id="harness",
        provider=object(),
        _prepare_context=AsyncMock(return_value=(session, messages)),
        _flush_pending_chunks=AsyncMock(),
        _flush_pending_cancellation_safe=AsyncMock(),
        _schedule_memory_consolidation=lambda *args: None,
        _condense_history=lambda *_args, **_kwargs: None,
        _tool_defs=lambda: [],
        _append_assistant_content=AsyncMock(),
        _maybe_continue_unchanged_repair=None,
        _maybe_continue_unverified_repair=None,
    )
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


@pytest.mark.asyncio
async def test_auto_parallel_synthesis_is_presented_consistently(monkeypatch) -> None:
    """Auto-parallel text used to be stream-only; both transports now share it.

    The non-streaming adapter returns the synthesis and the streaming adapter
    emits it as one text delta, followed by the token usage and terminal
    response. Both run through the same harness-owned plan.
    """
    from machina.core import agent as module
    from machina.core.orchestration import auto as auto_module

    session = Session(id=uuid4(), agent_id="harness")
    response = AgentResponse(content="Parallel synthesis complete.", tokens_used=42)
    planned: list[str] = []

    async def fake_auto_parallel(agent, sess, session_id, user_message, pending_chunks):
        planned.append(user_message)
        return response

    monkeypatch.setattr(auto_module, "maybe_auto_parallel", fake_auto_parallel)
    agent = _fake_agent(session, [{"role": "user", "content": WORK}])

    result = await module.ReActAgent.run.__wrapped__(agent, session.id, WORK, False)
    assert result is response

    events = [
        event
        async for event in module.ReActAgent.run_stream.__wrapped__(agent, session.id, WORK, False)
    ]

    assert [event.type for event in events] == ["text", "token_usage", "done"]
    assert events[0].content == response.content
    assert events[1].tokens_used == response.tokens_used
    assert events[2].response is response
    assert planned == [WORK, WORK]


@pytest.mark.asyncio
async def test_run_and_run_stream_share_terminal_accounting(monkeypatch) -> None:
    """One model turn terminates identically through either adapter."""
    from machina.core import agent as module

    session = Session(id=uuid4(), agent_id="harness")
    messages = [{"role": "user", "content": "hello"}]
    model_response = LLMResponse(
        model="test",
        content="Same answer",
        usage={"total_tokens": 11, "prompt_tokens": 7, "completion_tokens": 4},
    )

    async def gate(msgs, **kwargs):
        return SimpleNamespace(messages=msgs, failure=None)

    async def complete(**_kwargs):
        return model_response

    async def streamed(**_kwargs):
        yield StreamEvent(type="text", content="Same answer")
        yield StreamEvent(type="done", response=model_response)

    async def finish(
        session_id,
        user_message,
        content,
        tool_calls_made,
        total_tokens,
        iterations,
        pending_chunks,
        *,
        outcome="completed",
    ):
        return AgentResponse(
            content=content,
            tool_calls=tool_calls_made,
            tokens_used=total_tokens,
            iterations=iterations,
            outcome=outcome,
        )

    monkeypatch.setattr(module, "evaluate_request", gate)
    agent = _fake_agent(
        session,
        messages,
        _call_llm_with_retry=complete,
        _stream_llm_with_retry=streamed,
        _finish_turn_completion=finish,
    )

    result = await module.ReActAgent.run.__wrapped__(agent, session.id, "hello", False)
    events = [
        event
        async for event in module.ReActAgent.run_stream.__wrapped__(
            agent, session.id, "hello", False
        )
    ]

    assert [event.type for event in events] == ["text", "token_usage", "done"]
    assert result.content == events[-1].response.content == "Same answer"
    assert result.tokens_used == events[-1].response.tokens_used == 11
    assert result.iterations == events[-1].response.iterations == 1
    assert events[0].content == "Same answer"
    assert events[1].tokens_used == 11


@pytest.mark.asyncio
async def test_stream_cancellation_retains_partial_text_once() -> None:
    """Cancelling the stream persists streamed text with an interruption mark."""
    from machina.core import agent as module

    session = Session(id=uuid4(), agent_id="harness")
    streaming = asyncio.Event()
    flushes: list[list[dict]] = []

    async def streamed(**_kwargs):
        yield StreamEvent(type="text", content="partial ")
        streaming.set()
        await asyncio.sleep(60)
        yield StreamEvent(type="done", response=LLMResponse(model="test", content="partial "))

    async def cancellation_safe(pending, _session_id, _note="interrupted"):
        flushes.append([dict(chunk) for chunk in pending])
        pending.clear()

    agent = _fake_agent(
        session,
        [{"role": "user", "content": "hello"}],
        _stream_llm_with_retry=streamed,
        _flush_pending_cancellation_safe=cancellation_safe,
    )

    async def collect():
        return [
            event
            async for event in module.ReActAgent.run_stream.__wrapped__(
                agent, session.id, "hello", False
            )
        ]

    task = asyncio.create_task(collect())
    await streaming.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(flushes) == 1
    assert flushes[0][0]["payload"] == {"content": "partial ", "interrupted": True}
