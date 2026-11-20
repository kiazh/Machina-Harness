"""Wire-level tool schema projection keeps constraints and trims prose."""

from __future__ import annotations

import json

from machina.core.agent import ReActAgent
from machina.core.assembler import get_token_count
from machina.core.tool_projection import (
    compact_tool_definitions,
    reset_dynamic_tool_state,
)
from machina.tools.base import registry
from machina.tools.tool_loader import tool_load


def test_projection_preserves_names_types_required_and_ranges() -> None:
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


def test_dynamic_loader_respects_an_explicit_allow_list() -> None:
    from machina.core.tool_projection import set_dynamic_allowed_tools

    reset_dynamic_tool_state()
    set_dynamic_allowed_tools(["read_file", "web_search"])
    result = tool_load(["web_search", "mcp_call"])
    assert "Loaded schemas for: web_search" in result
    assert "mcp_call" in result
    assert "Not loaded" in result
    reset_dynamic_tool_state()
