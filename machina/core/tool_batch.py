"""Concurrent tool-batch execution: validate, authorize, run, record.

One assistant message may emit several tool calls. This module owns the batch
contract: request-order starts, bounded concurrency, per-tool timeouts, broker
authorization, result recording in provider order, and cancellation cleanup.
The agent facade delegates here; the executor sees only the agent surface it
needs, documented as :class:`ToolBatchHost`.

``execute_stream`` is a four-phase orchestrator; each phase is a private method:

A. :meth:`_plan_batch` parses and validates the whole batch (no side effects).
B. :meth:`_start_events` emits one start per runnable call, in request order.
C. :meth:`_completion_events` runs calls concurrently and yields completions
   as each finishes, cancelling and finalizing on an exceptional exit.
D. :meth:`_record_batch` records in provider order and appends the replies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Protocol

from rich.console import Console

from machina.core.agent_support import tool_outcome as _tool_outcome
from machina.core.background_tasks import joined_teardown
from machina.core.config import config
from machina.core.exceptions import DelegationAccountingError
from machina.core.metrics import metrics
from machina.core.models import LLMResponse, StreamEvent, ToolDefinition
from machina.core.prompt import _ensure_tool_call_id
from machina.core.provider import audit_log
from machina.core.rsi_context import rsi_choice as _rsi_choice
from machina.core.rsi_context import rsi_skills as _rsi_skills
from machina.observability.diagnostics import record_failure
from machina.observability.tracing import span
from machina.plugins.registry import plugin_registry
from machina.tools.base import registry

logger = logging.getLogger(__name__)
console = Console()

__all__ = ["ToolBatchExecutor", "ToolBatchHost"]


_TOOL_TIMEOUTS: dict[str, float] = {
    "share_memory": 60.0,
    "web_search": 45.0,
    "web_extract": 45.0,
    "web_research": 120.0,
    "web_crawl": 120.0,
    "browser_verify": 140.0,
    "web_verify": 140.0,
    "search_files": 120.0,
    "delegate_status": 320.0,
    "index_document": 120.0,
    "terminal": 60.0,
    # N6 (final pass): a cold scan of a large repo can exceed the 30s default;
    # 120s matches the other "legitimately slow read" tools above.
    "learn_directory": 120.0,
    # A question waits for a human, not for a provider or tool. The broker's
    # own timeout is the upper bound; this keeps the wrapper from cancelling
    # the question first.
    "ask_user": 620.0,
}
_TOOL_TIMEOUT_DEFAULT = 30.0
_TERMINAL_CAP = 300.0  # R4-019: terminal's schema max (1..300); tools/terminal.py.

# Independent tool calls in one assistant message run concurrently, capped so a
# model that emits a large batch cannot spawn unbounded subprocesses / queries.
_MAX_PARALLEL_TOOLS = 8


@dataclass
class _ToolRun:
    """Mutable per-call state shared by the Phase C helpers.

    The timeout is adjusted after the broker spec is resolved, and the raw
    result object (not its string form) is needed for outcome/accounting
    checks; both travel through the helpers here instead of hidden closures.
    """

    item: dict[str, Any]
    session_id: uuid.UUID
    tool_name: str
    tool_args: dict[str, Any]
    start: float
    timeout: float
    merged: dict[str, Any] = field(default_factory=dict)
    authority_token: Any = None
    result: Any = None


class ToolBatchHost(Protocol):
    """The agent surface a tool batch needs, and nothing else.

    The executor reads four descriptive attributes once per batch and calls
    five persistence/approval seams. Turn state, providers, the loop, and the
    execution mode live outside this interface: the executor obtains authority
    and mode from their own services, so a host can be a small test double
    without a database or a provider.

    Ordering invariants the host must honor:

    - ``_record_tool_action`` is called once per finished item in provider
      order and must be idempotent (``item['_recorded']``).
    - ``_ensure_artifact_view`` runs before ``_record_tool_action`` so a
      durable chunk never publishes a payload without its artifact handle
      (MCH-010).
    - ``_finalize_completed_tools`` is safe to call again after a normal batch
      and is the only cleanup path on cancellation (F02).
    """

    agent_id: str
    delegated: bool
    dynamic_tools: bool

    @property
    def allowed_tools(self) -> list[str] | None: ...

    def _append_tool_messages(
        self,
        messages: list[dict[str, Any]],
        response: LLMResponse,
        tc: dict[str, Any],
        content: str,
    ) -> None: ...

    async def _ensure_artifact_view(self, item: dict[str, Any], session_id: uuid.UUID) -> str: ...

    async def _record_tool_action(
        self,
        item: dict[str, Any],
        session_id: uuid.UUID,
        pending_chunks: list[dict[str, Any]] | None,
        tool_calls_made: list[dict[str, Any]],
    ) -> None: ...

    async def _retire_tool_approval(self, item: dict[str, Any], outcome: str) -> None: ...

    async def _finalize_completed_tools(
        self,
        pending: list[dict[str, Any]],
        session_id: uuid.UUID,
        pending_chunks: list[dict[str, Any]] | None = None,
        tool_calls_made: list[dict[str, Any]] | None = None,
    ) -> None: ...


class ToolBatchExecutor:
    """Execute one assistant tool batch for a bound host."""

    def __init__(self, agent: ToolBatchHost) -> None:
        self.agent = agent

    def definitions(self) -> list[ToolDefinition]:
        """Tool definitions offered to the model, honoring the allow-list."""
        defs = registry.get_tool_definitions()
        if self.agent.delegated:
            defs = [
                definition
                for definition in defs
                if definition.name
                not in {"learn_personality", "delegate", "delegate_status", "list_agents"}
            ]
        elif not config.get("personality_growth_enabled") or not config.get("memory_enabled"):
            defs = [d for d in defs if d.name != "learn_personality"]
        if self.agent.allowed_tools is not None:
            allowed = set(self.agent.allowed_tools)
            defs = [d for d in defs if d.name in allowed]
        from machina.memory.rsi import enabled

        weights = (_rsi_choice.get() or {}).get("tool_weights", {}) if enabled() else {}
        ordered = sorted(
            defs,
            key=lambda tool: -max(0.8, min(1.2, weights.get(tool.name, 1))),
        )
        from machina.core.tool_projection import (
            active_tool_definitions,
            compact_tool_definitions,
            set_dynamic_allowed_tools,
        )

        if getattr(self.agent, "dynamic_tools", False):
            set_dynamic_allowed_tools(self.agent.allowed_tools)
            ordered = active_tool_definitions(ordered)
        return compact_tool_definitions(ordered)

    async def execute(
        self,
        response: LLMResponse,
        messages: list[dict[str, Any]],
        tool_calls_made: list[dict[str, Any]],
        session_id: uuid.UUID,
        verbose: bool,
        pending_chunks: list[dict[str, Any]] | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> None:
        """Execute tool calls for the non-streaming loop.

        Thin wrapper over :meth:`execute_stream` (the single implementation)
        that prints progress when *verbose* is set.
        """
        async with joined_teardown(
            stream=self.execute_stream(
                response,
                messages,
                tool_calls_made,
                session_id,
                pending_chunks,
                allowed_tool_names=allowed_tool_names,
            )
        ) as events:
            async for event in events:
                if not verbose:
                    continue
                if event.type == "tool_call":
                    args_preview = json.dumps(event.tool_args, default=str)[:80]
                    console.print(f"[yellow]  → {event.tool_name}({args_preview})[/yellow]")
                elif event.type == "tool_result":
                    preview = event.tool_result[:200].replace("\n", " ")
                    console.print(f"[green]  ← {preview}[/green]")

    async def execute_stream(
        self,
        response: LLMResponse,
        messages: list[dict[str, Any]],
        tool_calls_made: list[dict[str, Any]],
        session_id: uuid.UUID,
        pending_chunks: list[dict[str, Any]] | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Execute tool calls from an LLM response, yielding StreamEvents.

        Shared by ``run()`` and ``run_stream()``. Handles missing tool names,
        invalid JSON arguments, execution with timing, audit logging, context
        storage, and message updates. Modifies *messages* and
        *tool_calls_made* in place.

        Independent tool calls in one assistant message run **concurrently**
        (bounded by ``_MAX_PARALLEL_TOOLS``, or the configured delegate limit
        for delegation) instead of one after another, so a
        batch of N reads costs roughly max(latency) rather than sum(latency).
        Starts are emitted up front in request order; completions are yielded
        as each call finishes; persistence and provider replies follow the
        original order so the transcript stays deterministic.

        If *pending_chunks* is provided, tool_call chunks are accumulated there
        instead of being inserted immediately. The caller is responsible for
        flushing them via ``context_manager.add_chunks_batch``.
        """
        s3trace = self._trace_hook()

        # ── Phase A: parse and validate the whole batch ─────────────────────
        planned = self._plan_batch(response, session_id, allowed_tool_names)

        # ── Phase B: emit starts BEFORE execution (AH-020) ────────────────
        for event in self._start_events(planned):
            yield event

        # ── Phase C: execute concurrently, yielding completions as they finish
        completions = self._completion_events(
            planned,
            session_id,
            pending_chunks,
            tool_calls_made,
            s3trace,
        )
        try:
            async for event in completions:
                yield event
        finally:
            # Close explicitly: an async generator is otherwise finalized
            # later by the event loop, and this phase's cleanup (task cancel +
            # finalize) must run before the caller observes cancellation.
            await completions.aclose()

        # ── Phase D: metrics for denied + persistence in provider order ───
        recording = self._record_batch(
            planned,
            messages,
            response,
            tool_calls_made,
            session_id,
            pending_chunks,
        )
        try:
            async for event in recording:
                yield event
        finally:
            await recording.aclose()

    # ── Phase A ─────────────────────────────────────────────────────────────

    def _plan_batch(
        self,
        response: LLMResponse,
        session_id: uuid.UUID,
        allowed_tool_names: set[str] | None,
    ) -> list[dict[str, Any]]:
        """Parse and validate every call before any tool can run.

        Missing names and invalid JSON become typed error replies; a call
        absent from the agent or step allow-list becomes a denied reply. The
        returned items carry ``tc``/``name``/``args``/``result``/``call_id``
        and are mutated by later phases.
        """
        planned: list[dict[str, Any]] = []
        for tc in response.tool_calls:
            function_data = tc.get("function", {})
            tool_name = function_data.get("name")
            call_id = _ensure_tool_call_id(tc)
            if not tool_name:
                logger.warning("Tool call missing 'name' field: %s", tc)
                audit_log(
                    "tool_call_invalid",
                    session_id=str(session_id),
                    error="missing_name",
                )
                planned.append(
                    {
                        "tc": tc,
                        "name": None,
                        "args": {},
                        "result": "Error: tool call missing 'name' field",
                        "call_id": call_id,
                    }
                )
                continue

            raw_args = function_data.get("arguments") or "{}"
            try:
                tool_args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
                if not isinstance(tool_args, dict):
                    raise ValueError("tool arguments must be an object")
            except (TypeError, ValueError) as e:
                # L-03: raw tool arguments can carry credentials; log a
                # bounded, secret-redacted preview instead of the payload.
                try:
                    from machina.memory.redaction import redact_secrets

                    raw_preview = redact_secrets(str(raw_args)[:500]).text
                except Exception:
                    raw_preview = "<unavailable>"
                logger.error(
                    "Failed to parse tool arguments for '%s': %s (raw preview: %s)",
                    tool_name,
                    e,
                    raw_preview,
                )
                audit_log(
                    "tool_call_invalid_args",
                    session_id=str(session_id),
                    tool_name=tool_name,
                    error=str(e),
                )
                planned.append(
                    {
                        "tc": tc,
                        "name": None,
                        "args": {},
                        "result": f"Error: invalid tool arguments: {e}",
                        "call_id": call_id,
                    }
                )
                continue

            denied = self._denial_result(tool_name, session_id, allowed_tool_names)
            if denied is not None:
                planned.append(
                    {
                        "tc": tc,
                        "name": tool_name,
                        "args": tool_args,
                        "result": denied,
                        "call_id": call_id,
                    }
                )
                continue

            planned.append(
                {"tc": tc, "name": tool_name, "args": tool_args, "result": None, "call_id": call_id}
            )
        return planned

    def _denial_result(
        self,
        tool_name: str,
        session_id: uuid.UUID,
        allowed_tool_names: set[str] | None,
    ) -> str | None:
        """Return a typed denial reply, or ``None`` when the tool may run."""
        if self.agent.allowed_tools is not None and tool_name not in self.agent.allowed_tools:
            audit_log(
                "tool_call_denied",
                session_id=str(session_id),
                tool_name=tool_name,
                agent_id=self.agent.agent_id,
            )
            return f"Error: tool '{tool_name}' is not allowed for agent '{self.agent.agent_id}'"

        # A model can retain a schema loaded earlier in the turn (for example
        # via tool_load) after the current step has narrowed the surface.
        # Enforce the schema actually advertised for this step, not just the
        # agent's static allow-list.
        if allowed_tool_names is not None and tool_name not in allowed_tool_names:
            audit_log(
                "tool_call_denied",
                session_id=str(session_id),
                tool_name=tool_name,
                agent_id=self.agent.agent_id,
                reason="not_in_tool_surface",
            )
            return (
                f"Error: tool '{tool_name}' is not in the current tool surface. "
                "Use only tools advertised for this step."
            )
        return None

    # ── Phase B ─────────────────────────────────────────────────────────────

    def _start_events(self, planned: list[dict[str, Any]]) -> Iterator[StreamEvent]:
        """Emit starts in request order with stable call IDs.

        Long-running tools must appear as running while executing, not only
        after all calls finish. Denied tools (result already set) still get a
        start+complete pair for UI consistency; they complete immediately in
        Phase D.
        """
        for item in planned:
            if item["name"] is None:
                continue
            yield StreamEvent(
                type="tool_call",
                tool_name=item["name"],
                tool_args=item["args"],
                tool_call_id=item["call_id"],
            )

    # ── Phase C ─────────────────────────────────────────────────────────────

    async def _completion_events(
        self,
        planned: list[dict[str, Any]],
        session_id: uuid.UUID,
        pending_chunks: list[dict[str, Any]] | None,
        tool_calls_made: list[dict[str, Any]],
        s3trace: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run runnable calls concurrently and yield results as each finishes.

        Completions are intentionally out of request order — the call ID pairs
        each result with its start. Metrics are recorded here at completion
        time; persistence happens in Phase D in provider-required order. An
        exceptional exit cancels the remaining tasks and finalizes completed
        actions exactly once (MCH-010: not on the normal path).
        """
        # Mark denied items so _run_one skips execution but Phase D still emits.
        for item in planned:
            if item["name"] is not None and item["result"] is not None:
                item["_denied"] = True

        runnable = [
            item for item in planned if item["name"] is not None and not item.get("_denied")
        ]
        completion_order: list[dict[str, Any]] = []
        if runnable:
            semaphore = asyncio.Semaphore(_MAX_PARALLEL_TOOLS)
            try:
                delegate_limit = int(config.get("max_delegate_concurrency") or 8)
            except (TypeError, ValueError):
                delegate_limit = 8
            delegate_semaphore = asyncio.Semaphore(max(1, min(delegate_limit, 64)))

            async def _bounded(item: dict[str, Any]) -> dict[str, Any]:
                slots = delegate_semaphore if item["name"] == "delegate" else semaphore
                async with slots:
                    with s3trace.trace_tool_call(item) if s3trace else nullcontext() as _trace:
                        await self._run_one(item, session_id)
                        if _trace is not None:
                            _trace.finish(_tool_outcome(item))
                return item

            tasks = [asyncio.create_task(_bounded(item)) for item in runnable]
            # MCH-010: Phase C finalization exists for EXCEPTIONAL exits only
            # (cancellation/error). On the normal path Phase D must prepare the
            # artifact handle BEFORE publishing the durable tool chunk; recording
            # here first would freeze a payload without the handle.
            finalize_on_exit = True
            try:
                for coro in asyncio.as_completed(tasks):
                    try:
                        finished = await coro
                    except DelegationAccountingError:
                        raise
                    except Exception:
                        continue
                    completion_order.append(finished)
                    # Yield completion immediately (out-of-order OK — call ID
                    # pairs it with its start). Metrics + persistence happen
                    # in Phase D in provider-required original order.
                    tool_name = finished["name"]
                    result_str = finished["result"]
                    if "elapsed_ms" in finished:
                        metrics.record_latency(f"tool.{tool_name}", finished["elapsed_ms"])
                        metrics.increment_counter(f"tool.{tool_name}.calls")
                        if result_str.startswith("Error:"):
                            metrics.record_error(f"tool.{tool_name}")
                    yield StreamEvent(
                        type="tool_result",
                        tool_name=tool_name,
                        tool_result=result_str,
                        tool_call_id=finished["call_id"],
                        tool_outcome=_tool_outcome(finished),
                    )
                    # AH-AUDIT-030: typed pause alongside the result so live
                    # consumers need not wait for the terminal response.
                    _pauses_c = finished.get("_needs_approvals") or [
                        finished.get("_needs_approval")
                    ]
                    for _pause_c in _pauses_c:
                        if not isinstance(_pause_c, dict) or not _pause_c.get("request_id"):
                            continue
                        if s3trace is not None:
                            s3trace.record_approval_pause(_pause_c)
                        yield StreamEvent(
                            type="needs_approval",
                            content=str(_pause_c.get("request_id", "")),
                            tool_name=tool_name,
                            tool_args={
                                "requestId": str(_pause_c.get("request_id", "")),
                                "sessionId": str(_pause_c.get("session_id", "")),
                                "operation": str(_pause_c.get("operation", "")),
                                "target": str(_pause_c.get("target", "")),
                                "status": str(_pause_c.get("status", "pending")),
                            },
                            tool_call_id=finished["call_id"],
                        )
                finalize_on_exit = False
            finally:
                # Ensure no task leaks on cancellation.
                for t in tasks:
                    if not t.done():
                        t.cancel()
                # Cancellation is complete only when children retire their
                # claims, reservations, and owned providers.
                from machina.core.background_tasks import await_joined

                if finalize_on_exit:

                    async def finish_batch():
                        await asyncio.gather(*tasks, return_exceptions=True)
                        await self.agent._finalize_completed_tools(
                            planned, session_id, pending_chunks, tool_calls_made
                        )

                    await await_joined(finish_batch())

    # ── Phase D ─────────────────────────────────────────────────────────────

    async def _record_batch(
        self,
        planned: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        response: LLMResponse,
        tool_calls_made: list[dict[str, Any]],
        session_id: uuid.UUID,
        pending_chunks: list[dict[str, Any]] | None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Record every item in provider order and append its tool reply.

        Provider message order must follow the original tool_calls order
        (OpenAI requires tool replies in order), even though Phase C yielded
        completions out of order. A cancellation here still finalizes any
        completed-but-unrecorded action (F02).
        """
        try:
            for item in planned:
                tool_name = item["name"]
                result_str = item["result"]
                tc = item["tc"]

                if tool_name is None:
                    # Missing name / unparsable args: no events, just the reply.
                    self.agent._append_tool_messages(messages, response, tc, result_str)
                    continue

                if item.get("_denied"):
                    metrics.increment_counter(f"tool.{tool_name}.calls")
                    metrics.record_error(f"tool.{tool_name}")
                    await plugin_registry.dispatch("on_tool_result", tool_name, result_str)
                    audit_log(
                        "tool_call_complete",
                        session_id=str(session_id),
                        tool_name=tool_name,
                        duration_ms=0,
                    )
                    # Denied completions were not yielded in Phase C (no
                    # execution); emit them now in order.
                    yield StreamEvent(
                        type="tool_result",
                        tool_name=tool_name,
                        tool_result=result_str,
                        tool_call_id=item["call_id"],
                        tool_outcome=_tool_outcome(item),
                    )
                    # AH-AUDIT-030: typed pause event on the stream (not just in
                    # the terminal done response) so SSE/transports can pause
                    # mid-turn with request binding instead of parsing prose.
                    _pause = item.get("_needs_approval")
                    if isinstance(_pause, dict) and _pause.get("request_id"):
                        yield StreamEvent(
                            type="needs_approval",
                            content=str(_pause.get("request_id", "")),
                            tool_name=tool_name,
                            tool_args={
                                "requestId": str(_pause.get("request_id", "")),
                                "sessionId": str(_pause.get("session_id", "")),
                                "operation": str(_pause.get("operation", "")),
                                "target": str(_pause.get("target", "")),
                                "status": str(_pause.get("status", "pending")),
                            },
                            tool_call_id=item["call_id"],
                        )
                else:
                    # Executed tools: metrics already recorded at completion time;
                    # still dispatch result hooks + audit once (idempotent).
                    if "elapsed_ms" in item and not item.get("_hooked"):
                        # Phase C recorded latency/counters; dispatch hooks here
                        # once in original order for determinism.
                        await plugin_registry.dispatch("on_tool_result", tool_name, result_str)
                        audit_log(
                            "tool_call_complete",
                            session_id=str(session_id),
                            tool_name=tool_name,
                            duration_ms=int(item["elapsed_ms"]),
                        )
                        item["_hooked"] = True

                # T2/T3: retain the original once and append the cached view.
                # The helper makes the same retention decision the recorder
                # uses, so the durable chunk carries the handle (MCH-010).
                view = await self.agent._ensure_artifact_view(item, session_id)
                # Durably record the action exactly once (chunk + approval
                # retirement + tool_calls_made entry; shared with cancellation).
                await self.agent._record_tool_action(
                    item, session_id, pending_chunks, tool_calls_made
                )
                self.agent._append_tool_messages(messages, response, tc, view)
        finally:
            await self.agent._finalize_completed_tools(
                planned, session_id, pending_chunks, tool_calls_made
            )

    # ── Phase C helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _trace_hook() -> Any:
        """S3.3 tool-trace hook (revert: delete this method; observe-only)."""
        try:
            from machina.controller import wiring as trace
        except Exception:
            return None
        return trace

    async def _run_one(self, item: dict[str, Any], session_id: uuid.UUID) -> None:
        """Execute one runnable item and normalize failures into its result."""
        tool_name = item["name"]
        tool_args = item["args"]
        audit_log("tool_call_start", session_id=str(session_id), tool_name=tool_name)
        run = _ToolRun(
            item=item,
            session_id=session_id,
            tool_name=tool_name,
            tool_args=tool_args,
            start=time.monotonic(),
            timeout=_TOOL_TIMEOUTS.get(tool_name, _TOOL_TIMEOUT_DEFAULT),
        )
        try:
            with span("agent.tool", tool_name=tool_name, session_id=str(session_id)):
                await plugin_registry.dispatch("on_tool_call", tool_name, tool_args)
                # AH-018: bind caller scope around EVERY tool (not just a
                # subset) so RAG tools and future tools see the active
                # session/agent. Tokens always reset in finally.
                if not await self._invoke_tool(run):
                    return
                result = run.result
        except DelegationAccountingError:
            # Unknown child spend is fatal for this turn, not a recoverable
            # tool failure that permits another provider call.
            raise
        except Exception as e:
            if isinstance(e, asyncio.TimeoutError) and "elapsed_ms" in item:
                return
            if isinstance(e, asyncio.TimeoutError):
                # R4-019: wait_for raises a message-less TimeoutError; type it.
                error_text = f"tool '{tool_name}' timed out after {run.timeout:g}s"
            else:
                error_text = str(e)
            logger.exception("Tool execution failed for '%s'", tool_name)
            audit_log(
                "tool_call_error",
                session_id=str(session_id),
                tool_name=tool_name,
                error=error_text,
                duration_ms=int((time.monotonic() - run.start) * 1000),
            )
            result = f"Error: {error_text}"
        item["_failed"] = bool(getattr(result, "is_error", False)) or str(result).startswith(
            "Error:"
        )
        skills = _rsi_skills.get()
        if (
            tool_name == "skill_read"
            and not item["_failed"]
            and str(result).startswith("Skill ")
            and skills is not None
            and len(skills) < 200
        ):
            skills.append(tool_args.get("skill_name", ""))
        item["result"] = str(result)
        item["elapsed_ms"] = (time.monotonic() - run.start) * 1000

    async def _invoke_tool(self, run: _ToolRun) -> bool:
        """Bind caller scope, publish authority, and run the authorized call.

        Returns ``False`` when the call ended in a pause or denial (its result
        is already set); ``True`` means the normal result tail must run.
        """
        from machina.tools.agents import current_agent_id, current_session_id

        token = current_session_id.set(run.session_id)
        agent_token = current_agent_id.set(self.agent.agent_id)
        try:
            self._publish_authority(run)
        except Exception:
            current_agent_id.reset(agent_token)
            current_session_id.reset(token)
            raise
        try:
            return await self._authorize_and_execute(run)
        finally:
            try:
                # AH-AUDIT-002: end the guard+execute window so a later tool
                # call cannot inherit this approval's snapshot.
                from machina.permissions.broker import (
                    clear_execution_context as _clear_ctx,
                )

                _clear_ctx()
            except Exception as _boundary_error:
                # Optional fallback preserves the primary outcome; report no payload.
                record_failure("agent._execute_tool_calls_stream", _boundary_error)
            try:
                if run.authority_token is not None:
                    from machina.core.agent_factory import parent_authority_var as _pav2

                    _pav2.reset(run.authority_token)
            except Exception as _boundary_error:
                # Optional fallback preserves the primary outcome; report no payload.
                record_failure("agent._execute_tool_calls_stream", _boundary_error)
            current_agent_id.reset(agent_token)
            current_session_id.reset(token)

    def _publish_authority(self, run: _ToolRun) -> None:
        """Compose this agent's authority overlay and publish it for children.

        LP-08/R-02: preserve inherited authority (max_mode, etc.) and intersect
        its tools with this agent's definition. The authority belongs to the
        host (agent_factory sets ``agent._authority``), not to this executor.
        """
        from machina.core.agent_factory import check_mode_cap, merge_authority
        from machina.core.agent_factory import parent_authority_var as _pav

        _inherited = dict(getattr(self.agent, "_authority", {}) or {})
        _ambient = _pav.get() or {}
        if _ambient.get("max_mode") is not None:
            check_mode_cap(_inherited, _ambient["max_mode"])
        _merged = merge_authority(_inherited, _ambient)
        _parent_tools = _merged.get("tools")
        # Terminal authority includes observing/stopping its own session jobs;
        # this adds no launch capability.
        if (
            _parent_tools is not None
            and "terminal" in _parent_tools
            and "terminal_job" not in _parent_tools
        ):
            _parent_tools = [*_parent_tools, "terminal_job"]
        if _parent_tools is None:
            _merged["tools"] = self.agent.allowed_tools
        elif self.agent.allowed_tools is None:
            _merged["tools"] = list(_parent_tools)
        else:
            _merged["tools"] = [t for t in self.agent.allowed_tools if t in _parent_tools]
        run.merged = _merged
        run.authority_token = _pav.set(_merged)

    async def _authorize_and_execute(self, run: _ToolRun) -> bool:
        """Resolve the execution context, guard the action, then execute it."""
        spec = await self._resolve_spec(run)
        from machina.core.execution_context import resolve_execution_context

        context = await resolve_execution_context(str(run.session_id), run.tool_name)
        mode, backend = context.mode, context.backend
        from machina.core.agent_factory import check_mode_cap
        from machina.core.agent_factory import parent_authority_var as _pav

        # Children inherit the parent's current execution mode, including
        # sandbox isolation, rather than a global default.
        check_mode_cap(run.merged, mode)
        run.merged["max_mode"] = mode
        if run.tool_name == "terminal" and run.merged.get("terminal_backend") == "sandbox":
            backend = "sandbox"
        if mode == "sandbox" or backend == "sandbox":
            run.merged["terminal_backend"] = "sandbox"
        _pav.set(run.merged)
        request = self._build_request(run, spec, mode, backend, context)
        if not await self._guard_tool(run, request):
            return False
        await self._execute_tool(run)
        return True

    async def _resolve_spec(self, run: _ToolRun) -> dict[str, Any]:
        """Refresh connector state when needed and resolve broker kwargs."""
        if run.tool_name in {"mcp_call", "mcp_list", "mcp_describe"}:
            from machina.core.runtime import runtime_services

            await runtime_services.get_connector_service().refresh()
        from machina.permissions import tools as _perm_tools

        spec = _perm_tools.action_for_tool(run.tool_name, run.tool_args)
        if run.tool_name in {"mcp_call", "mcp_connect", "mcp_disconnect"}:
            run.timeout = int(spec.get("timeout", 60)) + 1
        elif run.tool_name == "terminal":
            # Background authorization has no execution deadline; admission
            # still gets a normal startup window.
            run.timeout = (
                60
                if run.tool_args.get("background") is True
                else min(max(int(spec.get("timeout", 60)), 1), _TERMINAL_CAP)
            )
        return spec

    def _build_request(
        self, run: _ToolRun, spec: dict[str, Any], mode: str, backend: str, context
    ) -> Any:
        """Build the broker request the human reviews and the digest binds."""
        from machina.permissions.policy import build_request as _build_req

        return _build_req(
            operation=spec.get("operation", f"tool.{run.tool_name}"),
            targets=spec.get("targets"),
            argv=spec.get("argv"),
            shell_payload=spec.get("shell_payload", ""),
            cwd=spec.get("cwd", "."),
            content=spec.get("content", ""),
            mode=mode,
            backend=backend,
            workspace=str(context.workspace),
            agent_id=self.agent.agent_id,
            session_id=str(run.session_id),
            capabilities=spec.get("capabilities"),
            network=spec.get("network"),
            tool=run.tool_name,
            # AH-AUDIT-005/008: timeout is authorization-relevant and bound
            # into the approved digest.
            timeout=int(spec.get("timeout", 60)),
        )

    async def _guard_tool(self, run: _ToolRun, request: Any) -> bool:
        """Run the permission guard; a pause or denial sets the item's result.

        Validation errors stay distinct from authorization: malformed actions
        fail before any approval. Returns ``False`` for pause/denial.
        """
        from machina.permissions.broker import ApprovalDenied as _Denied
        from machina.permissions.broker import NeedsApproval as _Needs
        from machina.permissions.broker import permission_broker as _broker

        try:
            await _broker.guard(request)
            run.item["_approval_id"] = request.approval_id or ""
            return True
        except _Needs as _need:
            _ap = _need.approval
            result = (
                f"Needs approval ({_ap.get('request_id')}:{_ap.get('operation')} "
                f"{_ap.get('target')}). The action was NOT executed. "
                "Ask the user to approve it (approvals list/resolve), "
                "or choose another path."
            )
            run.item["result"] = str(result)
            # AH-AUDIT-022: typed pause outcome travels with the tool item into
            # AgentResponse.needs_approval. Schedulers/transports read this —
            # never prose.
            run.item["_needs_approval"] = {
                "request_id": _ap.get("request_id", ""),
                "session_id": _ap.get("session_id", ""),
                "agent_id": _ap.get("agent_id", self.agent.agent_id),
                "turn_id": _ap.get("turn_id", ""),
                "operation": _ap.get("operation", ""),
                "target": _ap.get("target", ""),
                "status": _ap.get("status", "pending"),
                # Epoch creation time: lets the trace layer measure the real
                # human wait at re-pause and at approval resume.
                "created_at": _ap.get("created_at"),
            }
            run.item["elapsed_ms"] = (time.monotonic() - run.start) * 1000
            return False
        except _Denied as _den:
            result = f"Permission denied: {_den.reason}. The action was NOT executed."
            run.item["result"] = str(result)
            run.item["elapsed_ms"] = (time.monotonic() - run.start) * 1000
            return False

    async def _execute_tool(self, run: _ToolRun) -> None:
        """Execute the authorized tool with its deadline and cancellation join."""
        from machina.core.background_tasks import CompletedOnCancellation, await_joined

        def save_result(completed):
            run.item["result"] = str(completed)
            run.item["_failed"] = bool(getattr(completed, "is_error", False)) or str(
                completed
            ).startswith("Error:")
            run.item["elapsed_ms"] = (time.monotonic() - run.start) * 1000
            run.item["_delegation_status"] = getattr(completed, "delegation_status", "")
            if getattr(completed, "outcome_unknown", False):
                run.item["_outcome_unknown"] = True
            from machina.tools.agents import DelegationToolResult

            if isinstance(completed, DelegationToolResult):
                run.item["_needs_approvals"] = completed.needs_approval
                run.item["_delegated_tokens"] = completed.tokens_used

        async def execute():
            try:
                completed = await registry.execute(run.tool_name, **run.tool_args)
            except CompletedOnCancellation as cancellation:
                save_result(cancellation.result)
                raise
            save_result(completed)
            return completed

        execution = asyncio.create_task(execute())
        try:
            result = (
                await execution
                if run.tool_name == "delegate"
                else await asyncio.wait_for(execution, timeout=run.timeout)
            )
        except asyncio.CancelledError:

            async def finish_execution():
                outcomes = await asyncio.gather(execution, return_exceptions=True)
                for error in outcomes:
                    if isinstance(error, Exception):
                        record_failure("agent.cancelled_execution", error)
                if "elapsed_ms" not in run.item:
                    _aid = run.item.get("_approval_id", "")
                    if _aid:
                        run.item["_cancelled"] = True
                        await self.agent._retire_tool_approval(run.item, "cancelled")

            await await_joined(finish_execution())
            raise
        run.result = result
