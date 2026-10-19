"""Shared streaming lifecycle for provider transports.

The transports differ only in payload shaping and wire-chunk decoding. The
accounting contract around the decoder is identical everywhere: reserve
before the first byte, settle on success, fail on every other exit (including
generator close), and emit exactly one terminal ``done`` event. Keeping that
contract here means policy changes land once and the god-file ratchets only
fall.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from machina.core.exceptions import ProviderError
from machina.core.metrics import record_llm_call
from machina.core.models import LLMResponse, StreamEvent, ToolDefinition
from machina.core.provider._log import logger
from machina.core.provider.audit import audit_log
from machina.core.provider.sse import sse_data
from machina.core.provider.transport import ReservationSettlement, _stream_error_message
from machina.core.usage import _estimate_tokens


@dataclass
class OpenAIStreamState:
    """Accumulated state of one OpenAI-compatible SSE stream."""

    resolved_model: str
    content_parts: list[str] = field(default_factory=list)
    tool_calls_by_index: dict[int, dict[str, str]] = field(default_factory=dict)
    stream_usage: dict[str, Any] = field(default_factory=dict)
    saw_terminal: bool = False

    def add_tool_call_delta(self, index: int, delta: dict[str, Any]) -> None:
        """Fold one incremental tool-call delta into its indexed entry."""
        entry = self.tool_calls_by_index.get(index)
        if entry is None:
            entry = {"id": "", "name": "", "arguments": ""}
            self.tool_calls_by_index[index] = entry
        if delta.get("id"):
            entry["id"] = delta["id"]
        function = delta.get("function") or {}
        if function.get("name"):
            entry["name"] = function["name"]
        arguments = function.get("arguments")
        if arguments:
            if isinstance(arguments, dict):
                # Some gateways emit structured args; stringify before the
                # streamed fragments are concatenated.
                arguments = json.dumps(arguments)
            entry["arguments"] += arguments

    def tool_calls(self) -> list[dict[str, Any]]:
        """Finalize streamed deltas into the OpenAI tool-call shape."""
        return [
            {
                "id": entry["id"],
                "type": "function",
                "function": {
                    "name": entry["name"],
                    "arguments": entry["arguments"] or "{}",
                },
            }
            for _, entry in sorted(self.tool_calls_by_index.items())
        ]


async def openai_stream_events(
    response: httpx.Response,
    state: OpenAIStreamState,
    *,
    add_tool_call: Callable[[int, dict[str, Any]], None] | None = None,
    stop_on_done: bool = True,
    track_model: bool = True,
) -> AsyncGenerator[StreamEvent, None]:
    """Yield text deltas from an OpenAI-compatible SSE body, filling *state*.

    ``[DONE]`` ends the body for direct/openrouter, while Copilot historically
    keeps reading (``stop_on_done=False``). Copilot also keeps the requested
    model (``track_model=False``) and supplies its own tool-call accumulator.
    """
    add = add_tool_call or state.add_tool_call_delta
    async for line in response.aiter_lines():
        data = sse_data(line)
        if data is None:
            continue
        data = data.strip()
        if not data:
            continue
        if data == "[DONE]":
            state.saw_terminal = True
            if stop_on_done:
                break
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        if chunk.get("error"):
            # Explicit error frame: fail the stream honestly instead of
            # reporting a truncated success (HTTP 200 + error body is legal).
            raise ProviderError(_stream_error_message(chunk, state.resolved_model))

        if track_model:
            state.resolved_model = chunk.get("model") or state.resolved_model
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            state.stream_usage = usage
        choices = chunk.get("choices")
        choice = (
            choices[0]
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else None
        )
        if choice is None:
            continue
        if choice.get("finish_reason"):
            state.saw_terminal = True
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        text = delta.get("content")
        if isinstance(text, str) and text:
            state.content_parts.append(text)
            yield StreamEvent(type="text", content=text)
        for call in delta.get("tool_calls") or []:
            if isinstance(call, dict):
                index = call.get("index", 0)
                add(index if isinstance(index, int) else 0, call)


async def begin_stream_usage(
    *,
    store: Any,
    provider_name: str,
    model: str,
    messages: list[dict[str, str]],
    tools: list[ToolDefinition] | None,
    max_tokens: int,
    session_id: uuid.UUID | None,
    agent_id: str | None,
) -> ReservationSettlement:
    """Audit a stream start and reserve its usage when attributable.

    ``store`` is the provider module's ``usage_store`` binding rather than a
    captured import, so the existing monkeypatch seam keeps working.
    """
    audit_log("llm_stream_start", provider=provider_name, model=model, message_count=len(messages))
    reservation_id = None
    if session_id is not None and agent_id is not None:
        reservation_id = await store.reserve(
            session_id, agent_id, provider_name, model, messages, tools or [], max_tokens
        )
    return ReservationSettlement(reservation_id, store)


async def fail_stream(
    settlement: ReservationSettlement,
    *,
    provider_name: str,
    audit_model: str,
    resolved_model: str,
    started: float,
    error: BaseException,
    audit_event: str = "llm_stream_error",
) -> None:
    """Account a failed or early-closed stream, then release its reservation.

    ``GeneratorExit``/``CancelledError`` are ``BaseException`` and must settle
    before re-raising, but they are not failures worth an error audit line.
    """
    record_llm_call(resolved_model, (time.monotonic() - started) * 1000, is_error=True)
    await settlement.fail()
    if isinstance(error, Exception):
        audit_log(audit_event, provider=provider_name, model=audit_model, error=str(error))


async def complete_stream(
    settlement: ReservationSettlement,
    *,
    provider_name: str,
    requested_model: str,
    resolved_model: str,
    messages: list[dict[str, str]],
    tools: list[ToolDefinition] | None,
    content: str,
    tool_calls: list[dict[str, Any]],
    usage: dict[str, Any],
    started: float,
    missing_usage: str = "keep",
    total_tokens: str = "keep",
    audit_model: str | None = None,
    debug_label: str | None = None,
    raw: dict[str, Any] | None = None,
) -> LLMResponse:
    """Finalize a completed stream: usage fallback, metric, settle, audit.

    ``missing_usage`` selects the no-usage policy: ``estimate`` (token
    estimate, ``estimated`` flag), ``zero`` (report nothing), or ``keep``
    (provider-specific parser already normalized it). ``total_tokens`` picks
    the total normalization: ``sum`` (always prompt+completion), ``max``
    (keep a larger vendor total), or ``keep``.
    """
    if not usage.get("prompt_tokens") and not usage.get("completion_tokens"):
        if missing_usage == "estimate":
            usage = {
                "prompt_tokens": _estimate_tokens(messages, tools or [], 0),
                "completion_tokens": _estimate_tokens(
                    [{"role": "assistant", "content": content, "tool_calls": tool_calls}],
                    [],
                    0,
                ),
                "estimated": True,
            }
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        elif missing_usage == "zero":
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if total_tokens == "sum":
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    elif total_tokens == "max":
        usage["total_tokens"] = max(
            usage.get("total_tokens", 0), usage["prompt_tokens"] + usage["completion_tokens"]
        )
    record_llm_call(
        resolved_model,
        (time.monotonic() - started) * 1000,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
    )
    await settlement.settle(usage, model=resolved_model)
    audit_log(
        "llm_stream_complete",
        provider=provider_name,
        model=audit_model or requested_model,
        tool_calls=len(tool_calls),
    )
    if debug_label is not None and logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "%s stream complete model=%s tool_calls=%d",
            debug_label,
            resolved_model,
            len(tool_calls),
        )
    return LLMResponse(
        content=content,
        model=resolved_model,
        usage=usage,
        raw=raw or {},
        tool_calls=tool_calls,
    )
