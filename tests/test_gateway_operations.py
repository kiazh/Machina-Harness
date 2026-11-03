"""Owned desktop operations keep the serial RPC channel responsive."""

import asyncio
import json
import uuid

import pytest
import pytest_asyncio

from machina.core.models import Session
from machina.core.runtime import RuntimeServices
from machina.gateway.errors import INVALID_PARAMS, NOT_FOUND, TURN_IN_PROGRESS, UNAUTHORIZED
from machina.gateway.server import Gateway


@pytest_asyncio.fixture
async def gateway(monkeypatch):
    from machina.core.session import session_manager

    sessions = [Session(id=uuid.uuid4(), agent_id="test") for _ in range(2)]

    async def get_session(session_id):
        return next((session for session in sessions if session.id == session_id), None)

    monkeypatch.setattr(session_manager, "get", get_session)
    frames = []
    gw = Gateway(frames.append, owns_db=False, runtime=RuntimeServices(), inherit_env_token=False)
    gw._db_ready = True
    gw.test_sessions = sessions
    gw.test_frames = frames
    try:
        yield gw
    finally:
        await gw.close(timeout=0.2)


async def rpc(gateway, method, params):
    await gateway.handle_message({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return gateway.test_frames.pop()


def start_params(gateway, **updates):
    return {
        "method": "agents.run",
        "params": {
            "sessionId": str(gateway.test_sessions[0].id),
            "steps": [{"agent": "research", "task": "Inspect the fixture"}],
        },
        **updates,
    }


async def start(gateway, **updates):
    response = await rpc(gateway, "operations.start", start_params(gateway, **updates))
    assert "result" in response, response
    return response["result"]


async def settled(gateway, operation):
    async with asyncio.timeout(5):
        while True:
            result = (
                await rpc(
                    gateway,
                    "operations.get",
                    {"id": operation["id"], "sessionId": operation.get("sessionId")},
                )
            )["result"]
            if result["status"] not in ("running", "cancelling"):
                return result
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_start_returns_before_runner_finishes_and_serial_dispatch_remains_responsive(gateway):
    from machina.gateway.__main__ import _dispatch_requests

    release = asyncio.Event()
    entered = asyncio.Event()

    async def run(params):
        assert params == start_params(gateway)["params"]
        entered.set()
        await release.wait()
        return {"results": [{"response": "finished"}]}

    gateway.add_method("agents.run", run)
    gateway.add_method("test.ping", lambda params: asyncio.sleep(0, result={"responsive": True}))
    queue = asyncio.Queue()
    for request_id, method, params in [
        (1, "operations.start", start_params(gateway)),
        (2, "test.ping", {}),
    ]:
        queue.put_nowait(
            json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        )
    queue.put_nowait(None)
    try:
        await asyncio.wait_for(_dispatch_requests(gateway, queue), 1)
        responses = {frame["id"]: frame for frame in gateway.test_frames}
        assert "result" in responses[1], responses[1]
        operation = responses[1]["result"]
        assert operation["status"] == "running"
        assert operation["method"] == "agents.run"
        assert operation["cancellable"] is True
        assert responses[2]["result"] == {"responsive": True}
        await asyncio.wait_for(entered.wait(), 1)
        pending = (await rpc(gateway, "operations.get", {"id": operation["id"]}))["result"]
        assert pending["status"] == "running"
        release.set()
        final = await settled(gateway, operation)
        assert final["status"] == "completed"
        assert final["result"] == {"results": [{"response": "finished"}]}
        assert final["startedAt"]
    finally:
        release.set()


@pytest.mark.asyncio
async def test_pending_operation_reserves_parent_against_prompts_and_mutations(gateway):
    from machina.core.turns import begin_mutation

    gateway.add_method("agents.run", lambda params: asyncio.Event().wait())
    operation = await start(gateway)
    session = gateway.test_sessions[0]
    assert gateway.turn_running(session.id)
    assert await begin_mutation(session.id) is None
    response = await rpc(
        gateway, "prompt.submit", {"sessionId": str(session.id), "text": "conflicting turn"}
    )
    assert response["error"]["code"] == TURN_IN_PROGRESS
    await rpc(gateway, "operations.cancel", {"id": operation["id"]})
    assert (await settled(gateway, operation))["status"] == "cancelled"
    assert not gateway.turn_running(session.id)


@pytest.mark.asyncio
async def test_existing_turn_and_any_second_operation_are_rejected(gateway):
    from machina.core.turns import end_turn, try_begin_turn

    gateway.add_method("agents.run", lambda params: asyncio.Event().wait())
    session = gateway.test_sessions[0]
    token = await try_begin_turn(session.id)
    try:
        response = await rpc(gateway, "operations.start", start_params(gateway))
        assert response["error"]["code"] == TURN_IN_PROGRESS
    finally:
        await end_turn(session.id, token)
    await start(gateway)
    for session in gateway.test_sessions:
        params = start_params(gateway)
        params["params"]["sessionId"] = str(session.id)
        response = await rpc(gateway, "operations.start", params)
        assert response["error"]["code"] == TURN_IN_PROGRESS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"method": "session.delete", "params": {}},
        {"method": "agents.run", "params": []},
        {"method": "agents.run", "params": {"steps": [{"agent": "a", "task": "t"}]}},
        {"method": "agents.run", "params": {"sessionId": "bad", "steps": []}},
        {
            "method": "interop.run",
            "params": {"platform": "codex", "prompt": "p", "timeout": float("inf")},
        },
        {"method": "interop.run", "params": {"platform": "not-a-platform", "prompt": "p"}},
    ],
)
async def test_invalid_start_is_rejected_before_background_work(gateway, params):
    response = await rpc(gateway, "operations.start", params)
    assert response["error"]["code"] == INVALID_PARAMS


@pytest.mark.asyncio
async def test_lookup_and_cancel_reject_foreign_session_scope(gateway):
    gateway.add_method("agents.run", lambda params: asyncio.Event().wait())
    operation = await start(gateway)
    for method in ("operations.get", "operations.cancel"):
        response = await rpc(
            gateway, method, {"id": operation["id"], "sessionId": str(gateway.test_sessions[1].id)}
        )
        assert response["error"]["code"] == UNAUTHORIZED
    response = await rpc(gateway, "operations.get", {"id": uuid.uuid4().hex})
    assert response["error"]["code"] == NOT_FOUND


@pytest.mark.asyncio
async def test_failure_diagnostics_are_retained_and_redacted(gateway):
    async def fail(params):
        raise RuntimeError("API request failed: sk-abcdefghijklmnopqrstuvwx")

    gateway.add_method("agents.run", fail)
    final = await settled(gateway, await start(gateway))
    assert final["status"] == "failed"
    assert "API request failed" in final["error"]
    assert "sk-abcdefghijklmnopqrstuvwx" not in final["error"]


@pytest.mark.asyncio
async def test_cancel_remains_pending_until_runner_cleanup_really_finishes(gateway):
    entered = asyncio.Event()
    cleanup_entered = asyncio.Event()
    finish_cleanup = asyncio.Event()

    async def run(params):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_entered.set()
            await finish_cleanup.wait()

    gateway.add_method("agents.run", run)
    operation = await start(gateway)
    await entered.wait()
    try:
        snapshot = (await rpc(gateway, "operations.cancel", {"id": operation["id"]}))["result"]
        assert snapshot["status"] == "cancelling"
        await cleanup_entered.wait()
        assert (await rpc(gateway, "operations.get", {"id": operation["id"]}))["result"][
            "status"
        ] == "cancelling"
        # Repeated stop must not interrupt the owner's cleanup.
        await rpc(gateway, "operations.cancel", {"id": operation["id"]})
        assert gateway.turn_running(gateway.test_sessions[0].id)
        finish_cleanup.set()
        assert (await settled(gateway, operation))["status"] == "cancelled"
    finally:
        finish_cleanup.set()


@pytest.mark.asyncio
async def test_cancel_before_runner_enters_releases_the_reserved_parent(gateway):
    calls = []

    async def run(params):
        calls.append(params)
        return {}

    gateway.add_method("agents.run", run)
    operation = await start(gateway)
    await rpc(gateway, "operations.cancel", {"id": operation["id"]})
    assert (await settled(gateway, operation))["status"] == "cancelled"
    assert calls == []
    assert not gateway.turn_running(gateway.test_sessions[0].id)


@pytest.mark.asyncio
async def test_cancellation_resistant_runner_reports_its_actual_completion(gateway):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def run(params):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return {"actual": "completed"}

    gateway.add_method("agents.run", run)
    operation = await start(gateway)
    await entered.wait()
    try:
        await rpc(gateway, "operations.cancel", {"id": operation["id"]})
        await cancelled.wait()
        assert (await rpc(gateway, "operations.get", {"id": operation["id"]}))["result"][
            "status"
        ] == "cancelling"
        release.set()
        final = await settled(gateway, operation)
        assert final["status"] == "completed"
        assert final["result"] == {"actual": "completed"}
    finally:
        release.set()


@pytest.mark.asyncio
async def test_interop_thread_stop_waits_for_actual_worker_through_shutdown(gateway):
    import threading

    from machina.gateway.features import interop

    entered = threading.Event()
    release = threading.Event()

    def run_task(platform, prompt, *, timeout, cancel_event):
        from types import SimpleNamespace

        entered.set()
        release.wait(2)
        return SimpleNamespace(
            ok=True, stdout="external result", stderr="", command=[], exit_code=0
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(interop, "run_task", run_task)
    try:
        operation = await start(
            gateway, method="interop.run", params={"platform": "codex", "prompt": "inspect fixture"}
        )
        assert operation["cancellable"] is True
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0)
        assert (await rpc(gateway, "operations.cancel", {"id": operation["id"]}))["result"][
            "status"
        ] == "cancelling"
        await asyncio.wait_for(gateway.close(timeout=0.01), 1)
        assert (await rpc(gateway, "operations.get", {"id": operation["id"]}))["result"][
            "status"
        ] == "cancelling"
        release.set()
        final = await settled(gateway, operation)
        assert final["status"] == "cancelled"
    finally:
        release.set()
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_shutdown_joins_owned_agent_cleanup_before_returning(gateway):
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def run(params):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    gateway.add_method("agents.run", run)
    operation = await start(gateway)
    await entered.wait()
    await gateway.close(timeout=0.2)
    assert cleaned.is_set()
    assert (await settled(gateway, operation))["status"] == "cancelled"
    assert not gateway.turn_running(gateway.test_sessions[0].id)


@pytest.mark.asyncio
async def test_retention_evicts_only_old_settled_results_and_bounds_result_bytes(
    gateway, monkeypatch
):
    from machina.gateway import operations

    monkeypatch.setattr(operations, "MAX_RETAINED_OPERATIONS", 2)
    monkeypatch.setattr(operations, "MAX_RESULT_BYTES", 64)
    gateway.add_method("agents.run", lambda params: asyncio.sleep(0, result={"value": "small"}))
    first = await start(gateway)
    await settled(gateway, first)
    second = await start(gateway)
    await settled(gateway, second)
    gateway.add_method(
        "agents.run", lambda params: asyncio.sleep(0, result={"value": "large" * 100})
    )
    final = await settled(gateway, await start(gateway))
    assert final["status"] == "failed"
    assert final["truncated"] is True
    assert "result" not in final
    assert (await rpc(gateway, "operations.get", {"id": first["id"]}))["error"]["code"] == NOT_FOUND
    assert (await rpc(gateway, "operations.get", {"id": second["id"]}))["result"][
        "status"
    ] == "completed"


@pytest.mark.asyncio
async def test_concurrent_starts_cannot_pass_the_same_free_slot(gateway, monkeypatch):
    from machina.core.session import session_manager

    original_get = session_manager.get
    entered = asyncio.Event()
    release = asyncio.Event()

    async def get_session(session_id):
        entered.set()
        await release.wait()
        return await original_get(session_id)

    monkeypatch.setattr(session_manager, "get", get_session)
    gateway.add_method("agents.run", lambda params: asyncio.Event().wait())
    pending = [
        asyncio.create_task(rpc(gateway, "operations.start", start_params(gateway)))
        for _ in range(2)
    ]
    await entered.wait()
    release.set()
    responses = await asyncio.gather(*pending)
    assert sum("result" in response for response in responses) == 1
    assert [response["error"]["code"] for response in responses if "error" in response] == [
        TURN_IN_PROGRESS
    ]


@pytest.mark.asyncio
async def test_parent_ownership_loss_stops_agent_work_and_reports_failure(gateway, monkeypatch):
    from machina.core import turns
    from machina.gateway import operations

    cleaned = asyncio.Event()

    async def run(params):
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def lose_ownership(session_id, token):
        return False

    gateway.add_method("agents.run", run)
    monkeypatch.setattr(operations, "CLAIM_RENEW_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(turns, "renew_turn", lose_ownership)
    final = await settled(gateway, await start(gateway))
    assert cleaned.is_set()
    assert final["status"] == "failed"
    assert "ownership" in final["error"]


@pytest.mark.asyncio
async def test_shutdown_keeps_cancellation_resistant_owner_reserved_until_it_settles(gateway):
    from machina.core.turns import begin_mutation

    entered = asyncio.Event()
    release = asyncio.Event()

    async def run(params):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        return {"actual": "late completion"}

    gateway.add_method("agents.run", run)
    operation = await start(gateway)
    await entered.wait()
    try:
        await asyncio.wait_for(gateway.close(timeout=0.01), 1)
        assert (await rpc(gateway, "operations.get", {"id": operation["id"]}))["result"][
            "status"
        ] == "cancelling"
        assert await begin_mutation(gateway.test_sessions[0].id) is None
        release.set()
        assert (await settled(gateway, operation))["status"] == "completed"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_shutdown_finishes_agent_cleanup_before_owned_database_closes(gateway, monkeypatch):
    from machina.db.connection import db
    from machina.observability.audit import audit_persistence

    entered = asyncio.Event()
    cleaned = asyncio.Event()
    database_closed = asyncio.Event()

    async def run(params):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def close_db():
        assert cleaned.is_set()
        database_closed.set()

    monkeypatch.setattr(db, "close", close_db)
    monkeypatch.setattr(audit_persistence, "stop", lambda: asyncio.sleep(0))
    gateway.add_method("agents.run", run)
    await start(gateway)
    await entered.wait()
    gateway._owns_db = True
    await gateway.close(timeout=0.2)
    assert database_closed.is_set()


@pytest.mark.asyncio
async def test_shutdown_racing_session_validation_cannot_launch_work_after_close(
    gateway, monkeypatch
):
    from machina.core.session import session_manager

    entered = asyncio.Event()
    release = asyncio.Event()
    original_get = session_manager.get
    runs = []

    async def get_session(session_id):
        entered.set()
        await release.wait()
        return await original_get(session_id)

    async def run(params):
        runs.append(params)
        return {}

    monkeypatch.setattr(session_manager, "get", get_session)
    gateway.add_method("agents.run", run)
    starting = asyncio.create_task(rpc(gateway, "operations.start", start_params(gateway)))
    await entered.wait()
    await gateway.close(timeout=0.01)
    release.set()
    response = await starting
    assert "error" in response
    await asyncio.sleep(0)
    assert runs == []


@pytest.fixture
def harmless_harness(monkeypatch):
    """Exercise the real process runner using Python instead of a model CLI."""
    import sys

    from machina import interop

    monkeypatch.setattr(interop, "_which", lambda spec: sys.executable)
    monkeypatch.setattr(
        interop,
        "_literal_prompt_invocation",
        lambda *args: ([sys.executable, "-c", "from pathlib import Path; print(Path.cwd())"], None),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_interop_runs_in_the_bound_session_workspace_through_real_child_runner(
    gateway, tmp_path, harmless_harness, wrapped
):
    selected = tmp_path / "selected project"
    selected.mkdir()
    gateway.test_sessions[0].state["desktop_workspace_root"] = str(selected)
    params = {
        "platform": "codex",
        "prompt": "print fixture cwd",
        "sessionId": str(gateway.test_sessions[0].id),
    }
    if wrapped:
        snapshot = await start(gateway, method="interop.run", params=params)
        final = await settled(gateway, snapshot)
        assert final["status"] == "completed", final
        result = final["result"]
    else:
        response = await rpc(gateway, "interop.run", params)
        assert "result" in response, response
        result = response["result"]
    assert result["ok"] is True, result
    assert result["stdout"] == str(selected.resolve())


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("pin", [None, "relative/folder", "missing", 17])
async def test_session_interop_refuses_invalid_or_unavailable_workspace_before_launch(
    gateway, tmp_path, monkeypatch, wrapped, pin
):
    from machina.gateway.features import interop

    calls = []

    def run_task(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("an invalid session workspace must never launch a harness")

    if pin == "missing":
        pin = str(tmp_path / "unavailable")
    if pin is not None:
        gateway.test_sessions[0].state["desktop_workspace_root"] = pin
    monkeypatch.setattr(interop, "run_task", run_task)
    params = {
        "platform": "codex",
        "prompt": "inspect fixture",
        "sessionId": str(gateway.test_sessions[0].id),
    }
    if wrapped:
        response = await rpc(
            gateway, "operations.start", {"method": "interop.run", "params": params}
        )
    else:
        response = await rpc(gateway, "interop.run", params)
    assert response.get("error", {}).get("code") == INVALID_PARAMS, response
    assert calls == []


def test_interop_explicit_cwd_is_never_replaced_with_the_default_if_it_disappears(
    tmp_path, harmless_harness
):
    from machina import interop

    result = interop.run_task("codex", "inspect fixture", cwd=tmp_path / "missing")
    assert result.ok is False
    assert result.stdout == ""


def test_sessionless_interop_keeps_its_existing_default_workspace(
    tmp_path, monkeypatch, harmless_harness
):
    from machina import interop

    monkeypatch.setattr(interop, "_repo_root", lambda: tmp_path)
    result = interop.run_task("codex", "inspect fixture")
    assert result.ok is True
    assert result.stdout == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_interop_redaction_failure_never_returns_raw_cli_diagnostics(gateway, monkeypatch):
    from types import SimpleNamespace

    from machina.gateway.features import interop
    from machina.memory import redaction

    raw = "confidential fixture credential"

    def fail_redaction(text):
        raise RuntimeError("redactor unavailable")

    monkeypatch.setattr(redaction, "redact_secrets", fail_redaction)
    monkeypatch.setattr(
        interop,
        "run_task",
        lambda platform, prompt, *, timeout: SimpleNamespace(
            ok=False, command=[raw], stdout=raw, stderr=raw, exit_code=1
        ),
    )
    response = await rpc(gateway, "interop.run", {"platform": "codex", "prompt": "fixture"})
    result = response["result"]
    assert raw not in json.dumps(result)
    assert result["stdout"] == "[redaction unavailable]"
    assert result["stderr"] == "[redaction unavailable]"


@pytest.mark.asyncio
async def test_lost_start_reply_can_be_recovered_by_caller_uuid_without_repeating_work(gateway):
    identity = str(uuid.uuid4())
    calls = []

    async def run(params):
        calls.append(params)
        return {"results": []}

    gateway.add_method("agents.run", run)
    operation = await start(gateway, id=identity)
    assert operation["id"] == identity
    final = await settled(gateway, {"id": identity})
    assert final["status"] == "completed"
    duplicate = await rpc(gateway, "operations.start", start_params(gateway, id=identity))
    assert "error" in duplicate
    assert len(calls) == 1
    assert (await rpc(gateway, "operations.get", {"id": str(uuid.uuid4()), "missingOk": True}))[
        "result"
    ] is None


@pytest.mark.asyncio
async def test_loop_retirement_does_not_release_parent_while_external_worker_is_live(
    gateway, monkeypatch, tmp_path
):
    import threading
    from types import SimpleNamespace

    from machina.core.turns import begin_mutation, end_mutation
    from machina.gateway.features import interop

    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    gateway.test_sessions[0].state["desktop_workspace_root"] = str(tmp_path)

    def run_task(*args, cancel_event=None, **kwargs):
        entered.set()
        release.wait(3)
        stopped.set()
        return SimpleNamespace(ok=True, stdout="finished", stderr="", command=[], exit_code=0)

    monkeypatch.setattr(interop, "run_task", run_task)
    operation = await start(
        gateway,
        method="interop.run",
        params={
            "platform": "codex",
            "prompt": "fixture",
            "sessionId": str(gateway.test_sessions[0].id),
        },
    )
    try:
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0)
        await gateway.operations.close(timeout=0)
        # The process entry point also cancels pending tasks after Gateway.close.
        owner = gateway.operations._items[operation["id"]].task
        owner.cancel()
        done, _ = await asyncio.wait({owner}, timeout=0.2)
        assert not stopped.is_set()
        assert not done
        assert await begin_mutation(gateway.test_sessions[0].id) is None
        release.set()
        final = await settled(gateway, operation)
        assert final["status"] == "cancelled"
        token = await begin_mutation(gateway.test_sessions[0].id)
        assert token is not None
        await end_mutation(gateway.test_sessions[0].id, token)
    finally:
        release.set()


@pytest.mark.asyncio
async def test_external_stop_reaches_real_process_kill_and_waits_for_worker_cleanup(
    gateway, monkeypatch, tmp_path
):
    import sys

    from machina import interop

    ready = tmp_path / "child-started"
    script = f"from pathlib import Path; import time; Path({str(ready)!r}).write_text('ready'); time.sleep(30)"
    monkeypatch.setattr(interop, "_which", lambda spec: sys.executable)
    monkeypatch.setattr(
        interop, "_literal_prompt_invocation", lambda *args: ([sys.executable, "-c", script], None)
    )
    operation = await start(
        gateway, method="interop.run", params={"platform": "codex", "prompt": "fixture"}
    )
    async with asyncio.timeout(5):
        while not ready.exists():
            await asyncio.sleep(0.02)
    response = await rpc(gateway, "operations.cancel", {"id": operation["id"]})
    assert response["result"]["status"] == "cancelling"
    assert (await settled(gateway, operation))["status"] == "cancelled"
