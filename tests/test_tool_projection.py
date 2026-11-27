"""Wire-level tool schema projection keeps constraints and trims prose."""

from __future__ import annotations

import json
import uuid

import pytest

from machina.core.agent import ReActAgent
from machina.core.assembler import get_token_count
from machina.core.models import Session
from machina.core.tool_projection import (
    compact_tool_definitions,
    reset_dynamic_tool_state,
)
from machina.tools.base import registry
from machina.tools.tool_loader import tool_load


def test_projection_preserves_names_types_required_and_ranges() -> None:
    reset_dynamic_tool_state()
    source = registry.get_tool_definitions()
    projected = compact_tool_definitions(source)
    assert [tool.name for tool in projected] == [tool.name for tool in source]
    assert all(len(tool.description) <= 160 or tool.name == "tool_load" for tool in projected)
    assert "web_search" in next(tool.description for tool in projected if tool.name == "tool_load")

    source_by_name = {tool.name: tool for tool in source}
    projected_by_name = {tool.name: tool for tool in projected}
    for name, original in source_by_name.items():
        projected_params = projected_by_name[name].parameters
        assert set(projected_params.get("properties", {})) == set(
            original.parameters.get("properties", {})
        )
        assert projected_params.get("required") == original.parameters.get("required")


def test_projection_reduces_the_wire_schema_but_keeps_original_immutable() -> None:
    source = registry.get_tool_definitions()
    before = json.dumps(
        [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in source
        ]
    )
    projected = compact_tool_definitions(source)
    after = json.dumps(
        [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in projected
        ]
    )
    assert get_token_count(after) < get_token_count(before)
    assert any(
        projected_tool.description == source_tool.description
        for projected_tool, source_tool in zip(projected, source, strict=True)
    )


def test_projection_removes_parameter_description_that_only_repeats_the_name() -> None:
    source = registry.get_tool_definitions()
    projected = compact_tool_definitions(source)
    source_by_name = {tool.name: tool for tool in source}
    projected_by_name = {tool.name: tool for tool in projected}
    original = source_by_name["skill_resource"].parameters["properties"]["skill_name"]
    compacted = projected_by_name["skill_resource"].parameters["properties"]["skill_name"]
    assert original.get("description") == "skill_name"
    assert "description" not in compacted


def test_dynamic_agent_starts_with_a_core_surface_and_loads_one_tool() -> None:
    reset_dynamic_tool_state()
    agent = ReActAgent(provider=object(), dynamic_tools=True)
    initial = {tool.name for tool in agent._tool_defs()}
    assert "tool_load" in initial
    assert "read_file" in initial
    assert "web_search" not in initial

    assert "Loaded schemas" in tool_load(["web_search"])
    after = {tool.name for tool in agent._tool_defs()}
    assert "web_search" in after
    assert "web_search" in {tool.name for tool in agent._tool_defs()}
    reset_dynamic_tool_state()


def test_tool_load_widens_the_next_step_across_the_tool_task_boundary() -> None:
    """Tool execution runs in a child asyncio task; the load must survive it.

    Regression: tool_load assigned a new ContextVar value inside the child
    task, the parent never saw it, and the next step denied the tool as
    "not in the current tool surface" even though it had just been loaded.
    """
    import asyncio

    reset_dynamic_tool_state()
    agent = ReActAgent(provider=object(), dynamic_tools=True)
    assert "delegate" not in {tool.name for tool in agent._tool_defs()}

    async def run_tool() -> str:
        # The real path nests the registry call inside the batch task and then
        # another execution task; exercise that two-level boundary here.
        async def nested() -> str:
            return await asyncio.create_task(registry.execute("tool_load", tools=["delegate"]))

        return await asyncio.create_task(nested())

    result = asyncio.run(run_tool())

    assert "Loaded schemas for: delegate" in result
    assert "delegate" in {tool.name for tool in agent._tool_defs()}
    reset_dynamic_tool_state()


def test_repeated_tool_load_reports_already_available_without_a_round_trip() -> None:
    reset_dynamic_tool_state()
    first = tool_load(["delegate"])
    second = tool_load(["delegate"])
    core = tool_load(["read_file"])

    assert "Loaded schemas for: delegate" in first
    assert "callable on this next model step" in first
    assert "Already available in the current tool surface: delegate" in second
    assert "No reload is needed" in second
    assert "Loaded schemas" not in second
    assert "Already available in the current tool surface: read_file" in core
    reset_dynamic_tool_state()


def test_tool_load_deduplicates_and_keeps_rejections_accurate() -> None:
    reset_dynamic_tool_state()
    result = tool_load(["delegate", "delegate", "not_a_tool"])

    assert result.count("delegate") == 1  # requested twice, loaded once
    assert result.startswith("Loaded schemas for: delegate.")
    assert "Not loaded (unknown or outside this agent's allow-list): not_a_tool" in result
    reset_dynamic_tool_state()


def test_dynamic_loader_respects_an_explicit_allow_list() -> None:
    from machina.core.tool_projection import set_dynamic_allowed_tools

    reset_dynamic_tool_state()
    set_dynamic_allowed_tools(["read_file", "web_search"])
    result = tool_load(["web_search", "mcp_call"])
    assert "Loaded schemas for: web_search" in result
    assert "mcp_call" in result
    assert "Not loaded" in result
    reset_dynamic_tool_state()


@pytest.mark.asyncio
async def test_factory_wires_dynamic_tool_loading_for_gateway_sessions(monkeypatch) -> None:
    from machina.core import agent_factory
    from machina.core import provider as provider_module
    from machina.core.agent_def import AgentDef, AgentRegistry
    from machina.core.config import DEFAULTS

    async def fake_get(_self, _name):
        return AgentDef(name="harness")

    async def no_rag():
        return None

    monkeypatch.setattr(AgentRegistry, "get", fake_get)
    monkeypatch.setattr(agent_factory, "_shared_rag_pipeline", no_rag)
    monkeypatch.setattr(provider_module, "get_provider", lambda **_kwargs: object())

    agent = await agent_factory.build_agent_for_session(
        Session(id=uuid.uuid4(), agent_id="harness")
    )

    assert DEFAULTS["dynamic_tool_loading"] is True
    assert agent.dynamic_tools is True


def test_context_preparer_resets_dynamic_state_from_the_host_flag() -> None:
    from types import SimpleNamespace

    from machina.core.context_prep import ContextPreparer
    from machina.core.tool_projection import (
        currently_visible_tool_names,
        mark_tools_loaded,
    )

    reset_dynamic_tool_state()
    mark_tools_loaded(["delegate"], {"delegate"})
    assert "delegate" in currently_visible_tool_names()

    passive = ContextPreparer(SimpleNamespace(dynamic_tools=False))
    assert passive._reset_dynamic_tool_state_if_needed() is False
    assert "delegate" in currently_visible_tool_names()

    active = ContextPreparer(SimpleNamespace(dynamic_tools=True))
    assert active._reset_dynamic_tool_state_if_needed() is True
    assert "delegate" not in currently_visible_tool_names()
