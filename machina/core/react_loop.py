"""The ReAct turn policy: a phase orchestrator.

The turn policy itself lives in :mod:`machina.core.react_phases`. This module
keeps the single ``while`` loop, runs the phases in their original order, and
forwards their events unchanged. Budgets, stop reasons, nudges,
empty-response recovery, auto-parallel planning, tool batches, terminal
shapes, and cancellation-safe flushes are owned by the phases and the shared
:class:`TurnState`.

Invariants
----------
- The phase order and the yielded event order are identical to the previous
  single-function loop.
- Phase generators are closed synchronously (``aclosing``) so stream teardown,
  cleanup, and cancellation flushes keep their original timing.
- ``evaluate_request``, ``audit_log``, and ``metrics`` stay module globals
  here and are injected into the phases, so the existing test seams that
  monkeypatch this module keep working.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from machina.core.agent import BaseReActAgent

from machina.core.agent_support import LoopTerminal as _LoopTerminal
from machina.core.background_tasks import joined_teardown
from machina.core.metrics import metrics
from machina.core.models import StreamEvent
from machina.core.provider import audit_log
from machina.core.react_phases import (
    AnswerResult,
    ModelPlan,
    ModelResult,
    ToolResult,
    finish_answer,
    finish_turn,
    new_turn_state,
    prepare_model_step,
    prepare_turn,
    record_turn_advisory,
    run_model_step,
    run_tool_step,
)
from machina.core.request_gate import evaluate_request

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

    ``stream`` selects the LLM/tool transport only; both transports emit the
    same text/token/tool events and terminate with a :class:`_LoopTerminal`,
    which the ``run``/``run_stream`` adapters present.
    """
    audit_log(
        "agent_run_stream_start" if stream else "agent_run_start",
        session_id=str(session_id),
        agent_id=agent.agent_id,
    )
    record_turn_advisory(agent, user_message)
    state = new_turn_state(session_id, user_message)

    async with joined_teardown(cleanup=lambda: finish_turn(agent, state)):
        try:
            session, auto_response = await prepare_turn(
                agent,
                state,
                user_message=user_message,
                images=images,
                stream=stream,
                audit=audit_log,
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

            repair_guard = getattr(agent, "_maybe_continue_unchanged_repair", None)
            repair_verify_guard = getattr(agent, "_maybe_continue_unverified_repair", None)
            # S3.1(d) progress history lives on TurnState (state.progress_history).
            while True:
                plan = ModelPlan()
                async with aclosing(
                    prepare_model_step(
                        agent,
                        state,
                        session,
                        user_message=user_message,
                        verbose=verbose,
                        gate_fn=evaluate_request,
                        audit=audit_log,
                        result=plan,
                    )
                ) as events:
                    async for event in events:
                        yield event
                if plan.terminal:
                    return

                model = ModelResult()
                async with aclosing(
                    run_model_step(
                        agent,
                        state,
                        plan,
                        stream=stream,
                        metrics=metrics,
                        audit=audit_log,
                        result=model,
                    )
                ) as events:
                    async for event in events:
                        yield event
                if model.terminal:
                    return
                if model.replanned:
                    continue

                response = model.response
                if response is None:
                    raise RuntimeError("model step ended without a response")
                if not response.tool_calls:
                    answer = AnswerResult(response=response)
                    async with aclosing(
                        finish_answer(
                            agent,
                            state,
                            response,
                            plan.tool_defs,
                            user_message=user_message,
                            repair_guard=repair_guard,
                            repair_verify_guard=repair_verify_guard,
                            audit=audit_log,
                            result=answer,
                        )
                    ) as events:
                        async for event in events:
                            yield event
                    if answer.terminal:
                        return
                    if answer.replanned:
                        continue
                    response = answer.response
                    if response is None:
                        raise RuntimeError("answer step ended without a response")

                tool = ToolResult()
                async with aclosing(
                    run_tool_step(
                        agent,
                        state,
                        response,
                        plan.tool_defs,
                        stream=stream,
                        verbose=verbose,
                        audit=audit_log,
                        result=tool,
                    )
                ) as events:
                    async for event in events:
                        yield event
                if tool.terminal:
                    return

        except BaseException:
            if state.pending_chunks:
                await agent._flush_pending_cancellation_safe(state.pending_chunks, session_id)
            raise
