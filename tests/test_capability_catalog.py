"""Pin the model's capability self-knowledge to the live registries."""

from __future__ import annotations

import json

import pytest

import machina.tools  # noqa: F401 — registers every built-in tool
from machina.core import capability_catalog as catalog_module
from machina.core.provider import PROVIDERS
from machina.core.routing.types import COST_TIERS, ORCHESTRATION_MODES, ROLES, TRANSPORTS
from machina.core.self_knowledge import capability_context
from machina.core.session_mode import MODES
from machina.tools.base import registry


def test_tool_groups_cover_every_registered_tool() -> None:
    grouped = {name for names in catalog_module.TOOL_GROUPS.values() for name in names}
    assert grouped == set(registry._tools)
    assert len(grouped) == len(registry._tools)


def test_catalog_matches_live_registries() -> None:
    payload = catalog_module.catalog("all")
    assert {row["name"] for row in payload["tools"]} == set(registry._tools)
    assert payload["providers"] == list(PROVIDERS)
    vocab = payload["vocabularies"]
    assert vocab["modes"] == list(MODES)
    assert vocab["orchestration_modes"] == list(ORCHESTRATION_MODES)
    assert vocab["route_roles"] == list(ROLES)
    assert vocab["transports"] == list(TRANSPORTS)
    assert vocab["cost_tiers"] == list(COST_TIERS)


def test_capability_index_names_every_tool_and_provider() -> None:
    text = catalog_module.capability_index(offered={"read_file"})
    for name in registry._tools:
        assert name in text
    for provider in PROVIDERS:
        assert provider in text
    for mechanism in catalog_module.MECHANISMS:
        assert mechanism in text
    assert "Offered to this agent now: read_file" in text
    assert "Only offered tools are callable now" in text


def test_prompt_capability_context_carries_the_index() -> None:
    class FakeSession:
        execution_mode = "ask"

    text = capability_context(FakeSession(), tool_names={"read_file"}, growth_state="none")
    assert "Capability index" in text
    assert "Built-in tools" in text
    assert "harness_catalog" in text
    assert "Feature details" in text


def test_catalog_sections_are_present_and_bounded() -> None:
    assert set(catalog_module.SECTIONS) == {
        "summary",
        "tools",
        "providers",
        "vocabularies",
        "surfaces",
        "all",
    }
    assert len(json.dumps(catalog_module.catalog("all"))) <= 16_000
    assert len(catalog_module.capability_index()) <= 3_000


def test_catalog_rejects_unknown_sections() -> None:
    with pytest.raises(ValueError):
        catalog_module.catalog("nope")


def test_catalog_has_no_secret_material() -> None:
    text = json.dumps(catalog_module.catalog("all"))
    for marker in ("sk-", "api_key", "ANTHROPIC_API_KEY", "password"):
        assert marker not in text


def test_harness_catalog_tool_is_registered() -> None:
    tool = registry._tools.get("harness_catalog")
    assert tool is not None
    assert tool.effects == ()
    assert "capability catalog" in tool.description
    section = tool.parameters["properties"]["section"]
    assert section["enum"] == list(catalog_module.SECTIONS)
