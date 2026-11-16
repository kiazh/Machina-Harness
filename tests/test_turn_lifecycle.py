"""Shared turn lifecycle: approval policy, ownership loss, cleanup/release."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from machina.core import turns
from machina.core.exceptions import StaleOwnershipError
from machina.core.models import AgentResponse, Session, StreamEvent
from machina.core.scheduler import Job, JobRunner
from machina.core.turn_lifecycle import (
    ApprovalPolicy,
    TurnLifecycle,
    TurnStepSupervisor,
)
from machina.permissions import broker


class FakeAgent:
    """Minimal agent double with the two driver surfaces the seam uses."""

    def __init__(self, events=None, final=None, gate: asyncio.Event | None = None):
        self.events = list(events or [])
        self.final = final
        self.gate = gate
        self.prompts: list[str] = []

    async def run_stream(self, session_id, text, *, verbose=False, images=None):
        self.prompts.append(text)
        for event in self.events:
            yield event
        if self.gate is not None:
            await self.gate.wait()

    async def run(self, session_id, text, *, verbose=False):
        self.prompts.append(text)
        return self.final


def session() -> Session:
    return Session(id=uuid.uuid4(), agent_id="harness")


def done_event(content: str, **kwargs) -> StreamEvent:
    return StreamEvent(
        type="done",
        response=AgentResponse(content=content, **kwargs),
    )


@pytest.mark.asyncio
async def test_claim_is_exclusive_and_close_releases_it():
    target = session()
    token = await TurnLifecycle.acquire_claim(target.id, turn_id="first")
    assert token

    lifecycle = TurnLifecycle(session_id=target.id, turn_id="first", token=token)
    assert await TurnLifecycle.acquire_claim(target.id, turn_id="second") is None

    close = await lifecycle.close()
    assert close.release_failed is False

    next_token = await TurnLifecycle.acquire_claim(target.id, turn_id="second")
    assert next_token
    await turns.end_turn(target.id, next_token)


@pytest.mark.asyncio
async def test_headless_policy_relays_pause_marker_and_terminal_summary(monkeypatch):
    target = session()
    token = await TurnLifecycle.acquire_claim(target.id, turn_id="headless")
    pause = {
        "request_id": "req-1",
        "operation": "tool.write",
        "target": "/tmp/a",
    }
    long_text = "working " * 80
    agent = FakeAgent(
        [
            StreamEvent(type="text", content=long_text),
            StreamEvent(
                type="needs_approval",
                content="req-1",
                tool_name="tool.write",
                tool_args={"target": "/tmp/a"},
            ),
            done_event(long_text, needs_approval=[pause]),
        ]
    )
    lifecycle = TurnLifecycle(
        session_id=target.id,
        turn_id="headless",
        token=token,
        approval=ApprovalPolicy(mode="headless", principal="http"),
    )
    lifecycle.start(renew=False)
    await lifecycle.prepare(target, builder=lambda _session: _return(agent))

    events = [event async for event in lifecycle.stream()]

    assert [event.kind for event in events] == ["delta", "needs_approval", "done"]
    terminal = lifecycle.build_terminal()
    assert terminal["needsApproval"] == [
        {
            "requestId": "req-1",
            "operation": "tool.write",
            "target": "/tmp/a",
            "status": "pending",
        }
    ]
    await lifecycle.close()


@pytest.mark.asyncio
async def test_interactive_policy_registers_handler_and_suppresses_pause_markers(monkeypatch):
    target = session()
    token = await TurnLifecycle.acquire_claim(target.id, turn_id="interactive")
    calls: list[tuple[object, dict]] = []

    def fake_set_handler(handler, **kwargs):
        calls.append((handler, kwargs))

    monkeypatch.setattr(broker, "set_approval_handler", fake_set_handler)

    async def approve(_card):
        return "approved"

    def resolved(_card):
        return None

    pause = {"request_id": "req-2", "operation": "tool.exec", "target": "ls"}
    agent = FakeAgent(
        [
            StreamEvent(
                type="needs_approval",
                content="req-2",
                tool_name="tool.exec",
                tool_args={"target": "ls"},
            ),
            done_event("", needs_approval=[pause]),
        ]
    )
    lifecycle = TurnLifecycle(
        session_id=target.id,
        turn_id="interactive",
        token=token,
        approval=ApprovalPolicy(
            mode="interactive",
            handler=approve,
            principal="tui",
            on_resolved=resolved,
        ),
    )
    lifecycle.start(renew=False)
    await lifecycle.prepare(target, builder=lambda _session: _return(agent))

    events = [event async for event in lifecycle.stream()]

    assert [event.kind for event in events] == ["done"]
    assert calls == [
        (
            approve,
            {"principal": "tui", "turn_id": "interactive", "on_resolved": resolved},
        )
    ]
    assert lifecycle.build_terminal()["needsApproval"][0]["requestId"] == "req-2"

    await lifecycle.close()
    assert calls[-1] == (None, {})


@pytest.mark.asyncio
async def test_renewal_loss_sets_shared_event_and_runs_transport_hook(monkeypatch):
    target = session()
    token = await TurnLifecycle.acquire_claim(target.id, turn_id="lost")
    owned = asyncio.create_task(asyncio.Event().wait())
    reasons: list[str] = []

    class Hooks:
        def on_stage(self, stage: str) -> None:
            return None

        def on_ownership_lost(self, reason: str) -> None:
            reasons.append(reason)
            owned.cancel()

    async def lose(*_args, **_kwargs):
        return False

    monkeypatch.setattr(turns, "renew_turn", lose)
    lifecycle = TurnLifecycle(
        session_id=target.id,
        turn_id="lost",
        token=token,
        hooks=Hooks(),
        renew_interval_s=0.01,
    )
    lifecycle.start(ownership_lost=asyncio.Event())
    await asyncio.wait_for(lifecycle.ownership_lost.wait(), timeout=2)
    assert reasons == ["claim_renewal_failed"]
    if not owned.done():
        await asyncio.wait({owned}, timeout=1)
    assert owned.cancelled()

    close = await lifecycle.close(cancelled=True)
    assert close.ownership_lost is True
    assert close.release_failed is False


@pytest.mark.asyncio
async def test_step_supervisor_raises_on_loss_and_cancels_the_step():
    lost = asyncio.Event()
    supervisor = TurnStepSupervisor(ownership_lost=lost)
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.Event().wait()

    run = asyncio.create_task(supervisor.run(slow()))
    await started.wait()
    lost.set()

    with pytest.raises(StaleOwnershipError):
        await run
    pending = supervisor.active
    if pending is not None and not pending.done():
        await asyncio.wait({pending}, timeout=1)
    assert pending is None or pending.cancelled()


@pytest.mark.asyncio
async def test_step_supervisor_times_out_and_cancels_the_step():
    supervisor = TurnStepSupervisor()
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.Event().wait()

    run = asyncio.create_task(supervisor.run(slow(), timeout=0.01))
    await started.wait()

    with pytest.raises(TimeoutError):
        await run
    pending = supervisor.active
    if pending is not None and not pending.done():
        await asyncio.wait({pending}, timeout=1)
    assert pending is None or pending.cancelled()


@pytest.mark.asyncio
async def test_non_streaming_run_captures_final_and_releases_claim():
    target = session()
    token = await TurnLifecycle.acquire_claim(target.id, turn_id="job")
    final = AgentResponse(content="job done")
    agent = FakeAgent(final=final)
    lifecycle = TurnLifecycle(
        session_id=target.id,
        turn_id="job",
        token=token,
    )
    lifecycle.start(renew=False)
    await lifecycle.prepare(target, builder=lambda _session: _return(agent))

    response = await lifecycle.run(prompt="job prompt")

    assert response is final
    assert lifecycle.final is final
    assert agent.prompts == ["job prompt"]
    close = await lifecycle.close()
    assert close.release_failed is False
    next_token = await TurnLifecycle.acquire_claim(target.id, turn_id="next")
    assert next_token
    await turns.end_turn(target.id, next_token)


@pytest.mark.asyncio
async def test_scheduler_job_uses_lifecycle_and_releases_the_turn():
    target = session()
    final = AgentResponse(content="job done")
    agent = FakeAgent(final=final)
    runner = JobRunner(agent_factory=lambda _name: agent)
    now = datetime.now(UTC)
    job = Job(
        id=uuid.uuid4(),
        name="fixture",
        kind="interval",
        session_id=target.id,
        agent_name="harness",
        prompt="job prompt",
        interval_seconds=60,
        enabled=True,
        status="active",
        last_run_at=None,
        next_run_at=now,
        last_error=None,
        run_count=0,
    )

    await runner._execute(job)

    assert agent.prompts == ["job prompt"]
    next_token = await TurnLifecycle.acquire_claim(target.id, turn_id="next")
    assert next_token
    await turns.end_turn(target.id, next_token)


@pytest.mark.asyncio
async def test_scheduler_typed_pause_is_raised_and_still_releases_the_turn():
    target = session()
    final = AgentResponse(
        content="",
        needs_approval=[{"request_id": "req-job", "operation": "tool.exec"}],
    )
    runner = JobRunner(agent_factory=lambda _name: FakeAgent(final=final))
    now = datetime.now(UTC)
    job = Job(
        id=uuid.uuid4(),
        name="fixture",
        kind="interval",
        session_id=target.id,
        agent_name="harness",
        prompt="job prompt",
        interval_seconds=60,
        enabled=True,
        status="active",
        last_run_at=None,
        next_run_at=now,
        last_error=None,
        run_count=0,
    )

    with pytest.raises(PermissionError, match="needs_approval"):
        await runner._execute(job)

    next_token = await TurnLifecycle.acquire_claim(target.id, turn_id="next")
    assert next_token
    await turns.end_turn(target.id, next_token)


@pytest.mark.asyncio
async def test_scheduler_no_agent_job_still_releases_the_turn(monkeypatch):
    target = session()
    runner = JobRunner(agent_factory=lambda _name: FakeAgent())
    calls: list[str] = []

    async def no_agent(job):
        calls.append(job.name)

    monkeypatch.setattr(runner, "_execute_no_agent_job", no_agent)
    now = datetime.now(UTC)
    job = Job(
        id=uuid.uuid4(),
        name="script-fixture",
        kind="interval",
        session_id=target.id,
        agent_name="harness",
        prompt="",
        interval_seconds=60,
        enabled=True,
        status="active",
        last_run_at=None,
        next_run_at=now,
        last_error=None,
        run_count=0,
        no_agent=True,
        script_path="fixture.py",
    )

    await runner._execute(job)

    assert calls == ["script-fixture"]
    next_token = await TurnLifecycle.acquire_claim(target.id, turn_id="next")
    assert next_token
    await turns.end_turn(target.id, next_token)


def test_cli_terminal_report_prints_only_the_withheld_suffix(monkeypatch):
    import importlib

    from machina import cli

    calls: list[tuple[str, str]] = []

    class FakeConsole:
        def print(self, *_args, **_kwargs) -> None:
            calls.append(("print", ""))

        def out(self, text, **_kwargs) -> None:
            calls.append(("out", text))

    # The reporting helper lives in machina.cli.chat after the CLI split; patch
    # that module's console (the re-exported function reads it there). The
    # package attribute ``machina.cli.chat`` is the command callback, so resolve
    # the module explicitly.
    chat_module = importlib.import_module("machina.cli.chat")
    monkeypatch.setattr(chat_module, "console", FakeConsole())
    final = AgentResponse(content="prefix tail")

    status = cli._report_terminal_response(final, ["prefix "], terminal_text="prefix tail")

    assert status == 0
    assert calls[-1] == ("out", "tail")


async def _return(value):
    return value
