"""Phase helpers for the ReAct turn policy.

Interface
---------
``react_loop`` owns the ``while`` loop and yields phase events in order. This
module owns the five phases it calls:

- ``prepare_turn``: context preparation, workspace snapshot, the immediate
  user-message flush, and the optional auto-parallel synthesis.
- ``prepare_model_step``: job/steering updates, stop checks, condensation, the
  tool surface, and the request gate.
- ``run_model_step``: the stream/non-stream provider call, usage accounting,
  and post-response steering.
- ``run_tool_step``: the stream/non-stream tool batch and its batch tail.
- ``finish_answer``: empty-response recovery, repair/verification nudges, and
  tool-free completion.

``new_turn_state`` and ``finish_turn`` own the turn's shared state lifetime
and cleanup. The terminal helpers own the exact stopped/failed response
shapes, flushes, audits, and consolidation scheduling.

Invariants
----------
- Phases run in the order above; each one mutates the shared ``TurnState`` and
  reports its outcome through the small result dataclasses, never through
  loose turn locals.
- Event, audit, flush, and scheduling order is identical to the previous
  single-function loop. The orchestrator yields phase events unchanged.
- Phase generators are closed synchronously by ``react_loop`` (``aclosing``)
  so stream teardown and cancellation flushes keep their original timing.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rich.console import Console

from machina.core.agent_support import LoopTerminal as _LoopTerminal
from machina.core.agent_support import assistant_chunk as _assistant_chunk
from machina.core.background_tasks import joined_teardown
from machina.core.exceptions import UsageBudgetExceededError
from machina.core.models import AgentResponse, LLMResponse, Session, StreamEvent
from machina.core.request_gate import RequestGate
from machina.core.session import session_manager
from machina.core.task_run import TaskRunPolicy
from machina.core.turn_flow import (
    collect_pauses,
    pause_response,
    provider_failure_response,
    stagnation_step,
    stopped_response,
)
from machina.core.turn_state import (
    STOP_REPAIR_DEADLINE,
    STOP_REPAIR_TOKEN_BUDGET,
    TurnBudget,
    TurnState,
    TurnStop,
)
from machina.observability.diagnostics import record_failure

if TYPE_CHECKING:
    from machina.core.agent import BaseReActAgent

__all__ = [
    "AnswerResult",
    "ModelPlan",
    "ModelResult",
    "ToolResult",
    "finish_answer",
    "finish_turn",
    "new_turn_state",
    "prepare_model_step",
    "prepare_turn",
    "record_turn_advisory",
    "run_model_step",
    "run_tool_step",
]

console = Console()

Audit = Callable[..., None]


def record_turn_advisory(agent: BaseReActAgent, user_message: str) -> None:
    """Record the turn-start advisory signals; never breaks the turn."""
    # S3.1(c)+S3.2 BEGIN turn-start advisory (revert: delete block; recorded/logged only, never breaks turn)
    try:
        from machina.controller import wiring as _s3w

        _s3w.record_triage(agent, user_message)
        _s3w.log_complexity(agent, user_message)
    except Exception:
        pass
    # S3.1(c)+S3.2 END turn-start advisory


def new_turn_state(session_id: uuid.UUID, user_message: str) -> TurnState:
    """Create the per-turn state and publish the repair-network block."""
    budget = TurnBudget.for_turn(user_message)
    state = TurnState(
        session_id=session_id,
        # Context preparation populates messages before the first model step.
        messages=[],
        pending_chunks=[],
        task_run=TaskRunPolicy(user_message),
        budget=budget,
    )
    from machina.core.task_run import set_repair_network_blocked

    set_repair_network_blocked(state.task_run.active and state.task_run.lean_tools)
    return state


async def finish_turn(agent: BaseReActAgent, state: TurnState) -> Any:
    """Close the turn snapshot and flush any remaining pending chunks."""
    from machina.workspace.snapshots import end_turn

    end_turn(state.turn_snapshot)
    return await agent._flush_pending_chunks(state.pending_chunks)


async def prepare_turn(
    agent: BaseReActAgent,
    state: TurnState,
    *,
    user_message: str,
    images: list[dict[str, str]] | None,
    stream: bool,
    audit: Audit,
) -> tuple[Session, AgentResponse | None]:
    """Prepare context/snapshot/persistence and run auto-parallel planning.

    Returns the fresh session and, when auto-parallel owned the turn, its
    synthesis response. A failed snapshot store is additive and never fails
    the turn; a cancelled user-message flush is persisted with the
    cancellation-safe path before it is re-raised.
    """
    session, messages = await agent._prepare_context(
        state.session_id,
        user_message,
        include_memory=True,
        pending_chunks=state.pending_chunks,
        images=images,
    )
    state.messages = messages
    try:
        from machina.core.execution_context import resolve_execution_context
        from machina.workspace.snapshots import begin_turn

        context = await resolve_execution_context(str(state.session_id), "terminal")
        state.turn_snapshot = begin_turn(
            session_id=str(state.session_id),
            workspace=context.workspace,
            prompt=user_message,
            provider=str(getattr(session, "provider", "") or ""),
            model=str(getattr(session, "model", "") or ""),
        )
    except Exception as snapshot_error:
        # Snapshot support is additive; a failed store must not fail
        # the turn. The file tool remains authoritative.
        state.turn_snapshot = None
        audit(
            "agent_snapshot_error",
            session_id=str(state.session_id),
            error=str(snapshot_error),
        )
    # AH-015: persist the user message immediately so cancellation during
    # the first LLM call cannot lose it. Wrapped like the stream path (F3):
    # a CancelledError during this DB write must still persist the user
    # message with an interruption marker rather than dropping the batch.
    try:
        await agent._flush_pending_chunks(state.pending_chunks)
    except asyncio.CancelledError:
        await agent._flush_pending_cancellation_safe(state.pending_chunks, state.session_id)
        raise

    # Explicit parallel requests are harness-owned in multi-model
    # mode: the plan is structured, validated, and executed in the
    # first wave without relying on the model to emit N delegate
    # tool calls. A failure here falls back to the normal loop.
    try:
        from machina.core.orchestration.auto import maybe_auto_parallel

        auto_response = await maybe_auto_parallel(
            agent,
            session,
            state.session_id,
            user_message,
            state.pending_chunks,
        )
        return session, auto_response
    except asyncio.CancelledError:
        raise
    except Exception as _auto_error:
        record_failure(
            "agent.auto_parallel_stream" if stream else "agent.auto_parallel",
            _auto_error,
        )
        return session, None


@dataclass
class ModelPlan:
    """Request-gate decision for one model step."""

    gate: RequestGate | None = None
    tool_defs: list[Any] = field(default_factory=list)
    terminal: bool = False


@dataclass
class ModelResult:
    """Outcome of one model step; events are yielded by the phase."""

    response: LLMResponse | None = None
    replanned: bool = False
    terminal: bool = False


@dataclass
class AnswerResult:
    """Outcome of the no-tool/answer phase."""

    response: LLMResponse | None = None
    replanned: bool = False
    terminal: bool = False


@dataclass
class ToolResult:
    """Outcome of one tool batch."""

    replanned: bool = False
    terminal: bool = False


async def terminal_for_stop(
    agent: BaseReActAgent,
    state: TurnState,
    stop: TurnStop,
    *,
    audit: Audit,
) -> AgentResponse:
    """Persist/audit a selected stop, then shape its failed terminal."""
    state.mark_stop(stop)
    if stop.kind == STOP_REPAIR_DEADLINE:
        audit(
            "agent_repair_deadline",
            session_id=str(state.session_id),
            deadline_seconds=state.budget.deadline_seconds,
        )
    elif stop.kind == STOP_REPAIR_TOKEN_BUDGET:
        audit(
            "agent_repair_token_budget",
            session_id=str(state.session_id),
            token_budget=state.budget.token_budget,
            total_tokens=state.total_tokens,
        )
    else:
        audit(
            "agent_task_run_stop",
            session_id=str(state.session_id),
            phase=state.task_run.phase,
            turns=state.iteration,
            tool_calls=len(state.tool_calls_made),
            reason=str(stop.text)[:240],
        )
    await agent._flush_pending_chunks(state.pending_chunks)
    agent._schedule_memory_consolidation(state.session_id)
    return stopped_response(
        stop.text,
        state.tool_calls_made,
        state.total_tokens,
        state.iteration + 1,
    )


async def terminal_for_gate_failure(
    agent: BaseReActAgent,
    state: TurnState,
    response: AgentResponse,
) -> AgentResponse:
    """Flush and schedule consolidation before a request-gate failure."""
    await agent._flush_pending_chunks(state.pending_chunks)
    agent._schedule_memory_consolidation(state.session_id)
    return response


async def terminal_for_budget(
    agent: BaseReActAgent,
    state: TurnState,
    error: Exception,
    *,
    audit: Audit,
) -> AgentResponse:
    """Flush and audit a usage-budget failure terminal."""
    await agent._flush_pending_chunks(state.pending_chunks)
    audit("agent_run_budget_exceeded", session_id=str(state.session_id), error=str(error))
    return AgentResponse(
        content=str(error),
        outcome="failed",
        tool_calls=state.tool_calls_made,
        tokens_used=state.total_tokens,
        iterations=state.iteration,
    )


async def terminal_for_provider_failure(
    agent: BaseReActAgent,
    state: TurnState,
    error: Exception,
    *,
    audit: Audit,
    label: str,
) -> AgentResponse:
    """Flush/audit/schedule the shared provider-failure terminal."""
    return await provider_failure_response(
        agent,
        error,
        session_id=state.session_id,
        pending_chunks=state.pending_chunks,
        tool_calls=state.tool_calls_made,
        tokens=state.total_tokens,
        iterations=state.iteration,
        audit=audit,
        label=label,
    )


def record_stream_usage(
    state: TurnState,
    response: LLMResponse,
    gate: RequestGate,
    metrics: Any,
) -> StreamEvent:
    """Record one billed stream response and build its token-usage event."""
    usage = response.usage
    state.record_usage(usage)
    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0
    metrics.record_tokens(str(state.session_id), prompt_tokens, completion_tokens)
    context_tokens = int(usage.get("context_tokens", 0) or 0)
    if not context_tokens:
        context_tokens = prompt_tokens + completion_tokens if prompt_tokens else gate.request_tokens
    context_percent: float | int | None = usage.get("context_usage_percentage")
    if not isinstance(context_percent, (int, float)) or isinstance(context_percent, bool):
        context_percent = (
            100 * context_tokens / gate.effective_budget if gate.effective_budget > 0 else None
        )
    credits = response.usage.get("credits_used")
    return StreamEvent(
        type="token_usage",
        tokens_used=usage.get("total_tokens", 0),
        context_tokens=context_tokens,
        context_usage_percentage=context_percent,
        credits_used=(
            float(credits)
            if isinstance(credits, (int, float)) and not isinstance(credits, bool)
            else None
        ),
    )


async def prepare_model_step(
    agent: BaseReActAgent,
    state: TurnState,
    session: Any,
    *,
    user_message: str,
    verbose: bool,
    gate_fn: Callable[..., Awaitable[RequestGate]],
    audit: Audit,
    result: ModelPlan,
) -> AsyncGenerator[StreamEvent | _LoopTerminal, None]:
    """Apply turn-start updates and build the next gated model request."""
    from machina.core.shell_jobs import apply_job_updates
    from machina.core.steering import apply_steering

    apply_job_updates(state.session_id, state.messages)
    await apply_steering(state.session_id, agent.agent_id, state.messages)
    stop = state.check_stop()
    if stop is not None:
        yield _LoopTerminal(await terminal_for_stop(agent, state, stop, audit=audit))
        result.terminal = True
        return
    if verbose:
        console.print(f"[dim]Iteration {state.iteration + 1}[/dim]")

    # Capacity applies to this request, never cumulative billed usage.
    stale_chars = state.budget.stale_chars(state.total_tokens)
    if stale_chars is None:
        agent._condense_history(state.messages)
    else:
        agent._condense_history(state.messages, stale_chars=stale_chars)
    # Once a repair has both an edit and passing verification
    # evidence, force the answer step: exposing tools again lets a
    # model keep rerunning tests or inspecting the diff, which
    # costs a full provider round trip without adding evidence.
    tool_defs = (
        []
        if state.task_run.active and state.task_run.edited and state.task_run.verified
        else state.task_run.filter_tool_defs(agent._tool_defs())
    )
    # Keep the dynamic loader inside the lean repair surface too.
    # Otherwise tool_load can add web/network tools after the schema
    # filter has already run, and the stale schema can still be
    # executed from the model's context.
    from machina.core.tool_projection import set_dynamic_allowed_tools

    if state.task_run.active and state.task_run.lean_tools:
        set_dynamic_allowed_tools(sorted(state.task_run.allowed_tool_names()))
    else:
        set_dynamic_allowed_tools(None)
    gate = await gate_fn(
        state.messages,
        session=session,
        provider=agent.provider,
        tool_defs=tool_defs,
        session_id=state.session_id,
        agent_id=agent.agent_id,
        user_message=user_message,
        total_tokens=state.total_tokens,
        iterations=state.iteration,
        tool_calls_made=state.tool_calls_made,
    )
    if gate.failure is not None:
        yield _LoopTerminal(await terminal_for_gate_failure(agent, state, gate.failure))
        result.terminal = True
        return
    result.gate = gate
    result.tool_defs = tool_defs


async def run_model_step(
    agent: BaseReActAgent,
    state: TurnState,
    plan: ModelPlan,
    *,
    stream: bool,
    metrics: Any,
    audit: Audit,
    result: ModelResult,
) -> AsyncGenerator[StreamEvent | _LoopTerminal, None]:
    """Call the provider for one model step and record its usage."""
    gate = plan.gate
    if gate is None:
        raise RuntimeError("model step requires a request gate")
    tool_defs = plan.tool_defs

    if stream:
        streamed_text: list[str] = []
        try:
            final_response = None
            async with joined_teardown(
                stream=agent._stream_llm_with_retry(
                    messages=gate.messages,
                    tools=tool_defs,
                    session_id=state.session_id,
                )
            ) as events:
                async for event in events:
                    if event.type == "text":
                        streamed_text.append(event.content or "")
                        yield StreamEvent(type="text", content=event.content)
                    elif event.type == "done":
                        final_response = event.response
                        yield record_stream_usage(state, event.response, gate, metrics)

            if final_response is None:
                raise RuntimeError("Stream completed without a final response")

        except asyncio.CancelledError:
            if streamed_text:
                content = "".join(streamed_text)
                state.pending_chunks.append(
                    _assistant_chunk(state.session_id, agent.agent_id, content, interrupted=True)
                )
            await agent._flush_pending_cancellation_safe(state.pending_chunks, state.session_id)
            raise
        except UsageBudgetExceededError as e:
            yield _LoopTerminal(await terminal_for_budget(agent, state, e, audit=audit))
            result.terminal = True
            return
        except Exception as e:
            yield _LoopTerminal(
                await terminal_for_provider_failure(
                    agent,
                    state,
                    e,
                    audit=audit,
                    label="LLM stream ultimately failed",
                )
            )
            result.terminal = True
            return

        response = final_response
    else:
        try:
            response = await agent._call_llm_with_retry(
                messages=gate.messages,
                tools=tool_defs,
                session_id=state.session_id,
            )
        except UsageBudgetExceededError as e:
            yield _LoopTerminal(await terminal_for_budget(agent, state, e, audit=audit))
            result.terminal = True
            return
        except Exception as e:
            yield _LoopTerminal(
                await terminal_for_provider_failure(
                    agent,
                    state,
                    e,
                    audit=audit,
                    label="LLM call ultimately failed",
                )
            )
            result.terminal = True
            return

        state.record_usage(response.usage)
        metrics.record_tokens(
            str(state.session_id),
            response.usage.get("prompt_tokens", 0),
            response.usage.get("completion_tokens", 0),
        )

    from machina.core.steering import apply_steering, pending_steering

    if pending_steering(state.session_id) and response.content:
        await agent._append_assistant_content(
            state.session_id, response.content, state.pending_chunks
        )
        await agent._flush_pending_chunks(state.pending_chunks)
    if await apply_steering(state.session_id, agent.agent_id, state.messages):
        state.advance_iteration()
        result.replanned = True
        return
    result.response = response


async def run_tool_step(
    agent: BaseReActAgent,
    state: TurnState,
    response: LLMResponse,
    tool_defs: list[Any],
    *,
    stream: bool,
    verbose: bool,
    audit: Audit,
    result: ToolResult,
) -> AsyncGenerator[StreamEvent | _LoopTerminal, None]:
    """Run one tool batch and its pause/stagnation/task-run tail."""
    await agent._append_assistant_content(state.session_id, response.content, state.pending_chunks)
    batch_start = len(state.tool_calls_made)
    advertised_tool_names = {
        name for tool in tool_defs if (name := getattr(tool, "name", None)) is not None
    }
    # AH-015: flush incrementally and, on cancellation, persist what
    # completed with an explicit interruption marker. (run_stream
    # previously held all history in pending until the final done
    # event, so a CancelledError dropped user/tool records.)
    try:
        if stream:
            async with joined_teardown(
                stream=agent._execute_tool_calls_stream(
                    response,
                    state.messages,
                    state.tool_calls_made,
                    state.session_id,
                    state.pending_chunks,
                    allowed_tool_names=advertised_tool_names,
                )
            ) as events:
                async for event in events:
                    yield event
        else:
            await agent._execute_tool_calls(
                response,
                state.messages,
                state.tool_calls_made,
                state.session_id,
                verbose,
                state.pending_chunks,
                allowed_tool_names=advertised_tool_names,
            )
        await agent._flush_pending_chunks(state.pending_chunks)
    except BaseException:
        await agent._flush_pending_cancellation_safe(state.pending_chunks, state.session_id)
        raise
    batch = state.tool_calls_made[batch_start:]
    state.record_tokens(sum(entry.get("delegated_tokens", 0) for entry in batch))
    pauses = collect_pauses(batch)
    if pauses:
        yield _LoopTerminal(
            pause_response(
                state.tool_calls_made,
                state.total_tokens,
                state.iteration + 1,
                pauses,
            )
        )
        result.terminal = True
        return
    # S3.1(d stagnation hook: shared batch tail (turn_flow owns the
    # guarded wiring access; stagnation-only, never a cap).
    state.progress_history, _s3_text = await stagnation_step(
        state.progress_history,
        batch,
        state.tool_calls_made,
        session_id=state.session_id,
        session_manager=session_manager,
        audit=audit,
        flush=lambda: agent._flush_pending_chunks(state.pending_chunks),
    )
    if _s3_text is not None:
        agent._schedule_memory_consolidation(state.session_id)
        yield _LoopTerminal(
            stopped_response(
                _s3_text,
                state.tool_calls_made,
                state.total_tokens,
                state.iteration + 1,
            )
        )
        result.terminal = True
        return
    state.task_run.observe(batch)
    task_nudge = state.task_run.take_nudge()
    if task_nudge is not None:
        await agent._append_task_run_nudge(
            session_id=state.session_id,
            text=task_nudge,
            messages=state.messages,
            pending_chunks=state.pending_chunks,
        )
        state.advance_iteration()
        result.replanned = True
        return
    state.advance_iteration()


async def finish_answer(
    agent: BaseReActAgent,
    state: TurnState,
    response: LLMResponse,
    tool_defs: list[Any],
    *,
    user_message: str,
    repair_guard: Any,
    repair_verify_guard: Any,
    audit: Audit,
    result: AnswerResult,
) -> AsyncGenerator[StreamEvent | _LoopTerminal, None]:
    """Recover empty responses, nudge repairs, or complete a tool-free turn."""
    content = response.content or ""
    if not content and state.tool_calls_made and state.empty_recoveries < 8:
        (
            recovery,
            recovery_messages,
            recovery_tokens,
        ) = await agent._continue_after_empty_tool_turn(
            state.session_id, user_message, state.tool_calls_made, tool_defs
        )
        state.note_empty_recovery()
        state.record_tokens(recovery_tokens)
        if recovery is not None:
            response = recovery
            content = response.content or ""
            if response.tool_calls:
                state.messages = recovery_messages
                audit(
                    "agent_empty_tool_recovery",
                    session_id=str(state.session_id),
                    tool_calls=len(response.tool_calls),
                )
    if not response.tool_calls:
        if repair_guard is not None and await repair_guard(
            session_id=state.session_id,
            user_message=user_message,
            content=content,
            messages=state.messages,
            pending_chunks=state.pending_chunks,
            nudges=state.repair_nudges,
        ):
            state.note_repair_nudge()
            state.advance_iteration()
            result.replanned = True
            return
        if repair_verify_guard is not None and await repair_verify_guard(
            session_id=state.session_id,
            user_message=user_message,
            content=content,
            messages=state.messages,
            pending_chunks=state.pending_chunks,
            tool_calls_made=state.tool_calls_made,
            nudges=state.verification_nudges,
        ):
            state.note_verification_nudge()
            state.advance_iteration()
            result.replanned = True
            return
        outcome = "completed"
        if not content:
            content, recovery_tokens = await agent._recover_empty_final(
                state.session_id, user_message, state.tool_calls_made
            )
            state.record_tokens(recovery_tokens)
        if not content:
            content = (
                "The model provider returned an empty response; "
                "no work was performed. Retry the turn."
            )
            outcome = "failed"
        completed = await agent._finish_turn_completion(
            state.session_id,
            user_message,
            content,
            state.tool_calls_made,
            state.total_tokens,
            state.iteration + 1,
            state.pending_chunks,
            outcome=outcome,
        )
        yield _LoopTerminal(completed)
        result.terminal = True
        return
    result.response = response
