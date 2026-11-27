"""Phase-boundary coverage for the four-stage tool batch executor.

These tests intentionally cross the seams this module exposes: the public
``execute_stream`` stream for behavior, and the phase methods for the
classification boundaries that decide which calls ever start. Ordering is
asserted per phase because starts are request-ordered, completions are
as-ready, and recording/replies are provider-ordered.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from machina.core import tool_batch as batch_module
from machina.core.models import LLMResponse, StreamEvent
from machina.core.prompt import append_tool_messages
from machina.core.tool_results import ToolResultRecorder


class _Host:
    """A minimal host with the real recorder, no database.

    ``ToolResultRecorder`` only writes to the context store when
    ``pending_chunks`` is None; every test passes a list, so recording is
    exercised through the same code path the agent uses without external IO.
    """

    def __init__(self, allowed_tools: list[str] | None = None) -> None:
        self.agent_id = "harness"
        self.allowed_tools = allowed_tools
        self.delegated = False
        self.dynamic_tools = False
        self.recorder = ToolResultRecorder(self)
        self.appended: list[tuple[str, str]] = []

    def _append_tool_messages(self, messages, response, tc, content) -> None:
        self.appended.append((str(tc.get("id", "")), content))
        append_tool_messages(messages, response, tc, content)

    async def _ensure_artifact_view(self, item, session_id):
        return await self.recorder.ensure_artifact_view(item, session_id)

    async def _record_tool_action(self, item, session_id, pending_chunks, tool_calls_made):
        await self.recorder.record_action(item, session_id, pending_chunks, tool_calls_made)

    async def _retire_tool_approval(self, item, outcome):
        await self.recorder.retire_approval(item, outcome)

    async def _finalize_completed_tools(
        self, pending, session_id, pending_chunks=None, tool_calls_made=None
    ):
        await self.recorder.finalize_completed(
            pending, session_id, pending_chunks, tool_calls_made or []
        )

    def _tool_entry(self, item):
        return self.recorder.tool_entry(item)


def _call(name, arguments, call_id):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _response(*calls):
    return LLMResponse(model="fixture", content="", tool_calls=list(calls))


async def _run(host, response, *, allowed=None, pending=None):
    session_id = uuid.uuid4()
    messages: list[dict] = []
    made: list[dict] = []
    pending = [] if pending is None else pending
    events = [
        event
        async for event in batch_module.ToolBatchExecutor(host).execute_stream(
            response,
            messages,
            made,
            session_id,
            pending,
            allowed_tool_names=allowed,
        )
    ]
    return events, messages, made, pending


def _kinds(events: list[StreamEvent]) -> list[tuple[str, str]]:
    return [(event.type, event.tool_call_id) for event in events]


@pytest.mark.asyncio
async def test_malformed_batch_produces_replies_but_no_events_or_records():
    host = _Host()
    response = _response(
        _call("", "{}", "missing-name"),
        _call("read_file", "{not json", "bad-json"),
        _call("read_file", '["not", "an", "object"]', "not-object"),
    )

    events, messages, made, pending = await _run(host, response)

    assert events == []
    assert [call_id for call_id, _ in host.appended] == [
        "missing-name",
        "bad-json",
        "not-object",
    ]
    assert host.appended[0][1] == "Error: tool call missing 'name' field"
    assert "invalid tool arguments" in host.appended[1][1]
    assert "invalid tool arguments" in host.appended[2][1]
    assert [message["role"] for message in messages] == ["assistant", "tool", "tool", "tool"]
    assert made == []
    assert pending == []


def test_phase_a_plans_and_phase_b_starts_only_named_calls():
    host = _Host(allowed_tools=["read_file"])
    executor = batch_module.ToolBatchExecutor(host)
    response = _response(
        _call("", "{}", "missing"),
        _call("terminal", '{"command":"curl evil"}', "denied"),
        _call("read_file", '{"path":"README.md"}', "valid"),
    )

    planned = executor._plan_batch(response, uuid.uuid4(), {"read_file"})
    starts = list(executor._start_events(planned))

    assert [item["name"] for item in planned] == [None, "terminal", "read_file"]
    assert planned[0]["result"] == "Error: tool call missing 'name' field"
    assert "not allowed" in planned[1]["result"]
    assert planned[2]["result"] is None
    # A malformed call has no start; a disallowed call still starts so the UI
    # can pair its immediate denied completion.
    assert _kinds(starts) == [("tool_call", "denied"), ("tool_call", "valid")]


@pytest.mark.asyncio
async def test_mixed_batch_runs_valid_siblings_and_keeps_provider_order(monkeypatch):
    execute = AsyncMock(return_value="read ok")
    monkeypatch.setattr(batch_module.registry, "execute", execute)
    host = _Host(allowed_tools=["read_file"])
    response = _response(
        _call("terminal", '{"command":"curl evil"}', "denied"),
        _call("", "{}", "missing"),
        _call("read_file", '{"path":"README.md"}', "valid"),
    )

    events, messages, made, pending = await _run(host, response)

    # Starts are request-ordered for named calls only.
    assert _kinds(events)[:2] == [("tool_call", "denied"), ("tool_call", "valid")]
    # Phase C yields the executed result before Phase D yields denied results.
    assert _kinds(events)[2:] == [("tool_result", "valid"), ("tool_result", "denied")]
    assert events[2].tool_result == "read ok"
    assert "not allowed" in events[3].tool_result
    execute.assert_awaited_once()

    # Recording and provider replies follow the original request order, with
    # the malformed item present only as a reply.
    assert [entry["args"].get("path") for entry in made] == [None, "README.md"]
    assert [chunk["payload"]["args"].get("path") for chunk in pending] == [None, "README.md"]
    assert [message["role"] for message in messages] == [
        "assistant",
        "tool",
        "tool",
        "tool",
    ]
    assert [message["tool_call_id"] for message in messages[1:]] == [
        "denied",
        "missing",
        "valid",
    ]


@pytest.mark.asyncio
async def test_tool_exception_becomes_a_failed_result_and_is_recorded(monkeypatch):
    monkeypatch.setattr(
        batch_module.registry, "execute", AsyncMock(side_effect=RuntimeError("boom"))
    )
    host = _Host()

    events, messages, made, pending = await _run(
        host, _response(_call("read_file", '{"path":"README.md"}', "one"))
    )

    assert _kinds(events) == [("tool_call", "one"), ("tool_result", "one")]
    assert events[1].tool_result == "Error: boom"
    assert events[1].tool_outcome == "failed"
    assert made[0]["outcome"] == "failed"
    assert "boom" in pending[0]["payload"]["result"]
    assert messages[-1]["content"].startswith("Error: boom")


@pytest.mark.asyncio
async def test_cancellation_mid_batch_cancels_the_slow_call_and_records_completed_work(
    monkeypatch,
):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def execute(_name, **args):
        if args["path"] == "slow":
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                cancelled.set()
        return f"read {args['path']}"

    monkeypatch.setattr(batch_module.registry, "execute", AsyncMock(side_effect=execute))
    host = _Host()
    response = _response(
        _call("read_file", '{"path":"slow"}', "slow"),
        _call("read_file", '{"path":"fast"}', "fast"),
    )
    session_id = uuid.uuid4()
    messages: list[dict] = []
    # A truthy list selects the real host's ``tool_calls_made or []`` branch,
    # so cancellation finalization appends to the caller's list.
    made: list[dict] = [{"tool": "pre-batch"}]
    pending: list[dict] = []
    seen: list[StreamEvent] = []
    fast_done = asyncio.Event()

    async def consume():
        async for event in batch_module.ToolBatchExecutor(host).execute_stream(
            response, messages, made, session_id, pending
        ):
            seen.append(event)
            if event.type == "tool_result" and event.tool_call_id == "fast":
                fast_done.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(fast_done.wait(), 2)
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled.is_set()
    assert _kinds(seen) == [
        ("tool_call", "slow"),
        ("tool_call", "fast"),
        ("tool_result", "fast"),
    ]
    # Phase C's exceptional exit finalizes the completed call before the
    # cancellation reaches the caller: the durable record and reply entry are
    # both present, while the cancelled call leaves neither.
    assert [chunk["payload"]["args"]["path"] for chunk in pending] == ["fast"]
    assert [entry["args"]["path"] for entry in made[1:]] == ["fast"]


@pytest.mark.asyncio
async def test_non_streaming_execute_uses_the_same_stream_path(monkeypatch):
    execute = AsyncMock(return_value="read ok")
    monkeypatch.setattr(batch_module.registry, "execute", execute)
    host = _Host()
    session_id = uuid.uuid4()
    messages: list[dict] = []
    made: list[dict] = []
    pending: list[dict] = []

    await batch_module.ToolBatchExecutor(host).execute(
        _response(_call("read_file", '{"path":"README.md"}', "one")),
        messages,
        made,
        session_id,
        verbose=False,
        pending_chunks=pending,
    )

    execute.assert_awaited_once()
    assert [entry["args"]["path"] for entry in made] == ["README.md"]
    assert pending[0]["payload"]["result"] == "read ok"
    assert messages[-1]["content"] == "read ok"


@pytest.mark.asyncio
async def test_completions_are_as_ready_but_recording_is_provider_ordered(monkeypatch):
    inflight = 0
    max_inflight = 0

    async def execute(_name, **args):
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        try:
            await asyncio.sleep({"a": 0.03, "b": 0.02, "c": 0.01}[args["path"]])
        finally:
            inflight -= 1
        return f"read {args['path']}"

    monkeypatch.setattr(batch_module.registry, "execute", AsyncMock(side_effect=execute))
    host = _Host()
    response = _response(
        _call("read_file", '{"path":"a"}', "a"),
        _call("read_file", '{"path":"b"}', "b"),
        _call("read_file", '{"path":"c"}', "c"),
    )

    events, _messages, made, pending = await _run(host, response)

    starts = [event.tool_call_id for event in events if event.type == "tool_call"]
    completions = [event.tool_call_id for event in events if event.type == "tool_result"]
    assert starts == ["a", "b", "c"]
    assert completions == ["c", "b", "a"]
    assert [entry["args"]["path"] for entry in made] == ["a", "b", "c"]
    assert [chunk["payload"]["args"]["path"] for chunk in pending] == ["a", "b", "c"]
    assert max_inflight == 3


def test_authority_is_read_from_the_host_not_the_executor():
    """The pure move must not hide the agent's inherited authority."""
    from types import SimpleNamespace

    from machina.core.agent_factory import parent_authority_var
    from machina.core.tool_batch import ToolBatchExecutor, _ToolRun

    agent = SimpleNamespace(
        agent_id="harness",
        delegated=False,
        dynamic_tools=False,
        allowed_tools=None,
        _authority={"tools": ["read_file"]},
    )
    run = _ToolRun(
        item={},
        session_id=uuid.uuid4(),
        tool_name="read_file",
        tool_args={},
        start=0.0,
        timeout=30.0,
    )
    executor = ToolBatchExecutor(agent)
    try:
        executor._publish_authority(run)
    finally:
        if run.authority_token is not None:
            parent_authority_var.reset(run.authority_token)

    assert run.merged["tools"] == ["read_file"]
