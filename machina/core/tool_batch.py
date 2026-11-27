"""Concurrent tool-batch execution: validate, authorize, run, record.

One assistant message may emit several tool calls. This module owns the batch
contract: request-order starts, bounded concurrency, per-tool timeouts, broker
authorization, result recording in provider order, and cancellation cleanup.
The agent facade delegates here; the executor sees only the agent surface it
needs through ``self.agent`` until the dependency interface is narrowed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import nullcontext
from typing import Any

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

__all__ = ["ToolBatchExecutor"]


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


class ToolBatchExecutor:
    """Execute one assistant tool batch for a bound agent."""

    def __init__(self, agent: Any) -> None:
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

        Thin wrapper over :meth:`_execute_tool_calls_stream` (the single
        implementation) that prints progress when *verbose* is set.
        """
        async with joined_teardown(
            stream=self.agent._execute_tool_calls_stream(
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
        Every ``tool_call`` event is emitted up front, then results are
        replayed in the original order so the transcript stays deterministic.

        If *pending_chunks* is provided, tool_call chunks are accumulated there
        instead of being inserted immediately. The caller is responsible for
        flushing them via ``context_manager.add_chunks_batch``.
        """
        # S3.3 BEGIN tool-trace hook (revert: delete 4-line block; observe-only)
        _s3trace: Any = None
        try:
            from machina.controller import wiring as _s3trace
        except Exception:
            _s3trace = None
        # S3.3 END tool-trace hook
        # ── Phase A: parse and validate the whole batch ─────────────────────
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

            if self.agent.allowed_tools is not None and tool_name not in self.agent.allowed_tools:
                result_str = (
                    f"Error: tool '{tool_name}' is not allowed for agent '{self.agent.agent_id}'"
                )
                audit_log(
                    "tool_call_denied",
                    session_id=str(session_id),
                    tool_name=tool_name,
                    agent_id=self.agent.agent_id,
                )
                planned.append(
                    {
                        "tc": tc,
                        "name": tool_name,
                        "args": tool_args,
                        "result": result_str,
                        "call_id": call_id,
                    }
                )
                continue

            # A model can retain a schema loaded earlier in the turn (for
            # example via tool_load) after the current step has narrowed the
            # surface. Enforce the schema actually advertised for this step,
            # not just the agent's static allow-list.
            if allowed_tool_names is not None and tool_name not in allowed_tool_names:
                result_str = (
                    f"Error: tool '{tool_name}' is not in the current tool surface. "
                    "Use only tools advertised for this step."
                )
                audit_log(
                    "tool_call_denied",
                    session_id=str(session_id),
                    tool_name=tool_name,
                    agent_id=self.agent.agent_id,
                    reason="not_in_tool_surface",
                )
                planned.append(
                    {
                        "tc": tc,
                        "name": tool_name,
                        "args": tool_args,
                        "result": result_str,
                        "call_id": call_id,
                    }
                )
                continue

            planned.append(
                {"tc": tc, "name": tool_name, "args": tool_args, "result": None, "call_id": call_id}
            )

        # ── Phase B: emit starts BEFORE execution (AH-020) ────────────────
        # Long-running tools must appear as running while executing, not only
        # after gather() finishes. Starts are emitted in request order with
        # stable call IDs; completions follow as each finishes.
        for item in planned:
            if item["name"] is None:
                continue
            # Denied tools (result already set) still get start+complete for
            # UI consistency — they complete immediately below.
            yield StreamEvent(
                type="tool_call",
                tool_name=item["name"],
                tool_args=item["args"],
                tool_call_id=item["call_id"],
            )

        # ── Phase C: execute concurrently, yielding completions as they finish
        async def _run_one(item: dict[str, Any]) -> None:
            tool_name = item["name"]
            tool_args = item["args"]
            audit_log("tool_call_start", session_id=str(session_id), tool_name=tool_name)
            start = time.monotonic()
            tool_timeout = _TOOL_TIMEOUTS.get(tool_name, _TOOL_TIMEOUT_DEFAULT)
            try:
                with span("agent.tool", tool_name=tool_name, session_id=str(session_id)):
                    await plugin_registry.dispatch("on_tool_call", tool_name, tool_args)
                    # AH-018: bind caller scope around EVERY tool (not just a
                    # subset) so RAG tools and future tools see the active
                    # session/agent. Tokens always reset in finally.
                    from machina.tools.agents import current_agent_id, current_session_id

                    token = current_session_id.set(session_id)
                    agent_token = current_agent_id.set(self.agent.agent_id)
                    # LP-08: publish this agent's authority for delegation
                    # children (restored afterwards like all caller scope).
                    try:
                        from machina.core.agent_factory import check_mode_cap, merge_authority
                        from machina.core.agent_factory import parent_authority_var as _pav

                        # R-02: Preserve inherited authority (max_mode, etc.)
                        # and intersect its tools with this agent's definition.
                        _inherited = dict(getattr(self, "_authority", {}) or {})
                        _ambient = _pav.get() or {}
                        if _ambient.get("max_mode") is not None:
                            check_mode_cap(_inherited, _ambient["max_mode"])
                        _merged = merge_authority(_inherited, _ambient)
                        _parent_tools = _merged.get("tools")
                        # Terminal authority includes observing/stopping its
                        # own session jobs; this adds no launch capability.
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
                            _merged["tools"] = [
                                t for t in self.agent.allowed_tools if t in _parent_tools
                            ]
                        authority_token = _pav.set(_merged)
                    except Exception:
                        current_agent_id.reset(agent_token)
                        current_session_id.reset(token)
                        raise
                    try:
                        # Permission broker gates every tool path.
                        # Validation errors stay distinct from authorization:
                        # malformed actions fail here before any approval.
                        from machina.permissions import tools as _perm_tools
                        from machina.permissions.broker import (
                            ApprovalDenied as _Denied,
                        )
                        from machina.permissions.broker import NeedsApproval as _Needs
                        from machina.permissions.broker import permission_broker as _broker
                        from machina.permissions.policy import build_request as _build_req

                        if tool_name in {"mcp_call", "mcp_list", "mcp_describe"}:
                            from machina.core.runtime import runtime_services

                            await runtime_services.get_connector_service().refresh()
                        _spec = _perm_tools.action_for_tool(tool_name, tool_args)
                        if tool_name in {"mcp_call", "mcp_connect", "mcp_disconnect"}:
                            tool_timeout = int(_spec.get("timeout", 60)) + 1
                        elif tool_name == "terminal":
                            # Background authorization has no execution deadline;
                            # admission still gets a normal startup window.
                            tool_timeout = (
                                60
                                if tool_args.get("background") is True
                                else min(max(int(_spec.get("timeout", 60)), 1), _TERMINAL_CAP)
                            )
                        from machina.core.execution_context import resolve_execution_context

                        _context = await resolve_execution_context(str(session_id), tool_name)
                        _mode, _backend = _context.mode, _context.backend
                        # Children inherit the parent's current execution mode,
                        # including sandbox isolation, rather than a global default.
                        check_mode_cap(_merged, _mode)
                        _merged["max_mode"] = _mode
                        if tool_name == "terminal" and _merged.get("terminal_backend") == "sandbox":
                            _backend = "sandbox"
                        if _mode == "sandbox" or _backend == "sandbox":
                            _merged["terminal_backend"] = "sandbox"
                        _pav.set(_merged)
                        _req = _build_req(
                            operation=_spec.get("operation", f"tool.{tool_name}"),
                            targets=_spec.get("targets"),
                            argv=_spec.get("argv"),
                            shell_payload=_spec.get("shell_payload", ""),
                            cwd=_spec.get("cwd", "."),
                            content=_spec.get("content", ""),
                            mode=_mode,
                            backend=_backend,
                            workspace=str(_context.workspace),
                            agent_id=self.agent.agent_id,
                            session_id=str(session_id),
                            capabilities=_spec.get("capabilities"),
                            network=_spec.get("network"),
                            tool=tool_name,
                            # AH-AUDIT-005/008: timeout is authorization-
                            # relevant and bound into the approved digest.
                            timeout=int(_spec.get("timeout", 60)),
                        )
                        try:
                            await _broker.guard(_req)
                            item["_approval_id"] = _req.approval_id or ""
                        except _Needs as _need:
                            _ap = _need.approval
                            result = (
                                f"Needs approval ({_ap.get('request_id')}:{_ap.get('operation')} "
                                f"{_ap.get('target')}). The action was NOT executed. "
                                "Ask the user to approve it (approvals list/resolve), "
                                "or choose another path."
                            )
                            item["result"] = str(result)
                            # AH-AUDIT-022: typed pause outcome travels with
                            # the tool item into AgentResponse.needs_approval.
                            # Schedulers/transports read this — never prose.
                            item["_needs_approval"] = {
                                "request_id": _ap.get("request_id", ""),
                                "session_id": _ap.get("session_id", ""),
                                "agent_id": _ap.get("agent_id", self.agent.agent_id),
                                "turn_id": _ap.get("turn_id", ""),
                                "operation": _ap.get("operation", ""),
                                "target": _ap.get("target", ""),
                                "status": _ap.get("status", "pending"),
                                # Epoch creation time: lets the trace layer
                                # measure the real human wait at re-pause and
                                # at approval resume.
                                "created_at": _ap.get("created_at"),
                            }
                            item["elapsed_ms"] = (time.monotonic() - start) * 1000
                            return
                        except _Denied as _den:
                            result = (
                                f"Permission denied: {_den.reason}. The action was NOT executed."
                            )
                            item["result"] = str(result)
                            item["elapsed_ms"] = (time.monotonic() - start) * 1000
                            return
                        try:
                            from machina.core.background_tasks import (
                                CompletedOnCancellation,
                                await_joined,
                            )

                            def save_result(completed):
                                item["result"] = str(completed)
                                item["_failed"] = bool(
                                    getattr(completed, "is_error", False)
                                ) or str(completed).startswith("Error:")
                                item["elapsed_ms"] = (time.monotonic() - start) * 1000
                                item["_delegation_status"] = getattr(
                                    completed, "delegation_status", ""
                                )
                                if getattr(completed, "outcome_unknown", False):
                                    item["_outcome_unknown"] = True
                                from machina.tools.agents import DelegationToolResult

                                if isinstance(completed, DelegationToolResult):
                                    item["_needs_approvals"] = completed.needs_approval
                                    item["_delegated_tokens"] = completed.tokens_used

                            async def execute():
                                try:
                                    completed = await registry.execute(tool_name, **tool_args)
                                except CompletedOnCancellation as cancellation:
                                    save_result(cancellation.result)
                                    raise
                                save_result(completed)
                                return completed

                            execution = asyncio.create_task(execute())
                            result = (
                                await execution
                                if tool_name == "delegate"
                                else await asyncio.wait_for(execution, timeout=tool_timeout)
                            )
                        except asyncio.CancelledError:

                            async def finish_execution():
                                outcomes = await asyncio.gather(execution, return_exceptions=True)
                                for error in outcomes:
                                    if isinstance(error, Exception):
                                        record_failure("agent.cancelled_execution", error)
                                if "elapsed_ms" not in item:
                                    _aid = item.get("_approval_id", "")
                                    if _aid:
                                        item["_cancelled"] = True
                                        await self.agent._retire_tool_approval(item, "cancelled")

                            await await_joined(finish_execution())
                            raise
                    finally:
                        try:
                            # AH-AUDIT-002: end the guard+execute window so a
                            # later tool call cannot inherit this approval's
                            # snapshot.
                            from machina.permissions.broker import (
                                clear_execution_context as _clear_ctx,
                            )

                            _clear_ctx()
                        except Exception as _boundary_error:
                            # Optional fallback preserves the primary outcome; report no payload.
                            record_failure("agent._execute_tool_calls_stream", _boundary_error)
                        try:
                            if authority_token is not None:
                                from machina.core.agent_factory import parent_authority_var as _pav2

                                _pav2.reset(authority_token)
                        except Exception as _boundary_error:
                            # Optional fallback preserves the primary outcome; report no payload.
                            record_failure("agent._execute_tool_calls_stream", _boundary_error)
                        current_agent_id.reset(agent_token)
                        current_session_id.reset(token)
            except DelegationAccountingError:
                # Unknown child spend is fatal for this turn, not a recoverable
                # tool failure that permits another provider call.
                raise
            except Exception as e:
                if isinstance(e, asyncio.TimeoutError) and "elapsed_ms" in item:
                    return
                if isinstance(e, asyncio.TimeoutError):
                    # R4-019: wait_for raises a message-less TimeoutError; type it.
                    error_text = f"tool '{tool_name}' timed out after {tool_timeout:g}s"
                else:
                    error_text = str(e)
                logger.exception("Tool execution failed for '%s'", tool_name)
                audit_log(
                    "tool_call_error",
                    session_id=str(session_id),
                    tool_name=tool_name,
                    error=error_text,
                    duration_ms=int((time.monotonic() - start) * 1000),
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
            item["elapsed_ms"] = (time.monotonic() - start) * 1000

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
                    with _s3trace.trace_tool_call(item) if _s3trace else nullcontext() as _trace:
                        await _run_one(item)
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
                        if _s3trace is not None:
                            _s3trace.record_approval_pause(_pause_c)
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

        # ── Phase D: metrics for denied + persistence in provider order ───
        # Provider message order must follow the original tool_calls order
        # (OpenAI requires tool replies in order), even though UI completions
        # above may have arrived out of order. A cancellation here still
        # finalizes any completed-but-unrecorded action (F02).
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
