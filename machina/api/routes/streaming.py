"""SSE streaming for session turns (moved verbatim from app.py).

Shared by the sessions router (prompt/chat) and the approvals router
(approval resume): both stream agent events as Server-Sent Events through
:func:`_stream_session`.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import sys
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from machina.api.routes.models import PromptRequest
from machina.api.routes.shared import _redact_value, _serialize_event
from machina.core.config import config
from machina.core.exceptions import StaleOwnershipError
from machina.core.execution_context import begin_turn_scope, end_turn_scope
from machina.core.models import StreamEvent
from machina.core.turn_lifecycle import ApprovalPolicy, TurnLifecycle, TurnStepSupervisor
from machina.observability.diagnostics import record_failure

logger = logging.getLogger(__name__)

# L1b (H01 residual): bound on how long the SSE retire path waits for the
# cancelled step task to settle before quarantining it (same contract as the
# engine's H01 source close). The engine for this stream is constructed with
# the same bound, so a cancellation-resistant step delays ``close_turn`` by at
# most ~2x this value (retire join, then the engine source close on the same
# task) plus the fixed 0.1 s settle window — instead of unbounded. Read at
# call time so tests can shorten it.
RETIRE_JOIN_TIMEOUT_SECONDS = 5.0


async def _stream_session(
    session_id: str, req: PromptRequest, *, resume: tuple[dict, dict] | None = None
) -> StreamingResponse:
    """Stream agent events as Server-Sent Events."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(400, "invalid session ID") from None
    # AH-AUDIT-019: execution-critical read bypasses the display cache.
    session = await TurnLifecycle.load_session(sid)
    if session is None:
        raise HTTPException(404, "session not found")
    # Shared coordinator: owner-token claim BEFORE SSE headers so a
    # conflicting turn gets a clean 409 instead of an interleaved stream.
    # Fails closed: claim errors propagate, never concurrent execution.
    # R4-084: the wire carries the canonical 12-hex turn id (the same shape
    # the stdio gateway generates, ``uuid4().hex[:12]``) so transports can
    # correlate one turn; the internal owner token ("turn:<id>:<nonce>")
    # never leaves this module.
    turn_id = uuid.uuid4().hex[:12]
    turn_token = await TurnLifecycle.acquire_claim(sid, turn_id=turn_id)
    if not turn_token:
        raise HTTPException(409, "a turn is already running for this session")

    # AH-AUDIT-013: managed renewal while the stream owns the turn
    # (compute only here — headless SSE never waits on humans, so no
    # approval accounting is needed on this path). Created lazily on
    # first iteration so an unconsumed stream never renews forever;
    # stopped when the generator exits; crash expiry recovery preserved.
    # The renewal interval is owned by machina.api.app (kept there as a
    # patch target). Read it off the loaded app module instead of
    # importing it: a function-level import would add a deferred-import
    # census entry and a module-level one would cycle back into app.
    # Resolved per stream so monkeypatching the app attribute keeps
    # working after the router split.
    _app_module = sys.modules.get("machina.api.app")
    _renew_interval = getattr(_app_module, "CLAIM_RENEW_INTERVAL_SECONDS", 30.0)

    # Shared per-step supervisor: owns the F07 step Context, the active task
    # handoff used by quarantine cleanup, and the ownership-loss/deadline race.
    supervisor = TurnStepSupervisor()

    async def approval_data(request_id: str) -> dict[str, Any]:
        """Return a redacted approval event, raising if the stored proposal is unavailable."""
        from machina.permissions.store import get_approval
        from machina.protocol import approval_fields

        record = await get_approval(request_id)
        if record is None:
            raise RuntimeError("approval proposal is unavailable")
        return {"type": "needs_approval", **_redact_value(approval_fields(record))}

    def frame(data: dict[str, Any]) -> str:
        if req.protocolVersion == 2:
            from machina.protocol import canonical_http_event

            data = canonical_http_event(data, session_id, turn_id)
        if req.eventFormat == "cloudevents":
            from machina.protocol.cloudevents import wrap_event

            data = wrap_event(data, source=f"/machina/sessions/{session_id}")
        return f"data: {json.dumps(data)}\n\n"

    async def event_stream() -> AsyncGenerator[str, None]:
        terminal_data = None
        resume_token = None
        _stream_cancelled = False
        _release_failed = False
        _usage_emitted = 0
        _emitted_approvals: set[str] = set()
        _stage = "turn_started"

        def _record_stage(stage: str) -> None:
            nonlocal _stage
            _stage = stage
            logger.debug("Session %s stage: %s", session_id, stage)

        class _TurnHooks:
            def on_stage(_self, stage: str) -> None:
                _record_stage(stage)

            def on_ownership_lost(_self, reason: str) -> None:
                supervisor.cancel_active()

        # W2: the shared lifecycle owns claim/session/agent/approval/renewal
        # and the bounded cleanup tail; the engine owns boundary-safe streaming
        # redaction (the withheld tail flushes exactly once before the
        # terminal), tool pairing (stable id, FIFO fallback, 4000-char
        # truncation), token accounting and final capture. This generator only
        # serializes semantic events.
        # Renewal starts here — on first iteration, not at response
        # construction — so an unconsumed stream never renews forever; crash
        # expiry recovery is preserved.
        lifecycle = TurnLifecycle(
            session_id=sid,
            turn_id=turn_id,
            token=turn_token,
            text=req.text,
            verbose=req.verbose,
            resume=resume,
            boundary="app.create_app",
            renew_interval_s=_renew_interval,
            source_close_timeout_s=RETIRE_JOIN_TIMEOUT_SECONDS,
            approval=ApprovalPolicy(mode="headless", principal="http"),
            hooks=_TurnHooks(),
        )

        async def close_turn(*, retirement_task: asyncio.Task | None = None):
            """Close the turn; a quarantined step defers release until it ends.

            The engine's source close owns the driver barrier: when the active
            step is still running, cleanup returns a pending outcome and the
            claim/provider stay owned until retirement.
            """
            from machina.core.background_tasks import await_joined

            async def finish():
                return await lifecycle.close(
                    cancelled=_stream_cancelled,
                    retirement_task=retirement_task,
                )

            return await await_joined(finish())

        lifecycle.start(ownership_lost=supervisor.ownership_lost)
        # R4-010: driver-owned turn scope — every `_owned_step` task runs in a
        # context that carries this scope (the stream steps share the armed
        # context below; the agent-build step copies this one), so the scope is
        # visible to all steps and the reset stays in the context that created
        # the token.
        #
        # R4-064 follow-up (stopped here, deliberately): no OTel span around
        # this drive loop. An async generator can be resumed by one task and
        # finalized by another (per-step `_owned_step` tasks here; Starlette
        # closing the response on disconnect), and opentelemetry's context
        # detach fails + logs in the foreign context. Unlike the turn-mode
        # reset there is no tolerant detach available for spans, so this
        # driver owns no span; gateway/CLI (single-task drivers) carry the
        # stream span. Evidence: proofs/codebase_review_round4_2026-10-14/
        # R4-063_064_observability.md.
        turn_scope_token = begin_turn_scope()
        try:
            yield frame({"type": "turn.started", "sessionId": session_id, "turnId": turn_id})
            # AH-022: definition-aware factory so specialist sessions
            # cannot bypass allowed_tools/persona via direct HTTP.
            from machina.core.agent_factory import build_agent_for_session

            _record_stage("building_agent")

            async def build_owned_agent():
                # Adoption belongs to the build task: even a build that resists
                # cancellation must hand its resources to deferred engine close.
                built = await build_agent_for_session(session)
                lifecycle.engine.attach(built)
                return built

            await lifecycle.prepare(
                session,
                builder=lambda _session: supervisor.run(build_owned_agent(), 300),
            )
            _record_stage("waiting_for_provider")
            # An explicit positive turn_timeout remains available; zero lets
            # per-operation guards and ownership checks govern long work.
            try:
                timeout = float(config.get("turn_timeout"))
            except (TypeError, ValueError):
                timeout = 300.0
            deadline = asyncio.get_running_loop().time() + timeout
            if resume is not None:
                # Transport-owned resume contextvar (the engine does not own
                # it): set in THIS context so every supervised step task copies
                # the value into the step that executes the reviewed action.
                from machina.permissions.broker import resume_approval_var

                resume_token = resume_approval_var.set(str(resume[0].get("request_id", "")))
            # F07: arm the shared step Context after the transport-owned resume
            # ContextVar is set, so every agent step (and the done handler) sees
            # it together with the growth/RSI state the agent writes while
            # preparing. Copied from THIS driver context, which already owns the
            # turn scope entered above.
            supervisor.arm(contextvars.copy_context())
            stream = lifecycle.stream()
            while True:
                remaining = deadline - asyncio.get_running_loop().time() if timeout > 0 else None
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("turn timed out")
                try:
                    turn_event = await supervisor.run(stream.__anext__(), remaining)
                except StopAsyncIteration:
                    break
                if turn_event.kind == "delta":
                    # The engine emitted only the safe redaction prefix.
                    data = _serialize_event(
                        StreamEvent(type="text", content=turn_event.payload["text"]),
                        _pre_redacted=True,
                    )
                    yield frame(data)
                elif turn_event.kind == "approval_resumed":
                    yield frame(
                        {
                            "type": "approval.resumed",
                            "requestId": turn_event.payload["request_id"],
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "originTurnId": turn_event.payload["origin_turn_id"],
                        }
                    )
                elif turn_event.kind == "tool_start":
                    yield frame(
                        _serialize_event(
                            StreamEvent(
                                type="tool_call",
                                tool_name=turn_event.payload["name"],
                                tool_args=turn_event.payload["args"],
                                tool_call_id=turn_event.payload["id"],
                            )
                        )
                    )
                elif turn_event.kind == "tool_complete":
                    payload = turn_event.payload
                    yield frame(
                        _serialize_event(
                            StreamEvent(
                                type="tool_result",
                                tool_name=payload["name"],
                                tool_result=payload["result"],
                                tool_outcome=payload.get("outcome", ""),
                                tool_call_id=payload["id"],
                            ),
                            # P10: carry the engine's real failure flag on the
                            # canonical wire; the v1 compatibility shape has no
                            # isError field, so only v2 requests forward it.
                            tool_is_error=(
                                payload.get("isError", False) if req.protocolVersion == 2 else None
                            ),
                        )
                    )
                elif turn_event.kind == "needs_approval":
                    # Stream pause marker (AH-AUDIT-030): typed and
                    # actionable, never a generic error. Continuation frames
                    # from the done response are emitted after the loop.
                    data = _serialize_event(
                        StreamEvent(
                            type="needs_approval",
                            content=turn_event.payload["request_id"],
                            tool_name=turn_event.payload["operation"],
                            tool_args=turn_event.payload["proposal"],
                        )
                    )
                    if req.protocolVersion == 2:
                        data = await approval_data(str(turn_event.payload["request_id"]))
                    _emitted_approvals.add(str(turn_event.payload["request_id"]))
                    yield frame(data)
                elif turn_event.kind == "usage":
                    # Transport shape preserved: SSE usage frames carry the
                    # per-call count while the engine reports the running
                    # total (the wire event keeps its historical semantics).
                    cumulative = turn_event.payload["tokens"]
                    per_call = cumulative - _usage_emitted
                    _usage_emitted = cumulative
                    yield frame(
                        _serialize_event(
                            StreamEvent(
                                type="token_usage",
                                tokens_used=per_call,
                                context_tokens=turn_event.payload.get("contextTokens") or 0,
                                context_usage_percentage=turn_event.payload.get("contextPercent"),
                                credits_used=turn_event.payload.get("creditsUsed"),
                            )
                        )
                    )
                elif turn_event.kind == "done":
                    if lifecycle.final is None:
                        # Defensive parity: a done without a response still
                        # terminates the stream with the bare done frame.
                        terminal_data = {"type": "done"}
                        continue
                    # AH-AUDIT-027: the withheld tail is flushed exactly once
                    # as a delta before the terminal; the done payload carries
                    # the canonical fully-redacted concatenation, never a raw
                    # tail.
                    tail = lifecycle.flush_tail()
                    if tail:
                        data = _serialize_event(
                            StreamEvent(type="text", content=tail),
                            _pre_redacted=True,
                        )
                        yield frame(data)
                    # AH-AUDIT-030: typed needs_approval SSE output with
                    # request/session/turn binding and the sanitized proposal.
                    # A pause is actionable and distinct from failure; resume
                    # revalidates under a fresh fenced claim (never by
                    # continuing stale generator state).
                    for pause in lifecycle.pauses:
                        pause_id = str(pause.get("requestId", ""))
                        if pause_id and pause_id in _emitted_approvals:
                            # The live turn event already emitted this card;
                            # emitting it again from the done summary produced
                            # duplicate needs_approval frames (combined #29).
                            continue
                        pause_event = StreamEvent(
                            type="needs_approval",
                            content=str(pause.get("requestId", "")),
                            tool_name=str(pause.get("operation", "")),
                            tool_args={
                                "requestId": str(pause.get("requestId", "")),
                                "sessionId": str(pause.get("sessionId", "") or session_id),
                                "operation": str(pause.get("operation", "")),
                                "target": str(pause.get("target", "")),
                                "status": str(pause.get("status", "pending")),
                            },
                        )
                        data = _serialize_event(pause_event)
                        if req.protocolVersion == 2:
                            data = await approval_data(str(pause["requestId"]))
                        yield frame(data)
                    terminal_payload = lifecycle.build_terminal(cancelled=False)
                    terminal_data = {
                        "type": "done",
                        "content": terminal_payload["text"],
                        "tokens": terminal_payload["tokens"],
                        "iterations": terminal_payload["iterations"],
                        "toolCalls": terminal_payload["toolCalls"],
                    }
                    if "needsApproval" in terminal_payload:
                        # The terminal summary is a transport artifact too:
                        # approval targets can contain URLs, commands, or
                        # paths with credentials and must use the same
                        # redaction as live needs_approval frames.
                        terminal_data["needsApproval"] = _redact_value(
                            terminal_payload["needsApproval"]
                        )
                else:
                    logger.warning("Unknown turn event kind: %s", turn_event.kind)
        except TimeoutError:
            logger.warning("Prompt stream timed out for session %s", session_id)
            error_data = {"type": "error", "message": "turn timed out"}
            yield frame(error_data)
        except StaleOwnershipError:
            yield frame({"type": "turn.ownership_lost", "sessionId": session_id, "turnId": turn_id})
        except Exception:
            logger.exception("Prompt stream failed for session %s", session_id)
            error_data = {"type": "error", "message": "prompt failed; check server logs"}
            yield frame(error_data)
        except GeneratorExit:
            _stream_cancelled = True
            raise
        except asyncio.CancelledError:
            # Client disconnect / server shutdown: bounded cleanup below,
            # then propagate (never yield DONE here — the consumer is gone
            # and swallowing cancellation would strand the task).
            _stream_cancelled = True
            raise
        finally:
            if isinstance(sys.exc_info()[1], (GeneratorExit, asyncio.CancelledError)):
                _stream_cancelled = True
            end_turn_scope(turn_scope_token)
            _record_stage("cleanup_started")
            if resume_token is not None:
                from machina.permissions.broker import resume_approval_var

                try:
                    resume_approval_var.reset(resume_token)
                except ValueError:
                    # Same as the turn scope above: an async-generator close
                    # can run in a different task context; the resumed
                    # request context is already gone.
                    pass
            from machina.core.background_tasks import await_joined, await_joined_bounded

            async def cleanup():
                active = supervisor.active
                if active is not None and not active.done():
                    active.cancel()
                    await asyncio.wait({active}, timeout=0.1)
                if active is not None and not active.done():
                    from machina.core.runtime import runtime_services

                    async def retire_execution():
                        # L1b: bound the join of the wedged step. Same contract
                        # as the engine's H01 source close — a cancellation
                        # delivered while joining is consumed once and the
                        # operation gets the bound again; at the deadline the
                        # step is quarantined by ``await_joined_bounded``
                        # (cancelled, strongly referenced, failure observed)
                        # and ``close_turn()`` returns a pending outcome. Provider
                        # close and claim release wait for actual retirement;
                        # renewal continues to exclude a successor meanwhile.
                        try:
                            await await_joined_bounded(
                                active,
                                timeout=RETIRE_JOIN_TIMEOUT_SECONDS,
                                label="app.retire_execution",
                            )
                        except asyncio.CancelledError:
                            pass
                        except Exception as error:
                            record_failure("app.retire_execution", error)
                        # The engine's source close treats a still-running step
                        # as its driver barrier: until it retires, provider and
                        # claim stay owned (deferred cleanup). Only a settled
                        # driver lets this close settle now.
                        await close_turn(retirement_task=active)

                    runtime_services.track(active)
                    runtime_services.track(asyncio.create_task(retire_execution()))
                    return None
                return await close_turn()

            close = await await_joined(cleanup())
            if close is None:
                if not _stream_cancelled:
                    yield frame(
                        {
                            "type": "turn.cleanup_pending",
                            "sessionId": session_id,
                            "turnId": turn_id,
                        }
                    )
            else:
                _release_failed = close.release_failed
                # H-03: Auto-compaction is fire-and-forget background work.
                # Never blocks [DONE] or holds the stream open.
                # AH-AUDIT-025: tracked by the shared runtime for bounded
                # shutdown joining before DB closure.
                if not _stream_cancelled and not _release_failed:
                    _record_stage("compaction_maintenance")
                    lifecycle.schedule_auto_compact()
                if not _stream_cancelled:
                    if _release_failed:
                        yield frame(
                            {
                                "type": "turn.cleanup_pending",
                                "reason": "ownership release unavailable",
                            }
                        )
                    else:
                        _record_stage("answer_complete")
                        if terminal_data is not None:
                            yield frame(terminal_data)
                        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # Canonical forward name + legacy alias (B4 documents both until a
            # deprecation window; the version value is normative, not prefix).
            "X-Machina-Protocol-Version": str(req.protocolVersion),
            "X-AH-Protocol-Version": str(req.protocolVersion),
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
