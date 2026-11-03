"""Actual MCP/CLI boundary traffic is safe and visible across processes."""

import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
import uuid
from types import SimpleNamespace

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    from machina.core import harness_communications as comms

    path = tmp_path / "communications.sqlite3"
    monkeypatch.setattr(comms, "store_path", lambda: path)
    return comms.CommunicationsStore(path)


def test_redacts_before_persistence_and_truncates_after_redaction(store):
    secret = "sk-proj-" + "x" * 35
    store.append(
        correlation_id=str(uuid.uuid4()),
        direction="incoming",
        phase="request",
        method="machina_chat",
        transport="mcp",
        status="running",
        payload={
            "prompt": f"start {secret} end",
            "Authorization": "private-bearer",
            "nested": {"tokenvars": {"SPECIAL_TOKEN": "private-env"}},
            "rawBody": "private-raw",
            "headers": {"Cookie": "private-cookie"},
        },
    )
    event = store.list()["events"][0]
    assert "start" in event["payload"]
    assert "[REDACTED" in event["payload"]
    disk = store.path.read_bytes()
    for raw in (secret, "private-bearer", "private-env", "private-raw", "private-cookie"):
        assert raw.encode() not in disk
        assert raw not in json.dumps(event)
    store.append(
        correlation_id=str(uuid.uuid4()),
        direction="outgoing",
        phase="response",
        method="machina_chat",
        transport="mcp",
        status="completed",
        payload="A" * 30000 + secret + "Z" * 30000,
    )
    event = store.list()["events"][-1]
    assert event["truncated"] is True
    assert len(event["payload"]) <= store.payload_max_chars
    assert "[truncated" in event["payload"]
    assert event["payload"].endswith("Z" * 50)


def test_retention_and_cursor_are_atomic_and_never_reuse_event_ids(store):
    store.max_events = 3
    for index in range(8):
        store.append(
            correlation_id=str(uuid.uuid4()),
            direction="outgoing",
            phase="request",
            method="interop.run",
            transport="cli",
            status="running",
            payload=str(index),
        )
    first = store.list(limit=2)
    assert [row["payload"] for row in first["events"]] == ["6", "7"]
    assert first["nextCursor"] == "8"
    assert first["hasMore"] is True
    assert store.list(cursor="8")["events"] == []
    store.append(
        correlation_id=str(uuid.uuid4()),
        direction="incoming",
        phase="response",
        method="interop.run",
        transport="cli",
        status="completed",
        payload="new",
    )
    after = store.list(cursor="8")
    assert [row["id"] for row in after["events"]] == ["9"]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM communications").fetchone()[0] == 3


def test_independent_process_writers_share_store_and_bound_retained_bytes(store):
    script = """
import sys, uuid
from pathlib import Path
from machina.core.harness_communications import CommunicationsStore
store = CommunicationsStore(Path(sys.argv[1]), max_events=15, max_bytes=12000)
for i in range(20):
    store.append(correlation_id=str(uuid.uuid4()), direction='incoming', phase='request',
                 method='machina_chat', transport='mcp', status='running', payload='x' * 1000)
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(store.path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(3)
    ]
    for child in children:
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 0, stderr.decode()
        assert stdout == b""
    events = store.list()["events"]
    assert 0 < len(events) <= 15
    assert len({event["id"] for event in events}) == len(events)
    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute("SELECT SUM(size_bytes) FROM communications").fetchone()[0] <= 12000
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.asyncio
async def test_mcp_boundary_records_success_error_cancellation_without_duplicate_calls(store):
    from mcp.types import CallToolRequestParams

    from machina.mcp_server import build_server

    server = build_server()

    async def fixture_reply(prompt: str) -> str:
        return json.dumps({"status": "completed", "reply": prompt, "session_id": session_id})

    async def fixture_fail() -> str:
        raise ValueError("password=private-fixture-password")

    entered = asyncio.Event()

    async def fixture_cancel() -> str:
        entered.set()
        await asyncio.Event().wait()
        return "unreachable"

    for tool in (fixture_reply, fixture_fail, fixture_cancel):
        server.add_tool(tool)
    session_id = str(uuid.uuid4())
    ctx = SimpleNamespace(
        session=SimpleNamespace(
            client_params=SimpleNamespace(client_info=SimpleNamespace(name="fixture-host"))
        )
    )
    await server._handle_call_tool(
        ctx,
        CallToolRequestParams(name="fixture_reply", arguments={"prompt": "inspect actual fixture"}),
    )
    result = await server._handle_call_tool(ctx, CallToolRequestParams(name="fixture_fail"))
    assert result.is_error is True
    task = asyncio.create_task(
        server._handle_call_tool(ctx, CallToolRequestParams(name="fixture_cancel"))
    )
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    events = store.list()["events"]
    assert len(events) == 6
    assert [row["phase"] for row in events] == ["request", "response"] * 3
    assert [row["status"] for row in events[1::2]] == ["completed", "failed", "cancelled"]
    assert [row["direction"] for row in events[:2]] == ["incoming", "outgoing"]
    assert events[0]["correlationId"] == events[1]["correlationId"]
    assert events[1]["sessionId"] == session_id
    assert events[0]["harness"] == "fixture-host"
    assert "private-fixture-password" not in store.path.read_bytes().decode(errors="ignore")


@pytest.mark.asyncio
async def test_gateway_run_records_real_result_and_communications_rpc(store, monkeypatch):
    from machina.gateway.features import METHODS, interop

    monkeypatch.setattr(
        interop,
        "run_task",
        lambda *a, **k: SimpleNamespace(
            ok=True,
            exit_code=0,
            command=["codex", "exec"],
            stdout="actual child response",
            stderr="",
        ),
    )
    await interop.interop_run(SimpleNamespace(), {"platform": "codex", "prompt": "actual prompt"})
    handler = METHODS["interop.communications"]
    result = await handler(SimpleNamespace(), {"limit": 20})
    assert len(result["events"]) == 2
    request, response = result["events"]
    assert request["direction"] == "outgoing"
    assert response["direction"] == "incoming"
    assert request["harness"] == "codex"
    assert "actual prompt" in request["payload"]
    assert "actual child response" in response["payload"]
    assert response["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": True}, {"limit": 201}, {"cursor": "bad"}, {"sessionId": "bad"}],
)
async def test_communications_rejects_invalid_filters(store, params):
    from machina.gateway.errors import RpcError
    from machina.gateway.features.interop import interop_communications

    with pytest.raises(RpcError):
        await interop_communications(SimpleNamespace(), params)


def test_missing_store_read_is_empty_without_creating_files_and_filters_are_precise(store):
    assert store.list()["events"] == []
    assert not store.path.exists()
    first_session, other_session = str(uuid.uuid4()), str(uuid.uuid4())
    for harness, session in (
        ("codex", first_session),
        ("other", first_session),
        ("codex", other_session),
    ):
        store.append(
            correlation_id=str(uuid.uuid4()),
            direction="incoming",
            phase="request",
            method="machina_chat",
            transport="mcp",
            status="running",
            payload="fixture",
            harness=harness,
            session_id=session,
        )
    matched = store.list(session_id=first_session, harness="codex")
    assert [row["id"] for row in matched["events"]] == ["1"]
    assert [row["id"] for row in store.list(cursor="0", limit=1)["events"]] == ["1"]
    assert store.list(cursor="0", limit=1)["hasMore"] is True


def test_redaction_failure_and_storage_failure_never_expose_or_raise(store, monkeypatch):
    from machina.core import harness_communications as comms
    from machina.memory import redaction

    def fail(*args, **kwargs):
        raise OSError("redaction failed with private-diagnostic")

    monkeypatch.setattr(redaction, "redact_diagnostic", fail)
    comms.record_event(
        correlation_id=str(uuid.uuid4()),
        direction="incoming",
        phase="request",
        method="machina_chat",
        transport="mcp",
        status="running",
        payload="private-prompt",
    )
    assert store.list()["events"][0]["payload"] == "[redaction unavailable]"
    assert b"private-prompt" not in store.path.read_bytes()
    monkeypatch.setattr(comms.CommunicationsStore, "append", fail)
    comms.record_event(payload="private-prompt")


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_gateway_failed_child_and_runner_error_are_recorded(store, monkeypatch, raises):
    from machina.gateway.features import interop

    def runner(*args, **kwargs):
        if raises:
            raise RuntimeError("SPECIAL_TOKEN=private-child-error")
        return SimpleNamespace(ok=False, exit_code=2, stdout="", stderr="child failed")

    monkeypatch.setattr(interop, "run_task", runner)
    if raises:
        with pytest.raises(RuntimeError):
            await interop.interop_run(SimpleNamespace(), {"platform": "codex", "prompt": "fixture"})
    else:
        result = await interop.interop_run(
            SimpleNamespace(), {"platform": "codex", "prompt": "fixture"}
        )
        assert result["ok"] is False
    assert store.list()["events"][-1]["status"] == "failed"
    assert b"private-child-error" not in store.path.read_bytes()


@pytest.mark.asyncio
async def test_gateway_cancellation_is_recorded_after_child_worker_exits(store, monkeypatch):
    from machina.gateway.features import interop

    entered, exited = threading.Event(), threading.Event()
    cancel = threading.Event()

    def runner(*args, cancel_event, **kwargs):
        entered.set()
        assert cancel_event.wait(3)
        exited.set()
        return SimpleNamespace(ok=False, stdout="", stderr="cancelled")

    monkeypatch.setattr(interop, "run_task", runner)
    context = interop._operation_cancel.set(cancel)
    try:
        task = asyncio.create_task(
            interop.interop_run(SimpleNamespace(), {"platform": "codex", "prompt": "fixture"})
        )
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        interop._operation_cancel.reset(context)
    assert exited.is_set()
    assert [row["status"] for row in store.list()["events"]] == ["running", "cancelled"]


@pytest.mark.asyncio
async def test_mcp_unknown_client_stays_unknown_and_invalid_input_is_a_failed_response(store):
    from mcp.types import CallToolRequestParams

    from machina.mcp_server import build_server

    server = build_server()
    ctx = SimpleNamespace(session=SimpleNamespace(client_params=None))
    result = await server._handle_call_tool(
        ctx, CallToolRequestParams(name="machina_chat", arguments={"prompt": ""})
    )
    assert "invalid_request" in result.content[0].text
    request, response = store.list()["events"]
    assert request["harness"] is None
    assert response["harness"] is None
    assert response["status"] == "failed"
    assert response["sessionId"] is None


@pytest.mark.asyncio
async def test_async_recording_uses_workers_and_settles_a_cancelled_start(store, monkeypatch):
    from machina.core import harness_communications as comms

    original = comms.record_event
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    worker_threads = []

    def blocked(**kwargs):
        worker_threads.append(threading.get_ident())
        if kwargs["phase"] == "request":
            entered.set()
            assert release.wait(2)
        original(**kwargs)

    monkeypatch.setattr(comms, "record_event", blocked)
    task = asyncio.create_task(
        comms.async_start_exchange(
            method="fixture", transport="mcp", direction="incoming", payload={"task": "read"}
        )
    )
    assert await asyncio.to_thread(entered.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(thread != loop_thread for thread in worker_threads)
    events = store.list()["events"]
    assert [row["status"] for row in events] == ["running", "cancelled"]
    assert events[0]["correlationId"] == events[1]["correlationId"]


def test_reads_are_readonly_and_lock_contention_is_best_effort(store):
    import time

    from machina.core import harness_communications as comms

    comms.record_event(
        correlation_id=str(uuid.uuid4()),
        direction="incoming",
        phase="request",
        method="fixture",
        transport="mcp",
        status="running",
        payload="first",
    )
    connection = store._connect(readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM communications")
    finally:
        connection.close()
    with sqlite3.connect(store.path) as locked:
        locked.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        comms.record_event(
            correlation_id=str(uuid.uuid4()),
            direction="incoming",
            phase="request",
            method="fixture",
            transport="mcp",
            status="running",
            payload="contended",
        )
        assert time.monotonic() - started < 0.3
        locked.rollback()
    assert len(store.list()["events"]) == 1
