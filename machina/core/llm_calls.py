"""Provider call, retry, and usage-settlement seam for one turn.

The loop owns when to call; this module owns how: one transient retry (never
after a delta, never for 429), a reservation around each attempt, and
exactly-once settlement. Keeping it separate makes the retry contract
testable without constructing an agent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from machina.core.agent_support import is_rate_limit_error, is_transient_error
from machina.core.background_tasks import joined_teardown
from machina.core.config import config
from machina.core.continuity import finish_usage
from machina.core.exceptions import DatabaseError, UsageBudgetExceededError
from machina.core.models import LLMResponse, StreamEvent
from machina.core.provider import LLMProvider
from machina.core.usage import provider_identity, usage_store

logger = logging.getLogger(__name__)

__all__ = ["LLMCaller"]


class LLMCaller:
    """Bound to one provider/agent identity for the lifetime of a turn."""

    def __init__(self, provider: LLMProvider, agent_id: str) -> None:
        self.provider = provider
        self.agent_id = agent_id

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        session_id: uuid.UUID | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Call ``provider.complete`` with one transient retry."""
        self._log_sections(messages, tools)
        last_exception: Exception | None = None
        for attempt in range(2):  # 1 initial + 1 retry
            try:
                return await usage_store.complete_call(
                    self.provider,
                    session_id,
                    self.agent_id,
                    messages,
                    tools=tools,
                    max_tokens=int(
                        max_tokens if max_tokens is not None else config.get("max_tokens")
                    ),
                    temperature=float(
                        temperature if temperature is not None else config.get("temperature")
                    ),
                )
            except (UsageBudgetExceededError, DatabaseError):
                raise
            except Exception as exc:
                last_exception = exc
                if is_rate_limit_error(exc):
                    # The provider already retried 429s with backoff; retrying
                    # here would amplify one rate limit into many requests.
                    raise
                if not is_transient_error(exc):
                    raise
                if attempt < 1:
                    delay = 2**attempt  # 1s
                    logger.warning(
                        "LLM call failed (attempt %d/%d): %s — retrying in %ds",
                        attempt + 1,
                        2,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("LLM call failed after %d attempts: %s", attempt + 1, exc)
        raise last_exception  # type: ignore[misc]

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        session_id: uuid.UUID | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Call ``provider.stream_complete`` with the same retry contract.

        A retry is only allowed before the first delta reaches the caller;
        replaying after a delta would duplicate text and could repeat a tool
        call.
        """
        self._log_sections(messages, tools)
        messages = await usage_store.prepare_stream(self, session_id, messages, tools)
        last_exception: Exception | None = None
        for attempt in range(2):  # 1 initial + 1 retry
            emitted = False
            try:
                reservation = await self.reserve(session_id, messages, tools)
                usage = None
                actual_model = None
                completed = False
                async with joined_teardown(
                    stream=self.provider.stream_complete(
                        messages=messages,
                        tools=tools,  # type: ignore[arg-type]
                        max_tokens=int(config.get("max_tokens")),
                        temperature=float(config.get("temperature")),
                    ),
                    cleanup=lambda: self.finish(
                        reservation, usage, failed=not completed, model=actual_model
                    ),
                ) as events:
                    async for event in events:
                        emitted = True
                        if event.type == "done" and event.response is not None:
                            usage = event.response.usage
                            actual_model = event.response.model
                            completed = True
                        yield event
                return
            except (UsageBudgetExceededError, DatabaseError):
                raise
            except Exception as exc:
                last_exception = exc
                if emitted:
                    raise
                if is_rate_limit_error(exc):
                    raise
                if not is_transient_error(exc):
                    raise
                if attempt < 1:
                    delay = 2**attempt  # 1s
                    logger.warning(
                        "LLM stream failed (attempt %d/%d): %s — retrying in %ds",
                        attempt + 1,
                        2,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("LLM stream failed after %d attempts: %s", attempt + 1, exc)
        raise last_exception  # type: ignore[misc]

    async def reserve(
        self,
        session_id: uuid.UUID | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> uuid.UUID | None:
        if session_id is None:
            return None
        model = str(getattr(self.provider, "model", "unknown"))
        provider_name = provider_identity(self.provider)
        max_tokens = int(config.get("max_tokens"))
        output_limit = getattr(type(self.provider), "reservation_max_tokens", None)
        if callable(output_limit):
            max_tokens = output_limit(self.provider, max_tokens)
        try:
            return await usage_store.reserve(
                session_id,
                self.agent_id,
                provider_name,
                model,
                messages,
                tools,
                max_tokens,
            )
        except UsageBudgetExceededError:
            raise
        except Exception as exc:
            raise DatabaseError("usage accounting unavailable") from exc

    async def finish(
        self,
        reservation: uuid.UUID | None,
        usage: dict[str, Any] | None = None,
        *,
        failed: bool = False,
        model: str | None = None,
    ) -> None:
        await finish_usage(reservation, usage, failed=failed, model=model)

    @staticmethod
    def _log_sections(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> None:
        try:
            from machina.controller import wiring

            wiring.log_section_tokens(messages, tools)
        except Exception:
            pass
