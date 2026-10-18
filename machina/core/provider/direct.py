"""OpenAI-compatible direct transport for every PROVIDER_SPECS family."""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from machina.core.config import config
from machina.core.exceptions import ProviderError, ValidationError
from machina.core.metrics import record_llm_call as _record_llm_call
from machina.core.models import LLMResponse, StreamEvent, ToolDefinition
from machina.core.provider._log import logger
from machina.core.provider.audit import audit_log
from machina.core.provider.base import LLMProvider
from machina.core.provider.caches import _cache_get, _cache_key, _cache_put, _caller_identity
from machina.core.provider.rate_limit import RateLimitBinding
from machina.core.provider.reasoning import _resolve_effort, normalize_reasoning_effort
from machina.core.provider.specs import ProviderSpec
from machina.core.provider.streaming import (
    OpenAIStreamState,
    begin_stream_usage,
    complete_stream,
    fail_stream,
    openai_stream_events,
)
from machina.core.provider.transport import (
    ReservationSettlement,
    _post_with_retry,
    _stream_with_retry,
    _tools_payload,
    _truncated_stream_error,
    _validate_messages,
    _validate_params,
)
from machina.core.provider.usage_map import parse_openai_usage
from machina.core.usage import usage_store


# ---------------------------------------------------------------------------
# Direct provider — one transport for every OpenAI-compatible family
# (OpenAI, DeepSeek, xAI, Groq, Together, Mistral, Google). Driven by a
# ProviderSpec so base URL, key, default model and effort mapping stay
# accurate in one table instead of seven copy-pasted classes.
# ---------------------------------------------------------------------------
class DirectProvider(LLMProvider, RateLimitBinding):
    """OpenAI-compatible chat API for a single vendor (see PROVIDER_SPECS)."""

    def __init__(
        self,
        spec: ProviderSpec,
        api_key: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.spec = spec
        self.api_key = api_key or config.get(spec.config_key) or ""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif spec.requires_key:
            # Hosted vendors fail fast on a missing key. Keyless local
            # endpoints (custom spec) simply send no Authorization header.
            raise ValidationError(f"{spec.api_key_env} not set")
        self.model = model or spec.default_model
        self._default_effort = normalize_reasoning_effort(reasoning_effort, provider=spec.name)
        self._rate_limit_key = spec.name
        self.client = httpx.AsyncClient(
            base_url=spec.base_url,
            headers=headers,
            timeout=120.0,
        )

    def _effort_payload(self, reasoning_effort: str | None) -> dict[str, Any]:
        """Extra body params for the requested effort ({} when unset/unsupported)."""
        effort = _resolve_effort(reasoning_effort, self._default_effort, provider=self.spec.name)
        if effort and self.spec.reasoning == "openai":
            return {"reasoning_effort": effort}
        return {}

    def _cache_key_for(self, messages, tools, model: str, request=None, identity=None) -> str:
        # Namespace by provider so identical model ids on two vendors can't
        # collide, and by endpoint + credential generation so a provider
        # updated to another base URL (or with a rotated key) can never be
        # answered from the previous endpoint's cached body within the TTL
        # (review round 3, F6). The credential enters only as a short digest.
        endpoint = str(self.spec.base_url or "").rstrip("/")
        credential = hashlib.sha256((self.api_key or "").encode("utf-8")).hexdigest()[:16]
        scope = f"{self.spec.name}:{endpoint}:{credential}:{model}"
        return _cache_key(messages, tools, scope, request, identity)

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
    ) -> LLMResponse:
        _validate_messages(messages)
        _validate_params(temperature, max_tokens)

        model = model or self.model
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self._effort_payload(reasoning_effort),
        }
        if tools:
            payload["tools"] = _tools_payload(tools)

        ckey = self._cache_key_for(
            messages, tools, model, payload, _caller_identity(session_id, agent_id)
        )
        cached = _cache_get(ckey)
        if cached is not None:
            logger.debug("cache hit for %s model=%s", self.spec.name, model)
            return cached
        await self._rate_limiter.acquire()

        audit_log(
            "llm_call_start", provider=self.spec.name, model=model, message_count=len(messages)
        )

        reservation_id = None
        if session_id is not None and agent_id is not None:
            reservation_id = await usage_store.reserve(
                session_id, agent_id, self.spec.name, model, messages, tools or [], max_tokens
            )
        settlement = ReservationSettlement(reservation_id, usage_store)

        start = time.monotonic()
        try:
            resp = await _post_with_retry(self.client, "/chat/completions", payload)
            resp.raise_for_status()
            data = resp.json()
        except BaseException as e:
            duration_ms = (time.monotonic() - start) * 1000
            _record_llm_call(model, duration_ms, is_error=True)
            await settlement.fail()
            if not isinstance(e, Exception):
                raise
            audit_log("llm_call_error", provider=self.spec.name, model=model, error=str(e))
            raise

        duration_ms = (time.monotonic() - start) * 1000
        choices = data.get("choices")
        if not choices or not isinstance(choices, list):
            error = data.get("error") or {}
            detail = error.get("message") if isinstance(error, dict) else str(error)
            message = (
                f"provider returned no choices for {model}: {detail or data or 'empty response'}"
            )
            _record_llm_call(model, duration_ms, is_error=True)
            await settlement.fail()
            audit_log("llm_call_error", provider=self.spec.name, model=model, error="no_choices")
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
        await settlement.settle(usage, model=data.get("model", model))
        audit_log(
            "llm_call_complete",
            provider=self.spec.name,
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
        _cache_put(ckey, response)
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
        _validate_messages(messages)
        _validate_params(temperature, max_tokens)
        await self._rate_limiter.acquire()

        model = model or self.model
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **self._effort_payload(reasoning_effort),
        }
        # stream_options.include_usage is only sent for vendors whose docs
        # confirm it — others 400 on the param.
        if self.spec.stream_usage:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = _tools_payload(tools)

        settlement = await begin_stream_usage(
            store=usage_store,
            provider_name=self.spec.name,
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            session_id=session_id,
            agent_id=agent_id,
        )
        start = time.monotonic()
        state = OpenAIStreamState(resolved_model=model)
        try:
            async with _stream_with_retry(self.client, "/chat/completions", payload) as resp:
                resp.raise_for_status()
                async for event in openai_stream_events(resp, state):
                    yield event
            if not state.saw_terminal:
                raise _truncated_stream_error(self.spec.name, state.resolved_model)
        except BaseException as exc:
            # GeneratorExit/CancelledError are BaseException: settle before
            # re-raising so an early close cannot strand the reservation.
            await fail_stream(
                settlement,
                provider_name=self.spec.name,
                audit_model=model,
                resolved_model=state.resolved_model,
                started=start,
                error=exc,
            )
            raise

        response = await complete_stream(
            settlement,
            provider_name=self.spec.name,
            requested_model=model,
            resolved_model=state.resolved_model,
            messages=messages,
            tools=tools,
            content="".join(state.content_parts),
            tool_calls=state.tool_calls(),
            usage=parse_openai_usage(state.stream_usage),
            started=start,
            missing_usage="estimate",
            total_tokens="sum",
        )
        yield StreamEvent(type="done", response=response)

    async def close(self) -> None:
        await self.client.aclose()
