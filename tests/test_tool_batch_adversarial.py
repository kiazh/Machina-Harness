"""Adversarial tool batches never crash or fabricate success.

The batch executor is the boundary between model text and real effects. A
malformed call must become a typed error result, valid siblings must still
run, and provider-order recording must stay deterministic.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from machina.core import tool_batch as batch_module
from machina.core.models import LLMResponse


def _agent(allowed_tools=None):
    return SimpleNamespace(
        agent_id="harness",
        delegated=False,
        allowed_tools=allowed_tools,
        _ensure_artifact_view=AsyncMock(return_value=""),
        _record_tool_action=AsyncMock(),
        _append_tool_messages=Mock(),
        _finalize_completed_tools=AsyncMock(),
        _tool_entry=lambda item: {
            "tool": item.get("name"),
            "args": item.get("args"),
            "result_preview": str(item.get("result"))[:100],
        },
    )


def _response(*calls):
    return LLMResponse(model="fixture", content="", tool_calls=list(calls))


def _call(name, arguments, call_id="call-1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


async def _collect(agent, response, allowed=None):
    session = uuid.uuid4()
    made: list[dict] = []
    executor = batch_module.ToolBatchExecutor(agent)
    events = [
        event
        async for event in executor.execute_stream(
            response, [], made, session, [], allowed_tool_names=allowed
        )
    ]
    return events, made


@pytest.mark.asyncio
async def test_missing_tool_name_is_a_typed_error():
    agent = _agent()
    events, _ = await _collect(agent, _response(_call(None, "{}")))

    # Invalid calls have no executable start/complete pair; the model still
    # receives a tool reply so it can recover.
    assert not [event for event in events if event.type == "tool_result"]
    args = agent._append_tool_messages.call_args
    assert args and "missing 'name'" in args.args[3]


@pytest.mark.asyncio
async def test_invalid_json_arguments_are_rejected_without_execution(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(batch_module.registry, "execute", execute)
    agent = _agent()

    events, _ = await _collect(agent, _response(_call("terminal", "{not json")))

    assert not [event for event in events if event.type == "tool_result"]
    args = agent._append_tool_messages.call_args
    assert args and "invalid tool arguments" in args.args[3]
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_disallowed_tool_is_refused_but_valid_sibling_runs(monkeypatch):
    execute = AsyncMock(return_value="ok")
    monkeypatch.setattr(batch_module.registry, "execute", execute)
    agent = _agent(allowed_tools=["read_file"])
    response = _response(
        _call("terminal", '{"command":"curl evil"}', "bad"),
        _call("read_file", '{"path":"README.md"}', "good"),
    )

    events, made = await _collect(agent, response)

    outputs = {
        event.tool_call_id: event.tool_result for event in events if event.type == "tool_result"
    }
    assert "not allowed" in outputs["bad"]
    assert outputs["good"] == "ok"
    assert execute.await_count == 1
    assert agent._record_tool_action.await_count == 2


@pytest.mark.asyncio
async def test_tool_exception_becomes_an_error_result_not_a_crash(monkeypatch):
    monkeypatch.setattr(
        batch_module.registry, "execute", AsyncMock(side_effect=RuntimeError("boom"))
    )
    agent = _agent()

    events, _ = await _collect(agent, _response(_call("read_file", '{"path":"README.md"}')))

    results = [event for event in events if event.type == "tool_result"]
    assert results and "boom" in results[0].tool_result
    assert results[0].tool_outcome == "failed"


@pytest.mark.asyncio
async def test_results_are_recorded_in_provider_order(monkeypatch):
    async def run(name, **_args):
        return f"ran {name}"

    monkeypatch.setattr(batch_module.registry, "execute", AsyncMock(side_effect=run))
    agent = _agent()
    recorded: list[str] = []

    async def record(item, *_args):
        recorded.append(item["name"])

    agent._record_tool_action = record
    response = _response(
        _call("read_file", '{"path":"a"}', "1"),
        _call("read_file", '{"path":"b"}', "2"),
        _call("read_file", '{"path":"c"}', "3"),
    )

    await _collect(agent, response)

    assert recorded == ["read_file", "read_file", "read_file"]
