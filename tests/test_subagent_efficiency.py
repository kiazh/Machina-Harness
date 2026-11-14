"""Delegated workers keep task tools without inheriting the main agent persona."""

from types import SimpleNamespace

import pytest

from machina.core.agent import ReActAgent
from machina.tools.agents import list_agents


def test_delegated_worker_has_compact_prompt_and_no_recursive_delegation():
    agent = ReActAgent(provider=object(), agent_id="harness", delegated=True)
    assert "delegated worker" in agent.system_prompt
    assert "persistent context stored in PostgreSQL" not in agent.system_prompt
    offered = {definition.name for definition in agent._tool_defs()}
    assert "delegate" not in offered
    assert "list_agents" not in offered
    assert "learn_personality" not in offered
    assert "terminal" in offered
    assert "delegate" not in agent.allowed_tools
    assert "learn_personality" not in agent.allowed_tools


@pytest.mark.asyncio
async def test_agent_discovery_is_bounded_and_searchable(monkeypatch):
    from machina.core.agent_def import agent_registry

    async def agents():
        return [
            SimpleNamespace(name=f"specialist-{index}", description=f"topic {index}")
            for index in range(30)
        ]

    monkeypatch.setattr(agent_registry, "list", agents)
    roster = await list_agents()
    assert roster.count("- specialist-") == 12
    assert "18 more agents" in roster
    assert "specialist-29" in await list_agents(query="topic 29")


@pytest.mark.asyncio
async def test_agent_discovery_accepts_multiple_space_separated_terms(monkeypatch):
    from machina.core.agent_def import agent_registry

    async def agents():
        return [
            SimpleNamespace(
                name="a11y-architect",
                description="Accessibility Architect specializing in WCAG compliance.",
            ),
            SimpleNamespace(name="coder", description="Reads, edits and runs code."),
        ]

    monkeypatch.setattr(agent_registry, "list", agents)
    found = await list_agents(query="accessibility a11y frontend")

    assert "- a11y-architect:" in found
    assert "- coder:" not in found


@pytest.mark.asyncio
async def test_default_roster_keeps_core_roles_visible_with_many_specialists(monkeypatch):
    from machina.core.agent_def import agent_registry

    async def agents():
        return [
            SimpleNamespace(name=f"a-specialist-{index}", description="specialist")
            for index in range(20)
        ] + [
            SimpleNamespace(name=name, description="core")
            for name in ("harness", "researcher", "coder")
        ]

    monkeypatch.setattr(agent_registry, "list", agents)
    roster = await list_agents()
    assert all(f"- {name}:" in roster for name in ("harness", "researcher", "coder"))
    assert roster.count("\n-") == 11
    assert "11 more agents" in roster
