"""OpenRouter transport — OpenAI-compatible, multi-model, free-tier aware."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from machina.core.config import config
from machina.core.exceptions import ProviderError, RateLimitError, ValidationError
from machina.core.metrics import record_llm_call as _record_llm_call
from machina.core.models import LLMResponse, StreamEvent, ToolDefinition
from machina.core.provider._log import logger
from machina.core.provider.audit import audit_log
from machina.core.provider.base import LLMProvider
from machina.core.provider.caches import (
    _cache_get,
    _cache_key,
    _cache_put,
    _caller_identity,
)
from machina.core.provider.prompt_cache import openrouter_cache_wiring
from machina.core.provider.rate_limit import RateLimitBinding
from machina.core.provider.reasoning import _resolve_effort, normalize_reasoning_effort
from machina.core.provider.streaming import (
    OpenAIStreamState,
    begin_stream_usage,
    complete_stream,
    fail_stream,
    openai_stream_events,
)
from machina.core.provider.transport import (
    ReservationSettlement,
    _is_free_model,
    _log_prompt_cache_breakpoints,
    _post_with_retry,
    _stream_with_retry,
    _tools_payload,
    _truncated_stream_error,
    _validate_messages,
    _validate_params,
)
from machina.core.provider.usage_map import parse_openai_usage
from machina.core.usage import usage_store

# Fallback chain when the requested free model keeps 429ing/404ing.
# Refreshed 2026-10-05 from GET /api/v1/models (all :free). First success wins.
_FREE_FALLBACK_MODELS = (
    "qwen/qwen3.8-27b:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "liquid/lfm-2.5-2.6b:free",
    "cohere/north-mini-code:free",
)


# ---------------------------------------------------------------------------
# OpenRouter provider
# ---------------------------------------------------------------------------
class OpenRouterProvider(LLMProvider, RateLimitBinding):
    """OpenRouter API — OpenAI-compatible, multi-model."""

    owns_usage_reservation = True
    BASE_URL = "https://openrouter.ai/api/v1"

    async def context_window(self, model: str | None = None) -> int:
        from machina.core.context_windows import openrouter_context_window

        return await openrouter_context_window(self.client, model or self.model)

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "openrouter/free",
        reasoning_effort: str | None = None,
    ) -> None:
        self.api_key = api_key or config.get("openrouter_api_key") or ""
        if not self.api_key:
            raise ValidationError("OPENROUTER_API_KEY not set")
        self.model = model
        self._default_effort = normalize_reasoning_effort(reasoning_effort)
        self._rate_limit_key = "openrouter"
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/kiazh/machina",
            },
            timeout=120.0,
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[ToolDefinition] | None = None,
        reasoning_effort: str | None = None,
        session_id: uuid.UUID | None = None,
        agent_id: str | None = None,
        usage_reserved: bool = False,
    ) -> LLMResponse:
        # Input validation
        _validate_messages(messages)
        _validate_params(temperature, max_tokens)

        model = model or self.model
        tool_payload = _tools_payload(tools) if tools else None
        wire_messages, tool_payload, marks = openrouter_cache_wiring(messages, tool_payload, model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": wire_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tool_payload is not None:
            payload["tools"] = tool_payload
        effort = _resolve_effort(reasoning_effort, self._default_effort)
        if effort:
            payload["reasoning"] = {"effort": effort}
        _log_prompt_cache_breakpoints(
            "openrouter", model, system=marks[0], tools=marks[1], tail=marks[2]
        )

        # Check cache before making any HTTP request
        if _is_free_model(model):
            payload["max_tokens"] = min(max_tokens, 1024)
        ckey = _cache_key(
            messages,
            tools,
            f"openrouter:{model}",
            payload,
            _caller_identity(session_id, agent_id),
        )
        cached = _cache_get(ckey)
        if cached is not None:
            logger.debug("cache hit for model=%s", model)
            return cached
        await self._rate_limiter.acquire()

        audit_log("llm_call_start", provider="openrouter", model=model, message_count=len(messages))

        # Reserve usage if session_id and agent_id are provided
        reservation_id = None
        if not usage_reserved and session_id is not None and agent_id is not None:
            reservation_id = await usage_store.reserve(
                session_id,
                agent_id,
                "openrouter",
                model,
                messages,
                tools or [],
                int(payload.get("max_tokens", max_tokens)),
            )
        settlement = ReservationSettlement(reservation_id, usage_store)

        start = time.monotonic()
        candidates = [model]
        if _is_free_model(model):
            # max_tokens was already clamped before the cache key was built.
            for fb in _FREE_FALLBACK_MODELS:
                if fb != model and fb not in candidates:
                    candidates.append(fb)
        last_err: Exception | None = None
        data: dict[str, Any] | None = None
        used_model = model
        cacheable = True
        for cand in candidates:
            payload["model"] = cand
            try:
                resp = await _post_with_retry(self.client, "/chat/completions", payload)
                resp.raise_for_status()
                data = resp.json()
                used_model = cand
                if cand != model:
                    # A fallback candidate's answer must not later masquerade
                    # as the requested model (see N1 cache note below).
                    cacheable = False
                    logger.info("free-tier fallback succeeded with model=%s", cand)
                    audit_log(
                        "llm_free_fallback", provider="openrouter", from_model=model, to_model=cand
                    )
                break
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response is not None else 0
                # 429 = throttled, 404 = retired free id OR no free provider
                # can serve this payload (e.g. tools unsupported) -> next model,
                # and for the last candidate retry once without tools.
                retryable = status in (429, 404) if _is_free_model(cand) else status == 429
                last_err = e
                if status == 404 and payload.get("tools") and cand == candidates[-1]:
                    # Per openrouter/free docs, 404 also means "no provider can
                    # serve the request" — free models often can't do tools.
                    # Retry plain-chat before giving up so `hello` still works.
                    logger.warning("free model %s 404 with tools, retrying without tools", cand)
                    stripped = {k: v for k, v in payload.items() if k != "tools"}
                    try:
                        resp2 = await _post_with_retry(self.client, "/chat/completions", stripped)
                        resp2.raise_for_status()
                        data = resp2.json()
                        used_model = cand
                        # The answer was produced without the tools the cache
                        # key covers, so it must not be cached under that key.
                        cacheable = False
                        audit_log("llm_free_fallback_no_tools", provider="openrouter", model=cand)
                        break
                    except Exception as e2:
                        last_err = e2
                        break
                if not retryable or cand == candidates[-1]:
                    break
                logger.warning(
                    "free model %s got %s, trying fallback %s after 2s",
                    cand,
                    status,
                    candidates[candidates.index(cand) + 1],
                )
                await asyncio.sleep(2.0)
                continue
            except RateLimitError as e:
                # Daily quota is exhausted account-wide: trying other free
                # models cannot succeed, so fail fast with the real reason.
                last_err = e
                break
            except Exception as e:
                last_err = e
                break
        try:
            if data is None:
                raise last_err or RuntimeError("LLM call failed with no response")
        except BaseException as e:
            duration_ms = (time.monotonic() - start) * 1000
            _record_llm_call(model, duration_ms, is_error=True)
            await settlement.fail()
            if not isinstance(e, Exception):
                raise
            audit_log("llm_call_error", provider="openrouter", model=model, error=str(e))
            raise

        duration_ms = (time.monotonic() - start) * 1000
        # OpenRouter can return HTTP 200 with an error body (and some providers
        # return an empty choices list), so raise_for_status() is not enough.
        choices = data.get("choices")
        if not choices or not isinstance(choices, list):
            error = data.get("error") or {}
            detail = error.get("message") if isinstance(error, dict) else str(error)
            message = (
                f"provider returned no choices for {used_model}: "
                f"{detail or data or 'empty response'}"
            )
            _record_llm_call(used_model, duration_ms, is_error=True)
            await settlement.fail()
            audit_log(
                "llm_call_error",
                provider="openrouter",
                model=used_model,
                error="no_choices",
            )
            raise ProviderError(message)

        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content", "")
        tool_calls = message.get("tool_calls", []) or []
        usage = parse_openai_usage(data.get("usage", {}))

        _record_llm_call(
            data.get("model", model),
            duration_ms,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

        # Finish usage reservation
        await settlement.settle(usage, model=data.get("model", model))

        audit_log(
            "llm_call_complete",
            provider="openrouter",
            model=data.get("model", model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            tool_calls=len(tool_calls),
        )

        response = LLMResponse(
            content=content,
            model=data.get("model", model),
            usage=usage,
            raw=data,
            tool_calls=tool_calls,
        )
        if cacheable:
            _cache_put(ckey, response)
        else:
            # N1: fallback-chain (or tools-stripped) answers are intentionally
            # not cached. A cache hit must never present another model's answer
            # under the requested model, and the next call should retry the
            # requested model in case its quota has recovered.
            logger.debug(
                "not caching fallback response for requested model=%s (used %s)",
                model,
                used_model,
            )
        return response

    async def stream_complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[ToolDefinition] | None = None,
        reasoning_effort: str | None = None,
        session_id: uuid.UUID | None = None,
        agent_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream completion via SSE — yields text deltas as they arrive."""
        # Input validation
        _validate_messages(messages)
        _validate_params(temperature, max_tokens)

        # Rate limiting
        await self._rate_limiter.acquire()

        model = model or self.model
        candidates = [model]
        if _is_free_model(model):
            payload_max = min(max_tokens, 1024)
            for fb in _FREE_FALLBACK_MODELS:
                if fb != model and fb not in candidates:
                    candidates.append(fb)
        else:
            payload_max = max_tokens
        tool_payload = _tools_payload(tools) if tools else None
        wire_messages, tool_payload, marks = openrouter_cache_wiring(messages, tool_payload, model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": wire_messages,
            "temperature": temperature,
            "max_tokens": payload_max,
            "stream": True,
        }
        # NOTE: openrouter/free does not advertise stream_options in its
        # capability list — only send include_usage for paid models.
        if not _is_free_model(model):
            # Ask for a final usage chunk so streamed turns report real token counts
            payload["stream_options"] = {"include_usage": True}
        if tool_payload is not None:
            payload["tools"] = tool_payload
        effort = _resolve_effort(reasoning_effort, self._default_effort)
        if effort:
            payload["reasoning"] = {"effort": effort}
        _log_prompt_cache_breakpoints(
            "openrouter", model, system=marks[0], tools=marks[1], tail=marks[2]
        )

        settlement = await begin_stream_usage(
            store=usage_store,
            provider_name="openrouter",
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=payload_max,
            session_id=session_id,
            agent_id=agent_id,
        )
        start = time.monotonic()
        state = OpenAIStreamState(resolved_model=model)

        for cand in candidates:
            payload["model"] = cand
            state = OpenAIStreamState(resolved_model=cand)
            try:
                async with _stream_with_retry(self.client, "/chat/completions", payload) as resp:
                    resp.raise_for_status()
                    async for event in openai_stream_events(resp, state):
                        yield event
                    if not state.saw_terminal:
                        raise _truncated_stream_error("openrouter", state.resolved_model)
                # Success — stop trying fallbacks.
                break
            except Exception as e:
                if isinstance(e, RateLimitError):
                    # Account-wide daily quota exhausted: no free candidate can
                    # succeed, so stop now instead of sleeping through the rest
                    # of the chain and burning more of the quota.
                    await fail_stream(
                        settlement,
                        provider_name="openrouter",
                        audit_model=cand,
                        resolved_model=state.resolved_model,
                        started=start,
                        error=e,
                        audit_event="llm_stream_rate_limited",
                    )
                    raise
                status = None
                if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
                    status = e.response.status_code
                nothing_yielded = not state.content_parts and not state.tool_calls_by_index
                retryable = status in (429, 404) and nothing_yielded and cand != candidates[-1]
                if retryable:
                    logger.warning(
                        "free stream model %s got %s, trying fallback after 2s", cand, status
                    )
                    # Discard any partial state so a throttled candidate cannot
                    # contaminate the next one.
                    state = OpenAIStreamState(resolved_model=cand)
                    try:
                        await asyncio.sleep(2.0)
                    except BaseException as pause_exc:
                        # Cancel/close while waiting for the next fallback
                        # candidate must settle the shared reservation.
                        await fail_stream(
                            settlement,
                            provider_name="openrouter",
                            audit_model=cand,
                            resolved_model=state.resolved_model,
                            started=start,
                            error=pause_exc,
                        )
                        raise
                    continue
                if (
                    status == 404
                    and nothing_yielded
                    and payload.get("tools")
                    and cand == candidates[-1]
                ):
                    # Same "no provider can serve tools" case as non-stream:
                    # retry the last candidate once as plain chat.
                    logger.warning(
                        "free stream model %s 404 with tools, retrying without tools", cand
                    )
                    payload.pop("tools", None)
                    state = OpenAIStreamState(resolved_model=cand)
                    try:
                        async with _stream_with_retry(
                            self.client, "/chat/completions", payload
                        ) as resp2:
                            resp2.raise_for_status()
                            async for event in openai_stream_events(resp2, state):
                                yield event
                            if not state.saw_terminal:
                                raise _truncated_stream_error("openrouter", state.resolved_model)
                        break
                    except Exception as e2:
                        e = e2
                await fail_stream(
                    settlement,
                    provider_name="openrouter",
                    audit_model=cand,
                    resolved_model=state.resolved_model,
                    started=start,
                    error=e,
                )
                raise e
            except BaseException as exc:
                # GeneratorExit/CancelledError at a text yield or inside the
                # SDK stream bypass ``except Exception``; settle before the
                # generator closes so the reservation is not stranded.
                await fail_stream(
                    settlement,
                    provider_name="openrouter",
                    audit_model=cand,
                    resolved_model=state.resolved_model,
                    started=start,
                    error=exc,
                )
                raise

        response = await complete_stream(
            settlement,
            provider_name="openrouter",
            requested_model=model,
            resolved_model=state.resolved_model,
            messages=messages,
            tools=tools,
            content="".join(state.content_parts),
            tool_calls=state.tool_calls(),
            usage=parse_openai_usage(state.stream_usage),
            started=start,
            missing_usage="zero",
            total_tokens="max",
        )
        yield StreamEvent(type="done", response=response)

    async def close(self) -> None:
        # Close the shared client (it will be recreated if needed)
        await self.client.aclose()
