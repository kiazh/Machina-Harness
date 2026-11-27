"""The ReAct turn policy, kept out of the agent class.

One module owns the loop: budgets, stop reasons, nudges, empty-response
recovery, auto-parallel planning, tool batches, terminal shapes, and
cancellation-safe flushes. ``run``/``run_stream`` adapt its events.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from rich.console import Console

if TYPE_CHECKING:
    from machina.core.agent import BaseReActAgent

from machina.core.agent_support import LoopTerminal as _LoopTerminal
from machina.core.agent_support import assistant_chunk as _assistant_chunk
from machina.core.background_tasks import joined_teardown
from machina.core.exceptions import UsageBudgetExceededError
from machina.core.metrics import metrics
from machina.core.models import AgentResponse, StreamEvent
from machina.core.provider import audit_log
from machina.core.request_gate import evaluate_request
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
)
from machina.observability.diagnostics import record_failure

logger = __import__("logging").getLogger(__name__)
console = Console()

__all__ = ["react_loop"]


async def react_loop(
    agent: BaseReActAgent,
    session_id: uuid.UUID,
    user_message: str,
    *,
    verbose: bool,
    images: list[dict[str, str]] | None,
    stream: bool,
) -> AsyncGenerator[StreamEvent | _LoopTerminal, None]:
    """Run one turn of the ReAct policy loop.

    This is the only copy of the turn policy: pending chunks, auto-parallel
    planning, repair and task-run budgets, iteration counters, nudges,
    empty-response recovery, tool execution, consolidation scheduling, and
    terminal construction. ``stream`` selects the LLM/tool transport only;
    both transports emit the same text/token/tool events and terminate with a
    :class:`_LoopTerminal`, which the ``run``/``run_stream`` adapters present.
    """
    audit_log(
        "agent_run_stream_start" if stream else "agent_run_start",
        session_id=str(session_id),
        agent_id=agent.agent_id,
    )
    # S3.1(c)+S3.2 BEGIN turn-start advisory (revert: delete block; recorded/logged only, never breaks turn)
    try:
        from machina.controller import wiring as _s3w

        _s3w.record_triage(agent, user_message)
        _s3w.log_complexity(agent, user_message)
    except Exception:
        pass
    # S3.1(c)+S3.2 END turn-start advisory
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

    def _finish_turn() -> Any:
        from machina.workspace.snapshots import end_turn

        end_turn(state.turn_snapshot)
        return agent._flush_pending_chunks(state.pending_chunks)

    async with joined_teardown(cleanup=_finish_turn):
        try:
            session, messages = await agent._prepare_context(
                session_id,
                user_message,
                include_memory=True,
                pending_chunks=state.pending_chunks,
                images=images,
            )
            state.messages = messages
            try:
                from machina.core.execution_context import resolve_execution_context
                from machina.workspace.snapshots import begin_turn

                context = await resolve_execution_context(str(session_id), "terminal")
                state.turn_snapshot = begin_turn(
                    session_id=str(session_id),
                    workspace=context.workspace,
                    prompt=user_message,
                    provider=str(getattr(session, "provider", "") or ""),
                    model=str(getattr(session, "model", "") or ""),
                )
            except Exception as snapshot_error:
                # Snapshot support is additive; a failed store must not fail
                # the turn. The file tool remains authoritative.
                state.turn_snapshot = None
                audit_log(
                    "agent_snapshot_error",
                    session_id=str(session_id),
                    error=str(snapshot_error),
                )
            # AH-015: persist the user message immediately so cancellation during
            # the first LLM call cannot lose it. Wrapped like the stream path (F3):
            # a CancelledError during this DB write must still persist the user
            # message with an interruption marker rather than dropping the batch.
            try:
                await agent._flush_pending_chunks(state.pending_chunks)
            except asyncio.CancelledError:
                await agent._flush_pending_cancellation_safe(state.pending_chunks, session_id)
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
                    session_id,
                    user_message,
                    state.pending_chunks,
                )
                if auto_response is not None:
                    # Report the synthesis once as a text event. run() ignores
                    # intermediate events and returns the terminal response.
                    if auto_response.content:
                        yield StreamEvent(type="text", content=auto_response.content)
                    yield StreamEvent(
                        type="token_usage",
                        tokens_used=auto_response.tokens_used,
                    )
                    yield _LoopTerminal(auto_response)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as _auto_error:
                record_failure(
                    "agent.auto_parallel_stream" if stream else "agent.auto_parallel",
                    _auto_error,
                )

            repair_guard = getattr(agent, "_maybe_continue_unchanged_repair", None)
            repair_verify_guard = getattr(agent, "_maybe_continue_unverified_repair", None)
            # S3.1(d) progress history lives on TurnState (state.progress_history).
            while True:
                from machina.core.shell_jobs import apply_job_updates
                from machina.core.steering import apply_steering, pending_steering

                apply_job_updates(session_id, state.messages)
                await apply_steering(session_id, agent.agent_id, state.messages)
                stop = state.check_stop()
                if stop is not None:
                    state.mark_stop(stop)
                    if stop.kind == STOP_REPAIR_DEADLINE:
                        audit_log(
                            "agent_repair_deadline",
                            session_id=str(session_id),
                            deadline_seconds=budget.deadline_seconds,
                        )
                    elif stop.kind == STOP_REPAIR_TOKEN_BUDGET:
                        audit_log(
                            "agent_repair_token_budget",
                            session_id=str(session_id),
                            token_budget=budget.token_budget,
                            total_tokens=state.total_tokens,
                        )
                    else:
                        audit_log(
                            "agent_task_run_stop",
                            session_id=str(session_id),
                            phase=state.task_run.phase,
                            turns=state.iteration,
                            tool_calls=len(state.tool_calls_made),
                            reason=str(stop.text)[:240],
                        )
                    await agent._flush_pending_chunks(state.pending_chunks)
                    agent._schedule_memory_consolidation(session_id)
                    yield _LoopTerminal(
                        stopped_response(
                            stop.text,
                            state.tool_calls_made,
                            state.total_tokens,
                            state.iteration + 1,
                        )
                    )
                    return
                if verbose:
                    console.print(f"[dim]Iteration {state.iteration + 1}[/dim]")

                # Capacity applies to this request, never cumulative billed usage.
                stale_chars = budget.stale_chars(state.total_tokens)
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
                gate = await evaluate_request(
                    state.messages,
                    session=session,
                    provider=agent.provider,
                    tool_defs=tool_defs,
                    session_id=session_id,
                    agent_id=agent.agent_id,
                    user_message=user_message,
                    total_tokens=state.total_tokens,
                    iterations=state.iteration,
                    tool_calls_made=state.tool_calls_made,
                )
                if gate.failure is not None:
                    await agent._flush_pending_chunks(state.pending_chunks)
                    agent._schedule_memory_consolidation(session_id)
                    yield _LoopTerminal(gate.failure)
                    return

                if stream:
                    streamed_text: list[str] = []
                    try:
                        final_response = None
                        async with joined_teardown(
                            stream=agent._stream_llm_with_retry(
                                messages=gate.messages,
                                tools=tool_defs,
                                session_id=session_id,
                            )
                        ) as events:
                            async for event in events:
                                if event.type == "text":
                                    streamed_text.append(event.content or "")
                                    yield StreamEvent(type="text", content=event.content)
                                elif event.type == "done":
                                    final_response = event.response
                                    usage = event.response.usage
                                    state.record_usage(usage)
                                    prompt_tokens = usage.get("prompt_tokens", 0) or 0
                                    completion_tokens = usage.get("completion_tokens", 0) or 0
                                    metrics.record_tokens(
                                        str(session_id), prompt_tokens, completion_tokens
                                    )
                                    context_tokens = int(usage.get("context_tokens", 0) or 0)
                                    if not context_tokens:
                                        context_tokens = (
                                            prompt_tokens + completion_tokens
                                            if prompt_tokens
                                            else gate.request_tokens
                                        )
                                    context_percent = usage.get("context_usage_percentage")
                                    if not isinstance(context_percent, (int, float)) or isinstance(
                                        context_percent, bool
                                    ):
                                        context_percent = (
                                            100 * context_tokens / gate.effective_budget
                                            if gate.effective_budget > 0
                                            else None
                                        )
                                    yield StreamEvent(
                                        type="token_usage",
                                        tokens_used=usage.get("total_tokens", 0),
                                        context_tokens=context_tokens,
                                        context_usage_percentage=context_percent,
                                        credits_used=(
                                            float(event.response.usage["credits_used"])
                                            if isinstance(
                                                event.response.usage.get("credits_used"),
                                                (int, float),
                                            )
                                            and not isinstance(
                                                event.response.usage.get("credits_used"), bool
                                            )
                                            else None
                                        ),
                                    )

                        if final_response is None:
                            raise RuntimeError("Stream completed without a final response")

                    except asyncio.CancelledError:
                        if streamed_text:
                            content = "".join(streamed_text)
                            state.pending_chunks.append(
                                _assistant_chunk(
                                    session_id, agent.agent_id, content, interrupted=True
                                )
                            )
                        await agent._flush_pending_cancellation_safe(
                            state.pending_chunks, session_id
                        )
                        raise
                    except UsageBudgetExceededError as e:
                        await agent._flush_pending_chunks(state.pending_chunks)
                        audit_log(
                            "agent_run_budget_exceeded", session_id=str(session_id), error=str(e)
                        )
                        yield _LoopTerminal(
                            AgentResponse(
                                content=str(e),
                                outcome="failed",
                                tool_calls=state.tool_calls_made,
                                tokens_used=state.total_tokens,
                                iterations=state.iteration,
                            )
                        )
                        return
                    except Exception as e:
                        response = await provider_failure_response(
                            agent,
                            e,
                            session_id=session_id,
                            pending_chunks=state.pending_chunks,
                            tool_calls=state.tool_calls_made,
                            tokens=state.total_tokens,
                            iterations=state.iteration,
                            audit=audit_log,
                            label="LLM stream ultimately failed",
                        )
                        yield _LoopTerminal(response)
                        return

                    response = final_response
                else:
                    try:
                        response = await agent._call_llm_with_retry(
                            messages=gate.messages,
                            tools=tool_defs,
                            session_id=session_id,
                        )
                    except UsageBudgetExceededError as e:
                        await agent._flush_pending_chunks(state.pending_chunks)
                        audit_log(
                            "agent_run_budget_exceeded", session_id=str(session_id), error=str(e)
                        )
                        yield _LoopTerminal(
                            AgentResponse(
                                content=str(e),
                                outcome="failed",
                                tool_calls=state.tool_calls_made,
                                tokens_used=state.total_tokens,
                                iterations=state.iteration,
                            )
                        )
                        return
                    except Exception as e:
                        response = await provider_failure_response(
                            agent,
                            e,
                            session_id=session_id,
                            pending_chunks=state.pending_chunks,
                            tool_calls=state.tool_calls_made,
                            tokens=state.total_tokens,
                            iterations=state.iteration,
                            audit=audit_log,
                            label="LLM call ultimately failed",
                        )
                        yield _LoopTerminal(response)
                        return

                    state.record_usage(response.usage)
                    metrics.record_tokens(
                        str(session_id),
                        response.usage.get("prompt_tokens", 0),
                        response.usage.get("completion_tokens", 0),
                    )

                if pending_steering(session_id) and response.content:
                    await agent._append_assistant_content(
                        session_id, response.content, state.pending_chunks
                    )
                    await agent._flush_pending_chunks(state.pending_chunks)
                if await apply_steering(session_id, agent.agent_id, state.messages):
                    state.advance_iteration()
                    continue
                if not response.tool_calls:
                    content = response.content or ""
                    if not content and state.tool_calls_made and state.empty_recoveries < 8:
                        (
                            recovery,
                            recovery_messages,
                            recovery_tokens,
                        ) = await agent._continue_after_empty_tool_turn(
                            session_id, user_message, state.tool_calls_made, tool_defs
                        )
                        state.note_empty_recovery()
                        state.record_tokens(recovery_tokens)
                        if recovery is not None:
                            response = recovery
                            content = response.content or ""
                            if response.tool_calls:
                                state.messages = recovery_messages
                                audit_log(
                                    "agent_empty_tool_recovery",
                                    session_id=str(session_id),
                                    tool_calls=len(response.tool_calls),
                                )
                    if not response.tool_calls:
                        if repair_guard is not None and await repair_guard(
                            session_id=session_id,
                            user_message=user_message,
                            content=content,
                            messages=state.messages,
                            pending_chunks=state.pending_chunks,
                            nudges=state.repair_nudges,
                        ):
                            state.note_repair_nudge()
                            state.advance_iteration()
                            continue
                        if repair_verify_guard is not None and await repair_verify_guard(
                            session_id=session_id,
                            user_message=user_message,
                            content=content,
                            messages=state.messages,
                            pending_chunks=state.pending_chunks,
                            tool_calls_made=state.tool_calls_made,
                            nudges=state.verification_nudges,
                        ):
                            state.note_verification_nudge()
                            state.advance_iteration()
                            continue
                        outcome = "completed"
                        if not content:
                            content, recovery_tokens = await agent._recover_empty_final(
                                session_id, user_message, state.tool_calls_made
                            )
                            state.record_tokens(recovery_tokens)
                        if not content:
                            content = (
                                "The model provider returned an empty response; "
                                "no work was performed. Retry the turn."
                            )
                            outcome = "failed"
                        completed = await agent._finish_turn_completion(
                            session_id,
                            user_message,
                            content,
                            state.tool_calls_made,
                            state.total_tokens,
                            state.iteration + 1,
                            state.pending_chunks,
                            outcome=outcome,
                        )
                        yield _LoopTerminal(completed)
                        return

                await agent._append_assistant_content(
                    session_id, response.content, state.pending_chunks
                )
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
                                session_id,
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
                            session_id,
                            verbose,
                            state.pending_chunks,
                            allowed_tool_names=advertised_tool_names,
                        )
                    await agent._flush_pending_chunks(state.pending_chunks)
                except BaseException:
                    await agent._flush_pending_cancellation_safe(state.pending_chunks, session_id)
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
                    return
                # S3.1(d stagnation hook: shared batch tail (turn_flow owns the
                # guarded wiring access; stagnation-only, never a cap).
                state.progress_history, _s3_text = await stagnation_step(
                    state.progress_history,
                    batch,
                    state.tool_calls_made,
                    session_id=session_id,
                    session_manager=session_manager,
                    audit=audit_log,
                    flush=lambda: agent._flush_pending_chunks(state.pending_chunks),
                )
                if _s3_text is not None:
                    agent._schedule_memory_consolidation(session_id)
                    yield _LoopTerminal(
                        stopped_response(
                            _s3_text,
                            state.tool_calls_made,
                            state.total_tokens,
                            state.iteration + 1,
                        )
                    )
                    return
                state.task_run.observe(batch)
                task_nudge = state.task_run.take_nudge()
                if task_nudge is not None:
                    await agent._append_task_run_nudge(
                        session_id=session_id,
                        text=task_nudge,
                        messages=state.messages,
                        pending_chunks=state.pending_chunks,
                    )
                    state.advance_iteration()
                    continue
                state.advance_iteration()
        except BaseException:
            if state.pending_chunks:
                await agent._flush_pending_cancellation_safe(state.pending_chunks, session_id)
            raise
