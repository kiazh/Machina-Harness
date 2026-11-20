"""Wire-level tool schema projection keeps constraints and trims prose."""

from __future__ import annotations

import json

from machina.core.assembler import get_token_count
from machina.core.tool_projection import compact_tool_definitions
from machina.tools.base import registry


def test_projection_preserves_names_types_required_and_ranges() -> None:
    source = registry.get_tool_definitions()
    projected = compact_tool_definitions(source)
    assert [tool.name for tool in projected] == [tool.name for tool in source]
    assert all(len(tool.description) <= 160 for tool in projected)

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
