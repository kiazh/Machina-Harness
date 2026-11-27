"""Golden-trace harness for the ReAct turn loop (plan R0.2).

The harness drives the real :func:`machina.core.react_loop.react_loop` with a
scripted provider and a minimal agent. Only infrastructure outside the loop is
stubbed: context preparation, workspace snapshots, the request gate, the
tool-batch executor, and the DB-backed sinks used by the answer phase (usage
ledger, session activity, accepted-completion attribution). The loop itself,
``turn_flow``, ``turn_state``, ``task_run``, ``llm_calls`` retry accounting and
``turn_phases`` answer logic all run for real.

Traces never contain raw text, ids, timestamps, or UUIDs: content is recorded
as a length plus SHA-256, tool arguments/results/payloads as hashes, and ids
are normalized to ``<uuid>``.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import re
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from machina.core.agent_support import LoopTerminal
from machina.core.llm_calls import LLMCaller
from machina.core.models import AgentResponse, LLMResponse, Session, StreamEvent, ToolDefinition
from machina.core.prompt import append_tool_messages
from machina.core.request_gate import RequestGate
from machina.core.turn_phases import TurnPhases

GOLDEN_DIR = Path(__file__).with_name("turns")
HARNESS_AGENT_ID = "harness"
HARNESS_AGENT_DEFS = ("read_file", "write_file", "terminal")

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(value: str) -> str:
    """Drop ids and clocks so a trace is reproducible across runs."""
    return _ISO_RE.sub("<timestamp>", _UUID_RE.sub("<uuid>", value))


def normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, Mapping):
        return {str(key): normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(normalize_value(value), sort_keys=True, separators=(",", ":"), default=str)


def canonical_trace(trace: Mapping[str, Any]) -> str:
    return json.dumps(normalize_value(trace), indent=2, sort_keys=True) + "\n"


# ── Scripted provider ───────────────────────────────────────────────────────


class ProviderHTTPError(RuntimeError):
    """Provider failure shaped like an httpx error (status only is observed)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.response = SimpleNamespace(status_code=status)


@dataclass(frozen=True)
class HangStream:
    """A stream that emits one delta and never completes (cancellation case)."""

    text: str = "partial "


@dataclass(frozen=True)
class ToolStep:
    name: str
    args: dict[str, Any]
    result: str
    outcome: str = "completed"
    returncode: int | None = None
    needs_approvals: tuple[dict[str, Any], ...] = ()
    delegated_tokens: int = 0


@dataclass(frozen=True)
class ToolBatch:
    """One scripted tool batch (stands in for the ToolBatchExecutor)."""

    steps: tuple[ToolStep, ...]
    # Completion order indexes; defaults to request order.
    completion_order: tuple[int, ...] | None = None


@dataclass(frozen=True)
class TurnSpec:
    prompt: str
    stream: bool = False
    complete_steps: tuple[Any, ...] = ()
    stream_steps: tuple[Any, ...] = ()
    tool_batches: tuple[ToolBatch, ...] = ()
    auto_parallel: AgentResponse | None = None
    cancel: bool = False
    tool_defs: tuple[str, ...] = HARNESS_AGENT_DEFS
    token_budget: int | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    turns: tuple[TurnSpec, ...]


class ScriptedProvider:
    """Deterministic provider: one scripted step per attempt."""

    def __init__(
        self,
        *,
        complete_steps: Iterable[Any] = (),
        stream_steps: Iterable[Any] = (),
    ) -> None:
        self.model = "scripted-model"
        self.provider_name = "fixture"
        self.spec = SimpleNamespace(name="fixture")
        self._steps: dict[str, list[Any]] = {
            "complete": list(complete_steps),
            "stream": list(stream_steps),
        }
        self.attempts: list[dict[str, Any]] = []
        self.streaming = asyncio.Event()
        self.release = asyncio.Event()

    def _pop(self, kind: str) -> Any:
        queue = self._steps[kind]
        if not queue:
            raise AssertionError(f"unexpected provider {kind} call; script exhausted")
        return queue.pop(0)

    def _record(self, kind: str, step: Any, messages: list[dict[str, Any]], tools: Any) -> None:
        status = getattr(getattr(step, "response", None), "status_code", None)
        if isinstance(step, BaseException):
            outcome = "error"
        elif isinstance(step, HangStream):
            outcome = "hang"
        else:
            outcome = "ok"
        self.attempts.append(
            {
                "kind": kind,
                "outcome": outcome,
                "status": status,
                "messages": len(messages),
                "tools": len(tools or []),
            }
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: Any = None,
        **_kwargs: Any,
    ) -> LLMResponse:
        step = self._pop("complete")
        self._record("complete", step, messages, tools)
        if isinstance(step, BaseException):
            raise step
        if not isinstance(step, LLMResponse):
            raise AssertionError(f"unsupported complete step: {step!r}")
        return step

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: Any = None,
        **_kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        step = self._pop("stream")
        self._record("stream", step, messages, tools)
        if isinstance(step, HangStream):
            yield StreamEvent(type="text", content=step.text)
            self.streaming.set()
            await self.release.wait()
            return
        if isinstance(step, BaseException):
            raise step
        if not isinstance(step, LLMResponse):
            raise AssertionError(f"unsupported stream step: {step!r}")
        if step.content:
            yield StreamEvent(type="text", content=step.content)
        yield StreamEvent(type="done", response=step)

    def assert_drained(self) -> None:
        pending = {kind: len(steps) for kind, steps in self._steps.items() if steps}
        if pending:
            raise AssertionError(f"unconsumed provider script: {pending}")


class _FakeUsageStore:
    """No DB: call the provider and treat every reservation as already closed."""

    async def complete_call(
        self,
        provider: ScriptedProvider,
        _session_id: Any,
        _agent_id: str,
        messages: list[dict[str, Any]],
        *,
        tools: Any = None,
        **_kwargs: Any,
    ) -> LLMResponse:
        return await provider.complete(messages, tools=tools)

    async def prepare_stream(
        self,
        _agent: Any,
        _session_id: Any,
        messages: list[dict[str, Any]],
        _tools: Any,
    ) -> list[dict[str, Any]]:
        return messages

    async def reserve(self, *_args: Any, **_kwargs: Any) -> None:
        return None


async def _no_sleep(*_args: Any, **_kwargs: Any) -> None:
    return None


async def _no_finish_usage(*_args: Any, **_kwargs: Any) -> None:
    return None


# ── Recording ───────────────────────────────────────────────────────────────


def _chunk_summary(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    types = Counter(str(chunk.get("chunk_type") or "") for chunk in chunks)
    payloads = [sha256(canonical_json(chunk.get("payload") or {})) for chunk in chunks]
    return {
        "chunks": len(chunks),
        "chunk_types": dict(sorted(types.items())),
        "payload_sha256": payloads,
    }


class Recorder:
    """Everything the loop does outside the event stream, order preserved."""

    def __init__(self) -> None:
        self.audits: list[str] = []
        self.flushes: list[dict[str, Any]] = []
        self.markers: list[dict[str, Any]] = []
        self.token_metrics: list[list[int]] = []
        self.gates: list[dict[str, int]] = []
        self.tool_batches: list[dict[str, Any]] = []
        self.snapshots: list[str] = []
        self.completions: list[dict[str, Any]] = []
        self.condense: list[dict[str, Any]] = []
        self.consolidations = 0
        self.learning_reviews = 0
        self.auto_parallel_calls = 0

    # audit_log-shaped callable
    def audit(self, event: Any, *_args: Any, **_kwargs: Any) -> None:
        self.audits.append(str(event))

    def flush(self, pending: list[dict[str, Any]]) -> None:
        if pending:
            self.flushes.append(_chunk_summary(pending))

    def marker(self, **kwargs: Any) -> None:
        self.markers.append(
            {
                "chunk_type": str(kwargs.get("chunk_type") or ""),
                "payload_sha256": sha256(canonical_json(kwargs.get("payload") or {})),
            }
        )

    def record_tokens(self, _session_id: Any, prompt: int, completion: int) -> None:
        self.token_metrics.append([int(prompt or 0), int(completion or 0)])

    def tool_batch(self, requested: list[str], completed: list[str], recorded: list[str]) -> None:
        self.tool_batches.append(
            {"requested": requested, "completion_order": completed, "recorded": recorded}
        )

    def completion(self, content: str, outcome: str, tool_calls: int) -> None:
        self.completions.append(
            {
                "outcome": outcome,
                "content_sha256": sha256(content),
                "tool_calls": tool_calls,
            }
        )


# ── Stubs for infrastructure outside the loop ──────────────────────────────


def _apply_stubs(monkeypatch: Any, recorder: Recorder, spec: TurnSpec) -> None:
    from machina.core import execution_context as execution_module
    from machina.core import llm_calls as llm_calls_module
    from machina.core import react_loop as loop_module
    from machina.core import shell_jobs as shell_jobs_module
    from machina.core import turn_phases as turn_phases_module
    from machina.core.orchestration import auto as auto_module
    from machina.workspace import snapshots as snapshots_module

    monkeypatch.setenv("MACHINA_REPAIR_DEADLINE_SECONDS", "0")
    monkeypatch.setenv("MACHINA_REPAIR_TOKEN_BUDGET", "0")
    monkeypatch.delenv("MACHINA_TASK_RUN_MAX_TURNS", raising=False)
    monkeypatch.delenv("MACHINA_TASK_RUN_MAX_TOOL_CALLS", raising=False)
    monkeypatch.delenv("MACHINA_REPAIR_LEAN_TOOLS", raising=False)
    if spec.token_budget is not None:
        monkeypatch.setenv("MACHINA_REPAIR_TOKEN_BUDGET", str(spec.token_budget))

    async def gate(messages: list[dict[str, Any]], **kwargs: Any) -> RequestGate:
        recorder.gates.append(
            {
                "messages": len(messages),
                "tools": len(kwargs.get("tool_defs") or []),
                "total_tokens": int(kwargs.get("total_tokens") or 0),
                "iterations": int(kwargs.get("iterations") or 0),
                "tool_calls": len(kwargs.get("tool_calls_made") or []),
            }
        )
        return RequestGate(messages=messages, effective_budget=1000, request_tokens=10)

    async def maybe_auto_parallel(*_args: Any, **_kwargs: Any) -> AgentResponse | None:
        recorder.auto_parallel_calls += 1
        return spec.auto_parallel

    async def resolve_execution_context(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(workspace=Path("/fixture-workspace"))

    def begin_turn(**_kwargs: Any) -> object:
        recorder.snapshots.append("begin")
        return object()

    def end_turn(_snapshot: Any) -> None:
        recorder.snapshots.append("end")

    def apply_job_updates(_session_id: Any, _messages: Any) -> None:
        return None

    async def update_activity(_session_id: Any) -> None:
        return None

    async def mark_turn_accepted(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def add_chunk(**kwargs: Any) -> None:
        recorder.marker(**kwargs)

    monkeypatch.setattr(loop_module, "evaluate_request", gate)
    monkeypatch.setattr(loop_module, "audit_log", recorder.audit)
    monkeypatch.setattr(
        loop_module,
        "metrics",
        SimpleNamespace(record_tokens=recorder.record_tokens),
    )
    monkeypatch.setattr(turn_phases_module, "audit_log", recorder.audit)
    monkeypatch.setattr(
        turn_phases_module,
        "session_manager",
        SimpleNamespace(update_activity=update_activity),
    )
    monkeypatch.setattr(turn_phases_module, "mark_turn_accepted", mark_turn_accepted)
    monkeypatch.setattr(
        turn_phases_module,
        "context_manager",
        SimpleNamespace(add_chunk=add_chunk),
    )
    monkeypatch.setattr(llm_calls_module, "usage_store", _FakeUsageStore())
    monkeypatch.setattr(llm_calls_module, "provider_identity", lambda _provider: "fixture")
    monkeypatch.setattr(llm_calls_module, "finish_usage", _no_finish_usage)
    monkeypatch.setattr(
        llm_calls_module,
        "asyncio",
        SimpleNamespace(sleep=_no_sleep),
    )
    monkeypatch.setattr(snapshots_module, "begin_turn", begin_turn)
    monkeypatch.setattr(snapshots_module, "end_turn", end_turn)
    monkeypatch.setattr(execution_module, "resolve_execution_context", resolve_execution_context)
    monkeypatch.setattr(auto_module, "maybe_auto_parallel", maybe_auto_parallel)
    monkeypatch.setattr(shell_jobs_module, "apply_job_updates", apply_job_updates)


def _build_agent(
    provider: ScriptedProvider,
    recorder: Recorder,
    spec: TurnSpec,
) -> tuple[Any, Session, list[ToolBatch]]:
    session_id = uuid.UUID(int=0)
    session = Session(id=session_id, agent_id=HARNESS_AGENT_ID)
    sessions = [
        {"role": "system", "content": "fixture system prompt"},
        {"role": "user", "content": spec.prompt},
    ]
    batches = list(spec.tool_batches)

    async def prepare_context(
        session_id: uuid.UUID,
        user_message: str,
        include_memory: bool = True,
        pending_chunks: list[dict[str, Any]] | None = None,
        images: list[dict[str, str]] | None = None,
    ) -> tuple[Session, list[dict[str, Any]]]:
        del include_memory, images
        if pending_chunks is None:
            pending_chunks = []
        pending_chunks.append(
            {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "agent_id": HARNESS_AGENT_ID,
                "chunk_type": "user_message",
                "payload": {"content": user_message},
                "token_count": 1,
            }
        )
        return session, [dict(message) for message in sessions]

    async def flush_pending_chunks(pending: list[dict[str, Any]]) -> bool:
        recorder.flush(pending)
        pending.clear()
        return True

    def condense_history(_messages: list[dict[str, Any]], *, stale_chars: int | None = None):
        recorder.condense.append({"stale_chars": stale_chars})

    def tool_defs() -> list[ToolDefinition]:
        return [ToolDefinition(name=name, description="", parameters={}) for name in spec.tool_defs]

    def take_batch(response: LLMResponse) -> ToolBatch:
        if not batches:
            raise AssertionError("unexpected tool batch")
        batch = batches.pop(0)
        if len(batch.steps) != len(response.tool_calls):
            raise AssertionError(
                f"batch has {len(batch.steps)} steps for {len(response.tool_calls)} calls"
            )
        return batch

    def execute_batch(
        batch: ToolBatch,
        response: LLMResponse,
        messages: list[dict[str, Any]],
        tool_calls_made: list[dict[str, Any]],
        *,
        stream: bool,
    ) -> list[StreamEvent]:
        steps = list(batch.steps)
        calls = list(response.tool_calls)
        names = [str(call.get("function", {}).get("name") or "") for call in calls]
        if [step.name for step in steps] != names:
            raise AssertionError("batch steps do not match response tool calls")
        order = list(batch.completion_order or range(len(steps)))
        if sorted(order) != list(range(len(steps))):
            raise AssertionError("completion_order must cover every step once")

        events: list[StreamEvent] = []
        if stream:
            for index, call in enumerate(calls):
                events.append(
                    StreamEvent(
                        type="tool_call",
                        tool_name=steps[index].name,
                        tool_args=dict(steps[index].args),
                        tool_call_id=str(call.get("id") or ""),
                    )
                )
        for index in order:
            step = steps[index]
            call = calls[index]
            call_id = str(call.get("id") or "")
            if stream:
                events.append(
                    StreamEvent(
                        type="tool_result",
                        tool_name=step.name,
                        tool_result=step.result,
                        tool_call_id=call_id,
                        tool_outcome=step.outcome,
                    )
                )
                for pause in step.needs_approvals:
                    if not isinstance(pause, dict) or not pause.get("request_id"):
                        continue
                    events.append(
                        StreamEvent(
                            type="needs_approval",
                            content=str(pause.get("request_id")),
                            tool_name=step.name,
                            tool_args={
                                "requestId": str(pause.get("request_id")),
                                "sessionId": str(pause.get("session_id", "")),
                                "operation": str(pause.get("operation", "")),
                                "target": str(pause.get("target", "")),
                                "status": str(pause.get("status", "pending")),
                            },
                            tool_call_id=call_id,
                        )
                    )
        for call, step in zip(calls, steps, strict=True):
            entry: dict[str, Any] = {
                "tool": step.name,
                "args": dict(step.args),
                "result": step.result,
                "result_preview": step.result[:500],
                "outcome": step.outcome,
            }
            if step.returncode is not None:
                entry["returncode"] = step.returncode
            if step.needs_approvals:
                entry["needs_approvals"] = [dict(pause) for pause in step.needs_approvals]
            if step.delegated_tokens:
                entry["delegated_tokens"] = step.delegated_tokens
            tool_calls_made.append(entry)
            append_tool_messages(messages, response, call, step.result)
        recorder.tool_batch(
            names,
            [names[index] for index in order],
            [step.name for step in steps],
        )
        return events

    async def execute_stream(
        response: LLMResponse,
        messages: list[dict[str, Any]],
        tool_calls_made: list[dict[str, Any]],
        _session_id: uuid.UUID,
        pending_chunks: list[dict[str, Any]] | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del pending_chunks, allowed_tool_names
        batch = take_batch(response)
        for event in execute_batch(batch, response, messages, tool_calls_made, stream=True):
            yield event

    async def execute(
        response: LLMResponse,
        messages: list[dict[str, Any]],
        tool_calls_made: list[dict[str, Any]],
        _session_id: uuid.UUID,
        _verbose: bool,
        pending_chunks: list[dict[str, Any]] | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> None:
        del pending_chunks, allowed_tool_names
        batch = take_batch(response)
        execute_batch(batch, response, messages, tool_calls_made, stream=False)

    caller = LLMCaller(provider, HARNESS_AGENT_ID)
    agent = SimpleNamespace(
        agent_id=HARNESS_AGENT_ID,
        provider=provider,
        allowed_tools=None,
        delegated=False,
        _prepare_context=prepare_context,
        _flush_pending_chunks=flush_pending_chunks,
        _condense_history=condense_history,
        _tool_defs=tool_defs,
        _call_llm_with_retry=caller.complete,
        _stream_llm_with_retry=caller.stream,
        _execute_tool_calls=execute,
        _execute_tool_calls_stream=execute_stream,
        _maybe_continue_unchanged_repair=None,
        _maybe_continue_unverified_repair=None,
        _schedule_memory_consolidation=lambda _session_id: _count_consolidation(recorder),
        _schedule_learning_review=lambda *_args, **_kwargs: _count_learning(recorder),
    )
    phases = TurnPhases(agent)
    agent._append_assistant_content = phases.append_assistant_content
    agent._append_task_run_nudge = phases.append_task_run_nudge
    agent._continue_after_empty_tool_turn = phases.continue_after_empty_tool_turn
    agent._recover_empty_final = phases.recover_empty_final
    agent._finish_turn_completion = _completion_stub(phases, recorder)
    agent._flush_pending_cancellation_safe = phases.flush_pending_cancellation_safe
    return agent, session, batches


def _count_consolidation(recorder: Recorder) -> None:
    recorder.consolidations += 1


def _count_learning(recorder: Recorder) -> None:
    recorder.learning_reviews += 1


def _completion_stub(phases: TurnPhases, recorder: Recorder):
    """Run the answer phase but skip only its out-of-loop attribution sinks."""

    async def finish(
        session_id: uuid.UUID,
        user_message: str,
        content: str,
        tool_calls_made: list[dict[str, Any]],
        total_tokens: int,
        iterations: int,
        pending_chunks: list[dict[str, Any]],
        *,
        outcome: str = "completed",
    ) -> AgentResponse:
        recorder.completion(content, outcome, len(tool_calls_made))
        return await phases.finish_turn_completion(
            session_id,
            user_message,
            content,
            tool_calls_made,
            total_tokens,
            iterations,
            pending_chunks,
            outcome=outcome,
        )

    return finish


# ── Trace normalization ─────────────────────────────────────────────────────


def terminal_summary(response: AgentResponse) -> dict[str, Any]:
    return {
        "outcome": str(response.outcome),
        "content_len": len(response.content),
        "content_sha256": sha256(response.content),
        "tokens_used": int(response.tokens_used),
        "iterations": int(response.iterations),
        "tool_names": [str(entry.get("tool") or "") for entry in response.tool_calls],
        "needs_approval": [
            {
                "request_id": normalize_text(str(pause.get("request_id") or "")),
                "operation": str(pause.get("operation") or ""),
            }
            for pause in response.needs_approval
        ],
    }


def event_trace(event: StreamEvent) -> dict[str, Any]:
    data: dict[str, Any] = {"type": event.type}
    if event.type == "text":
        data["text_len"] = len(event.content)
        data["text_sha256"] = sha256(event.content)
    elif event.content:
        data["content_sha256"] = sha256(normalize_text(event.content))
    if event.tool_name:
        data["tool_name"] = event.tool_name
    if event.tool_call_id:
        data["tool_call_id"] = normalize_text(event.tool_call_id)
    if event.tool_args:
        data["tool_args_sha256"] = sha256(canonical_json(event.tool_args))
    if event.type == "tool_result":
        data["tool_result_len"] = len(event.tool_result)
        data["tool_result_sha256"] = sha256(event.tool_result)
        data["tool_outcome"] = event.tool_outcome
    if event.type == "token_usage":
        data["tokens_used"] = int(event.tokens_used)
        data["context_tokens"] = int(event.context_tokens)
        data["context_usage_percentage"] = event.context_usage_percentage
        data["credits_used"] = event.credits_used
    return data


def _turn_trace(
    events: list[Any],
    recorder: Recorder,
    provider: ScriptedProvider,
    *,
    cancelled: bool,
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    terminal: dict[str, Any] | None = None
    for index, event in enumerate(events):
        if isinstance(event, LoopTerminal):
            summary = terminal_summary(event.response)
            normalized.append({"seq": index, "type": "terminal", **summary})
            terminal = {"seq": index, **summary}
        else:
            normalized.append({"seq": index, **event_trace(event)})
    return {
        "events": normalized,
        "terminal": terminal,
        "cancelled": cancelled,
        "audits": list(recorder.audits),
        "gates": recorder.gates,
        "provider_attempts": provider.attempts,
        "tool_batches": recorder.tool_batches,
        "flushes": recorder.flushes,
        "markers": recorder.markers,
        "token_metrics": recorder.token_metrics,
        "condense": recorder.condense,
        "snapshots": recorder.snapshots,
        "completions": recorder.completions,
        "consolidations": recorder.consolidations,
        "learning_reviews": recorder.learning_reviews,
        "auto_parallel_calls": recorder.auto_parallel_calls,
    }


# ── Driving the real loop ──────────────────────────────────────────────────


async def _run_turn(spec: TurnSpec, monkeypatch: Any) -> dict[str, Any]:
    from machina.core.react_loop import react_loop

    recorder = Recorder()
    provider = ScriptedProvider(
        complete_steps=spec.complete_steps,
        stream_steps=spec.stream_steps,
    )
    _apply_stubs(monkeypatch, recorder, spec)
    agent, session, remaining_batches = _build_agent(provider, recorder, spec)
    loop = react_loop(
        agent,
        session.id,
        spec.prompt,
        verbose=False,
        images=None,
        stream=spec.stream,
    )

    events: list[Any] = []
    cancelled = False
    if spec.cancel:

        async def collect() -> None:
            async for event in loop:
                events.append(event)

        task = asyncio.create_task(collect())
        await asyncio.wait_for(provider.streaming.wait(), timeout=5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            cancelled = True
        if not task.cancelled():
            raise AssertionError("cancelled turn did not finish through CancelledError")
    else:
        async for event in loop:
            events.append(event)

    provider.assert_drained()
    if remaining_batches:
        raise AssertionError(f"unconsumed tool batches: {len(remaining_batches)}")
    trace = _turn_trace(events, recorder, provider, cancelled=cancelled)
    if not cancelled and trace["terminal"] is None:
        raise AssertionError("turn ended without a terminal response")
    return trace


async def run_scenario(scenario: Scenario, monkeypatch: Any) -> dict[str, Any]:
    """Run every turn of a scenario and return its normalized trace."""
    turns = []
    for spec in scenario.turns:
        turns.append(await _run_turn(spec, monkeypatch))
    return {"scenario": scenario.name, "turns": turns}


def assert_no_raw_text(trace: Mapping[str, Any]) -> None:
    """Guard the redaction contract: no prose, only ids/hashes/counters."""

    def walk(value: Any) -> None:
        if isinstance(value, str):
            # Hashes are 64 chars; names, ids and normalized tokens are short.
            assert len(value) <= 80, f"trace carries raw text: {value[:40]!r}"
        elif isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(trace)


def trace_diff(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> str:
    actual_text = canonical_trace(actual)
    expected_text = canonical_trace(expected)
    return "".join(
        difflib.unified_diff(
            expected_text.splitlines(keepends=True),
            actual_text.splitlines(keepends=True),
            fromfile="golden",
            tofile="actual",
        )
    )


def assert_trace_matches(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    if actual != expected:
        raise AssertionError("golden trace mismatch\n" + trace_diff(actual, expected))
