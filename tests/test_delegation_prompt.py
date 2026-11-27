import asyncio
import uuid
from types import SimpleNamespace

import pytest

from machina.core import orchestrator as orch
from machina.core.orchestrator import Orchestrator


def test_delegation_prompt_separates_role_assignment_and_shared_context() -> None:
    prompt = Orchestrator._build_delegation_prompt(
        "code-architect", "Design the portfolio shell", "Goal: build a portfolio"
    )
    assert "# Delegation Prompt v1.1.0" in prompt
    assert "## Agent\ncode-architect" in prompt
    assert "## Assignment\nDesign the portfolio shell" in prompt
    assert "## Shared Project Context (read-only)" in prompt
    assert "do not replace your role" in prompt.lower()
    assert prompt.index("## Assignment") < prompt.index("## Shared Project Context")


def test_delegation_prompt_omits_shared_context_when_empty() -> None:
    prompt = Orchestrator._build_delegation_prompt("coder", "Fix the test", "")
    assert "## Shared Project Context" not in prompt


def test_parent_context_prioritizes_readable_text_over_tool_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> str:
        parent = SimpleNamespace(goal="Build a portfolio")
        chunks = [
            {"type": "tool_call", "payload": {"tool": "terminal", "args": {}}, "tokens": 1},
            {
                "type": "assistant_message",
                "payload": {"content": "Plan: vanilla HTML, three files, accessibility first."},
                "tokens": 10,
            },
            {
                "type": "user_message",
                "payload": {"content": "make a portfolio"},
                "tokens": 5,
            },
        ]

        async def get_fresh(_session_id):
            return parent

        async def get_recent_context(_session_id, limit=20):
            assert limit == 20
            return chunks

        monkeypatch.setattr(orch, "session_manager", SimpleNamespace(get_fresh=get_fresh))
        monkeypatch.setattr(
            orch,
            "context_manager",
            SimpleNamespace(get_recent_context=get_recent_context),
        )
        return await Orchestrator()._parent_context(uuid.uuid4())

    text = asyncio.run(scenario())
    assert "Parent goal: Build a portfolio" in text
    assert "make a portfolio" in text
    assert "Plan: vanilla HTML" in text
    assert "terminal" not in text
