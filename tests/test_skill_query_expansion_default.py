"""The default turn path does not spend a provider call on skill routing."""

from __future__ import annotations

import pytest

from machina.core.config import config
from machina.skills import query_expansion


@pytest.mark.asyncio
async def test_prepare_turn_is_local_when_expansion_is_opt_in(monkeypatch) -> None:
    query_expansion.clear_cache()
    monkeypatch.setattr(config, "skill_query_expansion_enabled", False)
    expansion = await query_expansion.prepare_turn("Fix the failing calculator test")
    assert expansion.is_empty
    assert query_expansion.current_expansion("Fix the failing calculator test").is_empty


@pytest.mark.asyncio
async def test_explicit_expansion_seam_still_runs(monkeypatch) -> None:
    query_expansion.clear_cache()
    monkeypatch.setattr(config, "skill_query_expansion_enabled", False)

    async def seam(_prompt: str) -> str:
        return "task_families: [web-build]\ncapabilities: [layout]\nkeywords: [css]"

    expansion = await query_expansion.prepare_turn("Fix the failing test", seam=seam)
    assert expansion.source == "llm"
    assert expansion.task_families == ("web-build",)
    assert expansion.capabilities == ("layout",)
