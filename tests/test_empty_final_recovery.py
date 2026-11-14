"""Providers that return an empty post-tool message still produce a final answer."""

from __future__ import annotations

import uuid

import pytest

from machina.core.agent import ReActAgent
from machina.core.models import LLMResponse


@pytest.mark.asyncio
async def test_empty_final_recovery_asks_for_a_fresh_tool_free_summary(monkeypatch):
    agent = ReActAgent(provider=object(), agent_id="harness")
    seen: dict = {}

    async def complete(messages, tools, session_id=None):
        seen["messages"] = messages
        seen["tools"] = tools
        return LLMResponse(
            content="Created index.html and verified the page.",
            model="fake",
            usage={"total_tokens": 7},
        )

    monkeypatch.setattr(agent, "_call_llm_with_retry", complete)
    content, tokens = await agent._recover_empty_final(
        uuid.uuid4(),
        "Build a portfolio",
        [{"tool": "write_file", "result_preview": "Successfully wrote index.html"}],
    )

    assert content == "Created index.html and verified the page."
    assert tokens == 7
    assert seen["tools"] == []
    assert "Build a portfolio" in seen["messages"][1]["content"]
    assert "write_file" in seen["messages"][1]["content"]


@pytest.mark.asyncio
async def test_empty_final_recovery_uses_deterministic_fallback(monkeypatch):
    agent = ReActAgent(provider=object(), agent_id="harness")

    async def complete(_messages, tools, session_id=None):
        return LLMResponse(content="", model="fake", usage={"total_tokens": 0})

    monkeypatch.setattr(agent, "_call_llm_with_retry", complete)
    content, tokens = await agent._recover_empty_final(
        uuid.uuid4(),
        "Build a portfolio",
        [{"tool": "write_file", "result_preview": ""}],
    )

    assert tokens == 0
    assert "Completed 1 tool call(s)" in content
    assert "write_file" in content


@pytest.mark.asyncio
async def test_empty_tool_turn_restarts_with_tools_and_current_results(monkeypatch):
    agent = ReActAgent(provider=object(), agent_id="harness")
    seen: dict = {}

    async def complete(messages, tools, session_id=None):
        seen["messages"] = messages
        seen["tools"] = tools
        return LLMResponse(
            content="",
            model="fake",
            usage={"total_tokens": 9},
            tool_calls=[{"id": "next", "function": {"name": "write_file", "arguments": "{}"}}],
        )

    monkeypatch.setattr(agent, "_call_llm_with_retry", complete)
    response, messages, tokens = await agent._continue_after_empty_tool_turn(
        uuid.uuid4(),
        "Build the page",
        [{"tool": "write_file", "args": {"path": "index.html"}, "result_preview": "wrote"}],
        tools=[{"type": "function", "function": {"name": "write_file"}}],
    )

    assert response is not None and response.tool_calls
    assert tokens == 9
    assert seen["tools"]
    assert messages[1]["role"] == "user"
    assert "Build the page" in messages[1]["content"]
    assert "write_file" in messages[1]["content"]


@pytest.mark.asyncio
async def test_empty_final_recovery_is_a_noop_without_tool_calls(monkeypatch):
    agent = ReActAgent(provider=object(), agent_id="harness")

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("must not call the provider without tool results")

    monkeypatch.setattr(agent, "_call_llm_with_retry", unexpected)
    assert await agent._recover_empty_final(uuid.uuid4(), "hello", []) == ("", 0)
