"""The interactive/headless ``chat`` and ``repl`` commands.

Headless chat drives the shared ``TurnEngine`` through ``TurnLifecycle``: the
same claim, ownership-loss, redaction and terminal-report contract as the
gateway and REST transports.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid

import typer
from rich.markup import escape

from machina.cli import output
from machina.cli.common import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PAUSED,
    _apply_cli_mode,
    _parse_uuid,
    _run,
)
from machina.cli.output import console
from machina.core.background_tasks import drain_background_tasks
from machina.core.config import config
from machina.core.execution_context import begin_turn_scope, end_turn_scope
from machina.core.session import session_manager
from machina.core.turn_lifecycle import ApprovalPolicy, TurnLifecycle
from machina.db.connection import db
from machina.observability.diagnostics import record_failure
from machina.observability.tracing import span
from machina.ui_launcher import launch_ui

# Keep the pre-split logger name: operator logging config may target
# ``machina.cli`` rather than the feature module.
logger = logging.getLogger("machina.cli")


# Claim renewal cadence for headless CLI turns (gateway/SSE use the same).
CLI_TURN_RENEW_INTERVAL_SECONDS = 30.0


def _report_terminal_response(
    final, streamed_parts: list[str], *, terminal_text: str | None = None
) -> int:
    """Display the terminal response; return the chat command's exit status.

    R14: the terminal ``AgentResponse`` is authoritative. Failure diagnostics
    and approval pauses are surfaced even when the provider stream emitted no
    text deltas, canonical content the stream never displayed is printed once
    (never duplicating an answer that already streamed), and the outcome
    selects the documented exit status: completed → 0, failed → EXIT_FAILED,
    paused for approval → EXIT_PAUSED.

    ``terminal_text`` is the lifecycle's fully-redacted canonical text (the
    withheld tail included); it keeps display redaction identical to the
    gateway/REST terminal frame while ``final`` still supplies the outcome and
    typed pauses.
    """
    if final is None:
        return EXIT_OK
    content = (
        terminal_text if terminal_text is not None else str(getattr(final, "content", "") or "")
    )
    content = str(content or "")
    streamed = "".join(streamed_parts)
    if content.strip():
        if streamed and content.startswith(streamed):
            # Only the withheld suffix is new; never replay emitted text.
            missing = content[len(streamed) :]
            if missing.strip():
                console.print()
                console.out(missing, highlight=False)
        elif content.strip() not in streamed:
            # Error/pause paths return their diagnostic as terminal content
            # without text frames; print it so it is visible exactly once.
            console.print()
            console.out(content, highlight=False)
    pauses = [
        pause
        for pause in (getattr(final, "needs_approval", None) or [])
        if isinstance(pause, dict) and pause.get("request_id")
    ]
    if pauses:
        from machina.protocol import approval_summary

        output.error(
            "Turn paused for approval; it did not run to completion.",
            "Resolve the request, then resume the session.",
        )
        for pause in pauses:
            summary = approval_summary(pause)
            detail = summary.get("operation", "")
            if summary.get("target"):
                detail = f"{detail} — {summary['target']}" if detail else summary["target"]
            line = f"  [yellow]request {escape(summary.get('requestId', ''))}[/yellow]"
            if detail:
                line += f" ({escape(detail)})"
            console.print(line)
        return EXIT_PAUSED
    if str(getattr(final, "outcome", "completed") or "completed") == "failed":
        output.error("Turn failed.")
        return EXIT_FAILED
    return EXIT_OK


def chat(
    message: str = typer.Argument(None, help="Message to send to the agent"),
    continue_: bool = typer.Option(False, "--continue", "-c", help="Continue last session"),
    session_id: str | None = typer.Option(None, "--session", "-s", help="Resume specific session"),
    model: str | None = typer.Option(
        None, "--model", "-m", help="Model to use (e.g., openrouter/free)"
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help=(
            "LLM provider (openrouter, openai, anthropic, chatgpt, google, mistral, groq, "
            "together, deepseek, xai, opencode, kimi, kiro, custom, ollama). "
            "opencode = OpenCode Zen gateway (OPENCODE_API_KEY); "
            "opencode_session = text-only local OpenCode session bridge; "
            "kiro = Kiro account through a local OpenAI-compatible proxy; custom = any "
            "OpenAI-compatible base URL (CUSTOM_BASE_URL, key optional)"
        ),
    ),
    verbose: bool = typer.Option(True, "--verbose/--quiet", "-v/-q", help="Show tool calls"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Open the interactive UI"),
    mode: str = typer.Option(None, "--mode", help="Execution mode: ask|workspace|sandbox|full"),
):
    """Send one message to the agent (or open the UI with -i).

    Exit statuses: 0 completed, 1 failed (including a session already busy
    with another live turn), 3 paused — the turn is waiting for an approval
    decision, 130 interrupted — Ctrl+C cancelled the running turn and its
    cleanup ran. Paused/failed runs point at the terminal response, never
    only at the session ID.
    """
    normalized_mode = _apply_cli_mode(mode) if mode is not None else None
    if interactive:
        raise typer.Exit(
            launch_ui(model=model, provider=provider, session_id=session_id, mode=mode)
        )

    # Guard before any I/O: message is required, and slicing it for the
    # session title (message[:50]) would raise TypeError on None.
    if not message:
        console.print('[yellow]No message provided. Use: machina chat "your message"[/yellow]')
        raise typer.Exit(1)

    async def _chat():
        await db.connect()
        agent = None
        # P0-18: headless chat is a first-class transport but never owned the
        # audit writer, so every event it emitted (including this run's start
        # record) hit the "dropped before the writer started" path. Own the
        # writer for the whole one-shot run; the finally below drains it
        # before the database closes.
        from machina.observability.audit import audit_persistence

        audit_persistence.start()
        try:
            try:
                await db.initialize_runtime_schema()
            except Exception as error:
                output.error("Runtime schema upgrade failed. Run machina init and retry.")
                raise typer.Exit(1) from error
            if continue_:
                session = await session_manager.get_last_active()
                if not session:
                    output.error(
                        "No active session to continue.", "Use `machina sessions` to list sessions."
                    )
                    raise typer.Exit(1)
                output.muted(f"Continuing session: {session.id}")
            elif session_id:
                sid = _parse_uuid(session_id)
                if sid is None:
                    output.error(f"Invalid session ID: {session_id}")
                    raise typer.Exit(1)
                session = await session_manager.get(sid)
                if not session:
                    output.error(
                        f"Session {session_id} not found",
                        "Use `machina sessions` to list sessions.",
                    )
                    raise typer.Exit(1)
                output.muted(f"Resuming session: {session.id}")
            else:
                session = await session_manager.create(title=message[:50], goal=message[:100])
                output.muted(f"New session: {session.id}")
                # R4-018: mirror the gateway/REST session-create paths — a
                # persistent `full` default is disclosed consent, so activate
                # the same session-scoped full grant. Without it every
                # governed headless tool call pauses with an approval this
                # transport cannot resolve (exit 3).
                try:
                    from machina.gateway.features.mode import grant_full_default

                    if await grant_full_default(session):
                        output.muted("FULL HOST mode active for this session (saved full default).")
                except Exception as grant_error:
                    record_failure("cli.chat.grant_full_default", grant_error)
                    logger.warning("full-mode grant activation failed: %s", grant_error)

            # R04: headless chat is a first-class transport — claim the shared
            # session turn before building or streaming anything, so a live
            # gateway/REST/job/CLI owner rejects this prompt instead of
            # interleaving transcript writes and tool effects. The shared
            # lifecycle renews the claim for the whole turn and releases it
            # owner-matched (the same seam gateway/server.py and
            # api/routes/streaming.py use).
            turn_id = uuid.uuid4().hex[:12]
            turn_token = await TurnLifecycle.acquire_claim(session.id, turn_id=turn_id)
            if not turn_token:
                output.error(
                    f"Session {session.id} is busy: another turn is already running for it.",
                    "Wait for it to finish (or stop it in the UI), then retry.",
                )
                raise typer.Exit(EXIT_FAILED)

            ownership_lost = asyncio.Event()
            # R04b: ownership loss must stop the running work immediately —
            # the same contract as gateway/server.py and
            # api/routes/streaming.py, whose on_lost hooks cancel the task
            # that drives the stream. Cancelling this task interrupts the
            # relay at its current await; a flag checked only after the next
            # yielded event would let a delayed effect execute after loss.
            _owned_task = asyncio.current_task()

            class _CliTurnHooks:
                def on_stage(_self, stage: str) -> None:
                    return None

                def on_ownership_lost(_self, reason: str) -> None:
                    if _owned_task is not None:
                        _owned_task.cancel()

            async def _build_cli_agent(target_session):
                # Same shared factory as gateway/REST/jobs (definition, shared
                # memory/RAG, authority). Headless: no approver → broker returns
                # structured needs_approval instead of hanging.
                from machina.core.agent_factory import build_agent_for_session

                return await build_agent_for_session(
                    target_session, provider_override=provider, model_override=model
                )

            lifecycle = TurnLifecycle(
                session_id=session.id,
                turn_id=turn_id,
                token=turn_token,
                text=message,
                verbose=False,
                boundary="cli.chat",
                renew_interval_s=CLI_TURN_RENEW_INTERVAL_SECONDS,
                join_timeout=10.0,
                settle_grace=1.0,
                approval=ApprovalPolicy(mode="headless", principal="headless"),
                hooks=_CliTurnHooks(),
                agent_builder=_build_cli_agent,
            )

            # The mode change is part of the turn mutation, not a side channel:
            # apply it only after the ownership claim and release that claim if
            # the durable activation or ceiling check fails. Use the shared
            # activation path so full grants, revocations, cache invalidation,
            # and env shadows stay consistent with gateway/REST.
            try:
                if (
                    normalized_mode is not None
                    and getattr(session, "execution_mode", None) != normalized_mode
                ):
                    if db.connected:
                        from machina.core.session_mode import activate_durable_mode

                        await activate_durable_mode(
                            str(session.id), session.agent_id, normalized_mode
                        )
                    else:
                        from machina.core.session_mode import set_session_mode

                        set_session_mode(str(session.id), normalized_mode)
                    session.execution_mode = normalized_mode

                ceiling = os.environ.get("MACHINA_MCP_MODE_CEILING")
                if ceiling is not None:
                    ranks = {"ask": 0, "workspace": 1, "sandbox": 2, "full": 3}
                    if ranks.get(session.execution_mode, 4) > ranks.get(ceiling, 0):
                        output.error("mode_ceiling_exceeded")
                        raise typer.Exit(1)
            except BaseException:
                try:
                    await lifecycle.close()
                except Exception as release_error:
                    record_failure("cli.chat.release_turn_after_mode_failure", release_error)
                raise

            # P0-18: forensic start record BEFORE the agent executes. Emitted
            # after the claim is held (a busy rejection is not a started run)
            # and before build/stream, so a crash mid-turn still leaves
            # evidence of the attempted run. Best-effort: queued like every
            # other audit write and never allowed to fail the turn.
            from machina.core.provider.audit import audit_log

            audit_log(
                "chat.start",
                session_id=str(session.id),
                transport="cli",
                mode=getattr(session, "execution_mode", None) or config.get("execution_mode"),
                turn_id=turn_id,
            )
            lifecycle.start(ownership_lost=ownership_lost)
            turn_scope_token = None
            final = None
            streamed_parts: list[str] = []
            try:
                # R04c: headless turns have no interactive approver, but the
                # pause record still carries this turn as its origin so
                # poll-based recovery (resolve + HTTP resume) can validate and
                # attribute the continuation.
                try:
                    agent = await lifecycle.prepare(session)
                    llm = agent.provider
                    _provider_name = provider or session.provider or config.get("provider")
                    # An explicit CLI route is a session pin, not just a
                    # one-turn override. Delegated children and later resume
                    # turns resolve the durable session route; leaving it null
                    # made a Kiro parent launch an OpenRouter child.
                    if provider is not None or model is not None:
                        try:
                            await session_manager.set_model(
                                session.id, model=llm.model, provider=str(_provider_name)
                            )
                            session.model = llm.model
                            session.provider = str(_provider_name)
                        except Exception as route_error:
                            record_failure("cli.chat.pin_route", route_error)
                except ValueError as e:
                    output.error(
                        f"Provider error: {e}",
                        "Check your provider configuration with `machina config`.",
                    )
                    raise typer.Exit(1) from None

                if verbose:
                    output.muted(f"Model: {llm.model} ({_provider_name})")
                    try:
                        output.muted(f"Mode: {config.get('execution_mode')} (backend: host)")
                    except Exception:
                        pass
                console.print()

                # R4-010: driver-owned turn scope (contextvar must exist
                # before per-step tasks — see TurnEngine.stream).
                turn_scope_token = begin_turn_scope()
                # R4-064: driver-owned OTel stream span — the CLI drives
                # the relay inline in this one task, so enter and detach
                # share a context.
                with span(
                    "agent.run_stream",
                    agent_id=str(getattr(agent, "agent_id", "")),
                    session_id=str(session.id),
                ):
                    # The shared engine owns boundary-safe streaming redaction,
                    # tool pairing and final capture; this loop only renders
                    # the semantic events on the terminal.
                    async with contextlib.aclosing(lifecycle.stream()) as stream:
                        async for turn_event in stream:
                            if ownership_lost.is_set():
                                break
                            if turn_event.kind == "delta":
                                text = turn_event.payload["text"]
                                streamed_parts.append(text)
                                console.out(text, end="", highlight=False)
                            elif turn_event.kind == "tool_start" and verbose:
                                console.print(
                                    f"\n[yellow]→ {escape(turn_event.payload['name'])}[/yellow]"
                                )
                            elif turn_event.kind == "tool_complete" and verbose:
                                preview = str(turn_event.payload["result"])[:120].replace("\n", " ")
                                console.print(f"[dim]  ← {escape(preview)}[/dim]")
                            elif turn_event.kind == "done":
                                final = lifecycle.final
                    if not ownership_lost.is_set():
                        # AH-AUDIT-027 parity: emit the withheld safe tail once
                        # as a delta before the terminal report so displayed
                        # text equals the canonical redacted answer once.
                        tail = lifecycle.flush_tail()
                        if tail:
                            streamed_parts.append(tail)
                            console.out(tail, end="", highlight=False)
                console.print()
            except asyncio.CancelledError:
                # Loss cancels this task through _CliTurnHooks: that is the
                # requested stop, and the cleanup below still runs. Any other
                # cancellation is not ours to swallow.
                if not ownership_lost.is_set():
                    raise
            finally:
                if turn_scope_token is not None:
                    end_turn_scope(turn_scope_token)
                try:
                    close = await lifecycle.close(cancelled=ownership_lost.is_set())
                except asyncio.CancelledError:
                    # A loss cancellation can still be pending if it raced
                    # into cleanup; the loss event already recorded it — the
                    # claim must still be released.
                    if not ownership_lost.is_set():
                        raise
                else:
                    if close.release_failed:
                        record_failure(
                            "cli.chat.release_turn",
                            RuntimeError("turn claim release unavailable"),
                        )
                        logger.warning(
                            "Session %s turn claim release unavailable; "
                            "claim falls back to TTL expiry",
                            session.id,
                        )

            if ownership_lost.is_set():
                output.error(
                    "Turn ownership was lost while running; stopped instead of "
                    "interleaving with the session's new owner.",
                )
                raise typer.Exit(EXIT_FAILED)

            terminal_text = lifecycle.build_terminal(cancelled=False)["text"]
            status = _report_terminal_response(final, streamed_parts, terminal_text=terminal_text)
            if final is not None and verbose:
                output.muted(
                    f"Iterations: {final.iterations} | Tool calls: {len(final.tool_calls)} "
                    f"| Tokens: {final.tokens_used}"
                )
            output.muted(f"Session ID: {session.id}")
            if status != EXIT_OK:
                raise typer.Exit(status)
        finally:
            # Fire-and-forget consolidation/review tasks need the live
            # provider AND a live pool — drain them before either closes, or
            # they die mid-flight and CLI-session memories never consolidate.
            # Bounded, never raises.
            try:
                await drain_background_tasks(timeout=10.0)
            except Exception as _drain_error:
                logger.debug("background drain failed (%s)", _drain_error)
            try:
                if agent is not None:
                    from machina.core.agent_factory import close_agent_provider

                    await close_agent_provider(agent)
            except Exception:
                pass
            # P0-18: stop the writer this transport started, before the pool
            # closes (a pending row must not lose its connection).
            try:
                await audit_persistence.stop()
            except Exception as audit_error:
                record_failure("cli.chat.audit_stop", audit_error)
            await db.close()

    try:
        _run(_chat())
    except KeyboardInterrupt:
        # R4-017: Ctrl+C — asyncio cancelled the running turn on the main
        # loop; _chat's finally blocks already released the claim and closed
        # the provider/database. Report the conventional SIGINT status
        # instead of Click's generic "Aborted!" (exit 1).
        raise typer.Exit(130) from None


def repl(
    model: str = typer.Option(None, "--model", "-m", help="Model to use"),
    provider: str = typer.Option(None, "--provider", "-p", help="LLM provider"),
    session_id: str | None = typer.Option(None, "--session", "-s", help="Resume specific session"),
    mode: str = typer.Option(None, "--mode", help="Execution mode: ask|workspace|sandbox|full"),
):
    """Open the interactive UI (same as running `machina` with no arguments)."""
    if mode is not None:
        _apply_cli_mode(mode)
    raise typer.Exit(launch_ui(model=model, provider=provider, session_id=session_id, mode=mode))
