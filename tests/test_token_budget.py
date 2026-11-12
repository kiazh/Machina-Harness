"""Token-budget rails for repair turns."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from machina.core.token_budget import (
    DEFAULT_REPAIR_TOKEN_BUDGET,
    HIGH_STALE_CHARS,
    MAX_REPAIR_TOKEN_BUDGET,
    SOFT_STALE_CHARS,
    repair_token_budget,
    repair_token_budget_exceeded,
    repair_token_pressure,
    stale_tool_result_chars,
)

REPAIR = "Fix the failing test in the repository."
CHAT = "Explain how the repository is organized."


def test_default_budget_is_bounded_and_disableable(monkeypatch) -> None:
    monkeypatch.delenv("MACHINA_REPAIR_TOKEN_BUDGET", raising=False)
    assert repair_token_budget() == DEFAULT_REPAIR_TOKEN_BUDGET

    monkeypatch.setenv("MACHINA_REPAIR_TOKEN_BUDGET", "0")
    assert repair_token_budget() == 0

    monkeypatch.setenv("MACHINA_REPAIR_TOKEN_BUDGET", "-50")
    assert repair_token_budget() == 0

    monkeypatch.setenv("MACHINA_REPAIR_TOKEN_BUDGET", "not-a-number")
    assert repair_token_budget() == DEFAULT_REPAIR_TOKEN_BUDGET

    monkeypatch.setenv("MACHINA_REPAIR_TOKEN_BUDGET", str(MAX_REPAIR_TOKEN_BUDGET * 2))
    assert repair_token_budget() == MAX_REPAIR_TOKEN_BUDGET


def test_budget_applies_only_to_repair_turns() -> None:
    assert repair_token_pressure(900_000, REPAIR, budget=1_000_000) == 0.9
    assert repair_token_pressure(900_000, CHAT, budget=1_000_000) == 0.0
    assert repair_token_pressure(900_000, REPAIR, budget=0) == 0.0
    assert repair_token_budget_exceeded(1_000_000, REPAIR, budget=1_000_000)
    assert not repair_token_budget_exceeded(999_999, REPAIR, budget=1_000_000)
    assert not repair_token_budget_exceeded(1_000_000, CHAT, budget=1_000_000)
    assert not repair_token_budget_exceeded(1_000_000, REPAIR, budget=0)


def test_pressure_tightens_stale_tool_results() -> None:
    assert stale_tool_result_chars(100_000, REPAIR, budget=1_000_000) is None
    assert stale_tool_result_chars(750_000, REPAIR, budget=1_000_000) == SOFT_STALE_CHARS
    assert stale_tool_result_chars(900_000, REPAIR, budget=1_000_000) == HIGH_STALE_CHARS
    assert stale_tool_result_chars(900_000, CHAT, budget=1_000_000) is None


def test_pressure_is_clamped() -> None:
    assert repair_token_pressure(-10, REPAIR, budget=1_000_000) == 0.0
    assert repair_token_pressure(10_000_000, REPAIR, budget=1_000_000) == 1.0


@pytest.mark.asyncio
async def test_agent_stops_before_a_continuation_after_budget(monkeypatch) -> None:
    from machina.core import agent as module
    from machina.core.models import LLMResponse, Session

    monkeypatch.setenv("MACHINA_REPAIR_TOKEN_BUDGET", "100")
    session = Session(id=uuid4(), agent_id="harness")
    messages = [{"role": "user", "content": REPAIR}]

    async def gate(msgs, **kwargs):
        return SimpleNamespace(messages=msgs, failure=None)

    async def model(**kwargs):
        return LLMResponse(
            model="test",
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "function": {"name": "terminal", "arguments": "{}"},
                }
            ],
            usage={"total_tokens": 100},
        )

    monkeypatch.setattr(module, "evaluate_request", gate)
    agent = SimpleNamespace(
        agent_id="harness",
        provider=object(),
        _prepare_context=AsyncMock(return_value=(session, messages)),
        _flush_pending_chunks=AsyncMock(),
        _append_assistant_content=AsyncMock(),
        _condense_history=lambda _: None,
        _tool_defs=lambda: [],
        _call_llm_with_retry=model,
        _execute_tool_calls=AsyncMock(),
        _finish_turn_completion=AsyncMock(),
        _schedule_memory_consolidation=lambda *args: None,
    )

    response = await module.ReActAgent.run.__wrapped__(
        agent, session.id, REPAIR, False
    )

    assert response.outcome == "failed"
    assert "token budget" in response.content.lower()
    assert response.tokens_used == 100
    agent._finish_turn_completion.assert_not_awaited()
