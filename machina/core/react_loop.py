"""The ReAct turn policy, kept out of the agent class.

One module owns the loop: budgets, stop reasons, nudges, empty-response
recovery, auto-parallel planning, tool batches, terminal shapes, and
cancellation-safe flushes. ``run``/``run_stream`` adapt its events.
"""

from __future__ import annotations

import asyncio
import time
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
from machina.core.repair_gate import (
    REPAIR_DEADLINE_TEXT,
    repair_deadline_exceeded,
    repair_deadline_seconds,
)
from machina.core.request_gate import evaluate_request
from machina.core.session import session_manager
from machina.core.task_run import TaskRunPolicy
from machina.core.token_budget import (
    REPAIR_TOKEN_BUDGET_TEXT,
    repair_token_budget,
    repair_token_budget_exceeded,
    stale_tool_result_chars,
)
from machina.core.turn_flow import (
    collect_pauses,
    pause_response,
    provider_failure_response,
    stagnation_step,
    stopped_response,
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
    pending_chunks: list[dict[str, Any]] = []
    repair_started = time.monotonic()
    repair_deadline = repair_deadline_seconds()
    token_budget = repair_token_budget()
    task_run = TaskRunPolicy(user_message)
    from machina.core.task_run import set_repair_network_blocked

    set_repair_network_blocked(task_run.active and task_run.lean_tools)
    turn_snapshot = None

    def _finish_turn() -> Any:
        from machina.workspace.snapshots import end_turn

        end_turn(turn_snapshot)
        return agent._flush_pending_chunks(pending_chunks)

    async with joined_teardown(cleanup=_finish_turn):
        try:
            session, messages = await agent._prepare_context(
                session_id,
                user_message,
                include_memory=True,
                pending_chunks=pending_chunks,
                images=images,
            )
            try:
                from machina.core.execution_context import resolve_execution_context
                from machina.workspace.snapshots import begin_turn

                context = await resolve_execution_context(str(session_id), "terminal")
                turn_snapshot = begin_turn(
                    session_id=str(session_id),
                    workspace=context.workspace,
                    prompt=user_message,
                    provider=str(getattr(session, "provider", "") or ""),
                    model=str(getattr(session, "model", "") or ""),
                )
            except Exception as snapshot_error:
                # Snapshot support is additive; a failed store must not fail
                # the turn. The file tool remains authoritative.
                turn_snapshot = None
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
                await agent._flush_pending_chunks(pending_chunks)
            except asyncio.CancelledError:
                await agent._flush_pending_cancellation_safe(pending_chunks, session_id)
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
                    pending_chunks,
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

            total_tokens = 0
            tool_calls_made: list[dict[str, Any]] = []

            iteration = 0
            repair_nudges = 0
            verification_nudges = 0
            empty_recoveries = 0
            repair_guard = getattr(agent, "_maybe_continue_unchanged_repair", None)
            repair_verify_guard = getattr(agent, "_maybe_continue_unverified_repair", None)
            # S3.1(d) BEGIN progress history (revert: delete 2 lines; unbounded loop unchanged)
            _s3_progress_history: list = []
            # S3.1(d) END progress history
            while True:
                from machina.core.shell_jobs import apply_job_updates
                from machina.core.steering import apply_steering, pending_steering

                apply_job_updates(session_id, messages)
                await apply_steering(session_id, agent.agent_id, messages)
                if repair_deadline > 0 and repair_deadline_exceeded(repair_started, user_message):
                    audit_log(
                        "agent_repair_deadline",
                        session_id=str(session_id),
                        deadline_seconds=repair_deadline,
                    )
                    await agent._flush_pending_chunks(pending_chunks)
                    agent._schedule_memory_consolidation(session_id)
                    yield _LoopTerminal(
                        stopped_response(
                            REPAIR_DEADLINE_TEXT,
                            tool_calls_made,
                            total_tokens,
                            iteration + 1,
                        )
                    )
                    return
                if token_budget > 0 and repair_token_budget_exceeded(
                    total_tokens,
                    user_message,
                    budget=token_budget,
                ):
                    audit_log(
                        "agent_repair_token_budget",
                        session_id=str(session_id),
                        token_budget=token_budget,
                        total_tokens=total_tokens,
                    )
                    await agent._flush_pending_chunks(pending_chunks)
                    agent._schedule_memory_consolidation(session_id)
                    yield _LoopTerminal(
                        stopped_response(
                            REPAIR_TOKEN_BUDGET_TEXT,
                            tool_calls_made,
                            total_tokens,
                            iteration + 1,
                        )
                    )
                    return
                task_stop = task_run.stop_reason(
                    turns=iteration,
                    tool_calls=len(tool_calls_made),
                )
                if task_stop is not None:
                    audit_log(
                        "agent_task_run_stop",
                        session_id=str(session_id),
                        phase=task_run.phase,
                        turns=iteration,
                        tool_calls=len(tool_calls_made),
                        reason=str(task_stop)[:240],
                    )
                    await agent._flush_pending_chunks(pending_chunks)
                    agent._schedule_memory_consolidation(session_id)
                    yield _LoopTerminal(
                        stopped_response(
                            task_stop,
                            tool_calls_made,
                            total_tokens,
                            iteration + 1,
                        )
                    )
                    return
                if verbose:
                    console.print(f"[dim]Iteration {iteration + 1}[/dim]")

                # Capacity applies to this request, never cumulative billed usage.
                stale_chars = stale_tool_result_chars(
                    total_tokens,
                    user_message,
                    budget=token_budget,
                )
                if stale_chars is None:
                    agent._condense_history(messages)
                else:
                    agent._condense_history(messages, stale_chars=stale_chars)
                # Once a repair has both an edit and passing verification
                # evidence, force the answer step: exposing tools again lets a
                # model keep rerunning tests or inspecting the diff, which
                # costs a full provider round trip without adding evidence.
                tool_defs = (
                    []
                    if task_run.active and task_run.edited and task_run.verified
                    else task_run.filter_tool_defs(agent._tool_defs())
                )
                # Keep the dynamic loader inside the lean repair surface too.
                # Otherwise tool_load can add web/network tools after the schema
                # filter has already run, and the stale schema can still be
                # executed from the model's context.
                from machina.core.tool_projection import set_dynamic_allowed_tools

                if task_run.active and task_run.lean_tools:
                    set_dynamic_allowed_tools(sorted(task_run.allowed_tool_names()))
                else:
                    set_dynamic_allowed_tools(None)
                gate = await evaluate_request(
                    messages,
                    session=session,
                    provider=agent.provider,
                    tool_defs=tool_defs,
                    session_id=session_id,
                    agent_id=agent.agent_id,
                    user_message=user_message,
                    total_tokens=total_tokens,
                    iterations=iteration,
                    tool_calls_made=tool_calls_made,
                )
                if gate.failure is not None:
                    await agent._flush_pending_chunks(pending_chunks)
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
                                    total_tokens += usage.get("total_tokens", 0)
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
                            pending_chunks.append(
                                _assistant_chunk(
                                    session_id, agent.agent_id, content, interrupted=True
                                )
                            )
                        await agent._flush_pending_cancellation_safe(pending_chunks, session_id)
                        raise
                    except UsageBudgetExceededError as e:
                        await agent._flush_pending_chunks(pending_chunks)
                        audit_log(
                            "agent_run_budget_exceeded", session_id=str(session_id), error=str(e)
                        )
                        yield _LoopTerminal(
                            AgentResponse(
                                content=str(e),
                                outcome="failed",
                                tool_calls=tool_calls_made,
                                tokens_used=total_tokens,
                                iterations=iteration,
                            )
                        )
                        return
                    except Exception as e:
                        response = await provider_failure_response(
                            agent,
                            e,
                            session_id=session_id,
                            pending_chunks=pending_chunks,
                            tool_calls=tool_calls_made,
                            tokens=total_tokens,
                            iterations=iteration,
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
                        await agent._flush_pending_chunks(pending_chunks)
                        audit_log(
                            "agent_run_budget_exceeded", session_id=str(session_id), error=str(e)
                        )
                        yield _LoopTerminal(
                            AgentResponse(
                                content=str(e),
                                outcome="failed",
                                tool_calls=tool_calls_made,
                                tokens_used=total_tokens,
                                iterations=iteration,
                            )
                        )
                        return
                    except Exception as e:
                        response = await provider_failure_response(
                            agent,
                            e,
                            session_id=session_id,
                            pending_chunks=pending_chunks,
                            tool_calls=tool_calls_made,
                            tokens=total_tokens,
                            iterations=iteration,
                            audit=audit_log,
                            label="LLM call ultimately failed",
                        )
                        yield _LoopTerminal(response)
                        return

                    total_tokens += response.usage.get("total_tokens", 0)
                    metrics.record_tokens(
                        str(session_id),
                        response.usage.get("prompt_tokens", 0),
                        response.usage.get("completion_tokens", 0),
                    )

                if pending_steering(session_id) and response.content:
                    await agent._append_assistant_content(
                        session_id, response.content, pending_chunks
                    )
                    await agent._flush_pending_chunks(pending_chunks)
                if await apply_steering(session_id, agent.agent_id, messages):
                    iteration += 1
                    continue
                if not response.tool_calls:
                    content = response.content or ""
                    if not content and tool_calls_made and empty_recoveries < 8:
                        (
                            recovery,
                            recovery_messages,
                            recovery_tokens,
                        ) = await agent._continue_after_empty_tool_turn(
                            session_id, user_message, tool_calls_made, tool_defs
                        )
                        empty_recoveries += 1
                        total_tokens += recovery_tokens
                        if recovery is not None:
                            response = recovery
                            content = response.content or ""
                            if response.tool_calls:
                                messages = recovery_messages
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
                            messages=messages,
                            pending_chunks=pending_chunks,
                            nudges=repair_nudges,
                        ):
                            repair_nudges += 1
                            iteration += 1
                            continue
                        if repair_verify_guard is not None and await repair_verify_guard(
                            session_id=session_id,
                            user_message=user_message,
                            content=content,
                            messages=messages,
                            pending_chunks=pending_chunks,
                            tool_calls_made=tool_calls_made,
                            nudges=verification_nudges,
                        ):
                            verification_nudges += 1
                            iteration += 1
                            continue
                        outcome = "completed"
                        if not content:
                            content, recovery_tokens = await agent._recover_empty_final(
                                session_id, user_message, tool_calls_made
                            )
                            total_tokens += recovery_tokens
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
                            tool_calls_made,
                            total_tokens,
                            iteration + 1,
                            pending_chunks,
                            outcome=outcome,
                        )
                        yield _LoopTerminal(completed)
                        return

                await agent._append_assistant_content(session_id, response.content, pending_chunks)
                batch_start = len(tool_calls_made)
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
                                messages,
                                tool_calls_made,
                                session_id,
                                pending_chunks,
                                allowed_tool_names=advertised_tool_names,
                            )
                        ) as events:
                            async for event in events:
                                yield event
                    else:
                        await agent._execute_tool_calls(
                            response,
                            messages,
                            tool_calls_made,
                            session_id,
                            verbose,
                            pending_chunks,
                            allowed_tool_names=advertised_tool_names,
                        )
                    await agent._flush_pending_chunks(pending_chunks)
                except BaseException:
                    await agent._flush_pending_cancellation_safe(pending_chunks, session_id)
                    raise
                batch = tool_calls_made[batch_start:]
                total_tokens += sum(entry.get("delegated_tokens", 0) for entry in batch)
                pauses = collect_pauses(batch)
                if pauses:
                    yield _LoopTerminal(
                        pause_response(tool_calls_made, total_tokens, iteration + 1, pauses)
                    )
                    return
                # S3.1(d stagnation hook: shared batch tail (turn_flow owns the
                # guarded wiring access; stagnation-only, never a cap).
                _s3_progress_history, _s3_text = await stagnation_step(
                    _s3_progress_history,
                    batch,
                    tool_calls_made,
                    session_id=session_id,
                    session_manager=session_manager,
                    audit=audit_log,
                    flush=lambda: agent._flush_pending_chunks(pending_chunks),
                )
                if _s3_text is not None:
                    agent._schedule_memory_consolidation(session_id)
                    yield _LoopTerminal(
                        stopped_response(_s3_text, tool_calls_made, total_tokens, iteration + 1)
                    )
                    return
                task_run.observe(batch)
                task_nudge = task_run.take_nudge()
                if task_nudge is not None:
                    await agent._append_task_run_nudge(
                        session_id=session_id,
                        text=task_nudge,
                        messages=messages,
                        pending_chunks=pending_chunks,
                    )
                    iteration += 1
                    continue
                iteration += 1
        except BaseException:
            if pending_chunks:
                await agent._flush_pending_cancellation_safe(pending_chunks, session_id)
            raise
