"""Shared transport plumbing for every provider: retry/quota policy, SSE
helpers, and wire-payload shaping. Stateless by design."""

from __future__ import annotations

import asyncio
import datetime
import random
import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from machina.core.config import MAX_TOKENS
from machina.core.exceptions import ProviderError, RateLimitError, ValidationError
from machina.core.models import ToolDefinition
from machina.core.provider._log import logger
from machina.observability.diagnostics import record_failure
from machina.observability.instrumentation import instrumented_provider

# The provider retries 429s a small fixed number of times; the agent layer
# must NOT retry 429s again (see machina.core.agent._is_rate_limit_error), or one
# rate limit would fan out into dozens of requests.
# Free-tier models (openrouter/free, *:free) get more retries + longer waits
# because OpenRouter throttles them aggressively.
_MAX_RETRIES = 2
_FREE_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 1.0  # seconds; doubles each attempt
_FREE_BASE_DELAY = 4.0  # free tier starts slower

# Transient TLS/socket failures (typically a stale keepalive socket surfacing
# ``SSLV3_ALERT_BAD_RECORD_MAC``) get their own small budget, so they neither
# consume nor extend the 429 budget. httpcore closes the affected connection
# when a transport exception escapes, so every retry opens a fresh socket.
# Timeouts are excluded below: their connect/read budgets already bound the
# wait, and retrying them would silently multiply those timeouts. Certificate
# verification failures stay fatal too — a retry cannot repair a bad cert.
_MAX_TRANSPORT_RETRIES = 2
_TRANSPORT_BASE_DELAY = 0.5  # seconds; doubles each attempt, then jittered
_TRANSPORT_MAX_DELAY = 4.0


def _is_free_model(model: str) -> bool:
    m = (model or "").lower()
    return m == "openrouter/free" or m.endswith((":free", "/free"))


def _is_retryable_transport_error(error: BaseException) -> bool:
    """True when a fresh connection could plausibly succeed after *error*.

    Raw ``ssl.SSLError`` can escape a body read unwrapped (httpcore maps
    handshake errors to ``ConnectError`` but not every read-side TLS failure),
    so both the SSL and httpx transport hierarchies are covered. Certificate
    verification failures are configuration errors: they are inspected through
    the cause chain and never retried.
    """
    seen: set[int] = set()
    cause: BaseException | None = error
    while cause is not None and id(cause) not in seen:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return False
        seen.add(id(cause))
        cause = cause.__cause__ or cause.__context__
    if isinstance(error, httpx.TimeoutException):
        return False
    return isinstance(error, (ssl.SSLError, httpx.TransportError))


def _transport_retry_delay(attempt: int) -> float:
    """Bounded exponential backoff with jitter for transient transport errors."""
    delay = min(_TRANSPORT_BASE_DELAY * (2**attempt), _TRANSPORT_MAX_DELAY)
    return delay + random.uniform(0, 0.25)


def _quota_exhausted(resp: httpx.Response) -> bool:
    """True when OpenRouter reports the *account-wide daily* free quota is gone.

    ``X-RateLimit-Remaining: 0`` means no free model will serve this account
    until the window resets — the cap is account-wide, not per model. Rotating
    through the fallback chain cannot help and only burns more requests, so
    callers must stop immediately instead of trying the next candidate.
    """
    try:
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining is not None and remaining.strip().isdigit():
            return int(remaining.strip()) == 0
    except Exception as _boundary_error:
        # Optional fallback preserves the primary outcome; report no payload.
        record_failure("provider._quota_exhausted", _boundary_error)
    return False


def _quota_reset_at(resp: httpx.Response) -> float | None:
    """Parse ``X-RateLimit-Reset`` (epoch milliseconds) into epoch seconds."""
    try:
        raw = resp.headers.get("x-ratelimit-reset", "")
        if raw and raw.strip().isdigit():
            value = int(raw.strip())
            # Heuristic: 13+ digits is milliseconds, fewer is already seconds.
            return value / 1000.0 if value > 10_000_000_000 else float(value)
    except Exception as _boundary_error:
        # Optional fallback preserves the primary outcome; report no payload.
        record_failure("provider._quota_reset_at", _boundary_error)
    return None


def _quota_message(resp: httpx.Response, model: str) -> str:
    """Human-readable exhaustion message including the reset time (local)."""
    limit = resp.headers.get("x-ratelimit-limit", "").strip() or "?"
    reset_at = _quota_reset_at(resp)
    when = ""
    if reset_at is not None:
        local = datetime.datetime.fromtimestamp(reset_at).astimezone()
        when = f" Quota resets {local.strftime('%Y-%m-%d %H:%M %Z')}."
    return (
        f"OpenRouter free-tier daily quota exhausted ({limit} requests/day, "
        f"account-wide).{when} Every free model returns 429 until then — "
        f"purchase credits for 1000/day, or switch to a paid model."
    )


def _retry_delay(attempt: int, resp: httpx.Response | None, free: bool) -> float:
    # Honor Retry-After when present: delta-seconds or an HTTP-date.
    if resp is not None:
        raw = ""
        try:
            raw = (resp.headers.get("retry-after") or "").strip()
        except Exception as _boundary_error:
            # Optional fallback preserves the primary outcome; report no payload.
            record_failure("provider._retry_delay", _boundary_error)
        if raw:
            if raw.isdigit():
                return min(float(raw), 60.0)
            try:
                from email.utils import parsedate_to_datetime

                when = parsedate_to_datetime(raw)
                if when is not None:
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=datetime.UTC)
                    delta = (when - datetime.datetime.now(datetime.UTC)).total_seconds()
                    return min(max(delta, 0.0), 60.0)
            except (TypeError, ValueError, OverflowError) as _boundary_error:
                # Not a valid HTTP-date either — fall through to plain backoff.
                record_failure("provider._retry_delay", _boundary_error)
    base = _FREE_BASE_DELAY if free else _RETRY_BASE_DELAY
    delay = base * (2**attempt)
    delay = min(delay, 30.0 if free else 8.0)
    return delay + random.uniform(0, 1.0)  # jitter so parallel turns don't thunder


@instrumented_provider("request")
async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    *,
    retry_429: bool = True,
) -> httpx.Response:
    """POST with exponential backoff on 429s and transient transport failures.

    Paid models: retries 429s 2x (1s, 2s) and transient TLS/socket failures 2x
    on a fresh connection. Free models: ONE attempt per model for 429s — the
    caller walks the fallback chain instead; retrying the same throttled free
    model 6x is what burns the per-minute quota (36 POSTs per hello). Transport
    retries are separate from the 429 budget and apply to both tiers.

    Scope is inference POSTs only (the idempotent, side-effect-free requests
    routed through this helper); tool and connector calls never pass here.
    """
    model = str(payload.get("model", ""))
    free = _is_free_model(model)
    # Free: single attempt; fallback loop handles rotation. Paid: 2 retries.
    max_retries = 0 if free else _MAX_RETRIES
    transport_attempt = 0
    attempt = 0
    while True:
        try:
            resp = await client.post(url, json=payload)
        except Exception as exc:
            # No complete response was received. Retry only transport failures
            # that a fresh connection may fix; timeouts/cert errors stay fatal.
            if transport_attempt >= _MAX_TRANSPORT_RETRIES or not _is_retryable_transport_error(
                exc
            ):
                raise
            delay = _transport_retry_delay(transport_attempt)
            transport_attempt += 1
            logger.warning(
                "transient transport error (%s, model=%s), retrying in %.1fs "
                "(transport attempt %d/%d).",
                type(exc).__name__,
                model,
                delay,
                transport_attempt,
                _MAX_TRANSPORT_RETRIES,
            )
            await asyncio.sleep(delay)
            continue
        if resp.status_code != 429 or not retry_429:
            return resp
        # Account-wide daily quota gone: no free model can serve this, so
        # rotating candidates or sleeping would only waste more requests.
        if free and _quota_exhausted(resp):
            raise RateLimitError(
                _quota_message(resp, model),
                reset_at=_quota_reset_at(resp),
                daily_limit=int(resp.headers.get("x-ratelimit-limit") or 0) or None,
                remaining=0,
            )
        if attempt >= max_retries:
            raise httpx.HTTPStatusError(
                f"429 Too Many Requests (attempt {attempt + 1}/{max_retries + 1})",
                request=resp.request,
                response=resp,
            )
        delay = _retry_delay(attempt, resp, free)
        attempt += 1
        logger.warning(
            "429 rate limited (model=%s), retrying in %.1fs (attempt %d/%d). "
            "Free tier: wait or /model <paid> to skip the queue.",
            model,
            delay,
            attempt,
            max_retries + 1,
        )
        await asyncio.sleep(delay)


@asynccontextmanager
@instrumented_provider("stream")
async def _stream_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    *,
    retry_429: bool = True,
    transport_retries: int = _MAX_TRANSPORT_RETRIES,
) -> AsyncGenerator[httpx.Response, None]:
    """Apply the POST retry policy before exposing any streaming content.

    Only failures while *opening* the stream (connect/handshake/headers) are
    retried here. Once the response is yielded, a body failure belongs to the
    caller, which alone knows whether tokens already reached the user; a
    caller that wants pre-first-token body retries passes ``transport_retries=0``
    and runs its own attempt loop.

    The response is opened explicitly (``build_request`` + ``send``) instead of
    ``client.stream`` so a failed open is retried without entering the response
    context, while a consumer exception still closes the response exactly once.
    """
    model = str(payload.get("model", ""))
    free = _is_free_model(model)
    max_retries = 0 if free else _MAX_RETRIES
    transport_attempt = 0
    attempt = 0
    while True:
        request = client.build_request("POST", url, json=payload)
        try:
            resp = await client.send(request, stream=True)
        except Exception as exc:
            if transport_attempt >= transport_retries or not _is_retryable_transport_error(exc):
                raise
            delay = _transport_retry_delay(transport_attempt)
            transport_attempt += 1
            logger.warning(
                "transient stream transport error (%s, model=%s), retrying in %.1fs "
                "(transport attempt %d/%d).",
                type(exc).__name__,
                model,
                delay,
                transport_attempt,
                transport_retries,
            )
            await asyncio.sleep(delay)
            continue
        try:
            if resp.status_code != 429 or not retry_429:
                yield resp
                return
            if free and _quota_exhausted(resp):
                raise RateLimitError(
                    _quota_message(resp, model),
                    reset_at=_quota_reset_at(resp),
                    daily_limit=int(resp.headers.get("x-ratelimit-limit") or 0) or None,
                    remaining=0,
                )
            if attempt >= max_retries:
                resp.raise_for_status()
                raise httpx.HTTPStatusError(
                    f"429 Too Many Requests (attempt {attempt + 1}/{max_retries + 1})",
                    request=resp.request,
                    response=resp,
                )
            delay = _retry_delay(attempt, resp, free)
            attempt += 1
        finally:
            await resp.aclose()
        # Close the rejected response before waiting or opening another stream.
        logger.warning("429 rate limited (model=%s), retrying stream in %.1fs", model, delay)
        await asyncio.sleep(delay)


def _stream_error_message(chunk: dict[str, Any], model: str) -> str:
    """Human-readable message for an explicit mid-stream provider error frame.

    OpenAI-compatible gateways sometimes send HTTP 200 and then an
    ``{"error": ...}`` SSE frame instead of choices. Treating that as a benign
    keep-alive would report a truncated stream as a successful completion.
    """
    error = chunk.get("error")
    if isinstance(error, dict):
        detail = error.get("message") or error.get("type") or str(error)
    else:
        detail = str(error)
    return f"provider stream error for {model}: {detail}"


def _truncated_stream_error(provider: str, model: str) -> ProviderError:
    """Clean EOF without ``[DONE]`` or a ``finish_reason`` frame.

    A clean EOF is not a completion: reporting ``done`` for a truncated
    stream would persist partial content as a successful turn.
    """
    return ProviderError(
        f"{provider} stream ended without a terminal frame ([DONE] or finish_reason) for {model}"
    )


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------
def _validate_messages(messages: list[dict[str, str]]) -> None:
    """Validate LLM message format."""
    if not isinstance(messages, list) or not messages:
        raise ValidationError("messages must be a non-empty list")
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ValidationError(f"Message at index {i} must be a dict")
        if "role" not in msg:
            raise ValidationError(f"Message at index {i} missing 'role' field")
        if "content" not in msg:
            raise ValidationError(f"Message at index {i} missing 'content' field")


def _validate_params(temperature: float, max_tokens: int) -> None:
    """Validate LLM call parameters against the shared output ceiling."""
    if not (0.0 <= temperature <= 2.0):
        raise ValidationError(f"temperature must be between 0.0 and 2.0, got {temperature}")
    if not (1 <= max_tokens <= MAX_TOKENS):
        raise ValidationError(f"max_tokens must be between 1 and {MAX_TOKENS}, got {max_tokens}")


def _tools_payload(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert tool definitions to the OpenAI-compatible function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


class ReservationSettlement:
    """Exactly-once settlement for a provider usage reservation.

    Providers reserve before opening a stream and must settle on every exit:
    success, error, cancellation, or generator close. ``GeneratorExit`` and
    ``CancelledError`` are ``BaseException`` and bypass an ``except Exception``
    handler, so streaming transports call :meth:`settle` on the success path
    and :meth:`fail` from ``finally``. The local guard (not the ledger's
    ``status='reserved'`` predicate) ensures a late close after a successful
    finish cannot issue a second write.
    """

    __slots__ = ("_settled", "_store", "reservation_id")

    def __init__(self, reservation_id, store=None) -> None:
        self.reservation_id = reservation_id
        if store is None and reservation_id is not None:
            # Lazy import: usage imports this package's transports indirectly.
            from machina.core.usage import usage_store as store
        self._store = store
        self._settled = False

    async def settle(
        self, usage: dict[str, Any] | None = None, *, model: str | None = None
    ) -> None:
        """Finish the reservation with *usage* (success). No-op if settled."""
        await self._finish(usage, failed=False, model=model)

    async def fail(self) -> None:
        """Finish the reservation as failed. No-op if already settled."""
        await self._finish(None, failed=True, model=None)

    async def _finish(
        self,
        usage: dict[str, Any] | None,
        *,
        failed: bool,
        model: str | None,
    ) -> None:
        if self._settled or self.reservation_id is None:
            return
        self._settled = True
        try:
            await self._store.finish(self.reservation_id, usage, failed=failed, model=model)
        except Exception as error:
            # Accounting is additive: a failed settle must never mask the
            # generator's real close/cancel path or break teardown. The
            # orphaned reservation is still bounded by the ledger's cleanup.
            record_failure("provider.reservation_settle", error)


# ---------------------------------------------------------------------------
# Prompt-cache breakpoints moved to machina/core/provider/prompt_cache.py
# (god-file discipline); re-exported so existing import paths keep working.
# ---------------------------------------------------------------------------
from machina.core.provider.prompt_cache import (  # noqa: E402, F401
    _log_prompt_cache_breakpoints,
    _mark_message_tail_breakpoint,
    _openrouter_anthropic_model,
    _prompt_cache_breakpoints_enabled,
)
