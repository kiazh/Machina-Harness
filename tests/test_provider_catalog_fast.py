"""The model-picker catalog path must never wait on live discovery."""

import pytest

from machina.core.provider import catalog
from machina.gateway.features import providers_feature as feature


class _Gateway:
    provider = "deepseek"
    model = "deepseek-v4-flash"

    def require_db(self) -> None:
        pass


@pytest.mark.asyncio
async def test_chatgpt_fast_uses_cached_catalog_without_fetch(monkeypatch) -> None:
    cached = [
        catalog.CatalogModel(
            slug="gpt-6-luna",
            display_name="GPT-6 Luna",
            description="",
            visibility="list",
            priority=1,
            context_window=272_000,
            max_context_window=272_000,
            default_effort="high",
            advertised_efforts=("low", "high"),
            supported_efforts=("low", "high"),
            source="codex-cache",
        )
    ]

    def fail_fetch(**_kwargs):
        raise AssertionError("fast catalog path touched the network")

    monkeypatch.setattr(catalog, "cached_catalog", lambda: cached)
    monkeypatch.setattr(catalog, "fetch_catalog_for_account", fail_fetch)

    result = await feature._chatgpt_models("chatgpt", False, fast=True)

    assert result["source"] == "codex-cache"
    assert [entry["id"] for entry in result["models"]] == ["gpt-6-luna"]
    assert result["fetchError"] is None


@pytest.mark.asyncio
async def test_named_fast_returns_cached_rows_without_fetch(monkeypatch) -> None:
    entry = {
        "base_url": "http://127.0.0.1:9000/v1",
        "models": [{"id": "acme-1"}],
        "models_fetched_at": "2026-11-12T00:00:00Z",
    }

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("fast catalog path touched the network")

    monkeypatch.setattr(feature.store, "get_provider_entry", lambda _name: entry)
    monkeypatch.setattr(feature.store, "fetch_models", fail_fetch)

    result = await feature._named_models("acme", True, fast=True)

    assert result["source"] == "cache"
    assert result["models"] == [{"id": "acme-1"}]


@pytest.mark.asyncio
async def test_kiro_fast_serves_cache_without_a_reachability_probe(monkeypatch) -> None:
    from machina.core.provider import kiro_proxy

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("fast catalog path touched the Kiro proxy")

    monkeypatch.setattr(
        kiro_proxy, "cached_models", lambda: [{"id": "auto"}, {"id": "claude-sonnet-5"}]
    )
    monkeypatch.setattr(kiro_proxy, "fetch_models", fail_fetch)

    result = await feature._kiro_models("kiro", False, fast=True)

    assert result["source"] == "cache"
    assert [entry["id"] for entry in result["models"]] == ["auto", "claude-sonnet-5"]


@pytest.mark.asyncio
async def test_kiro_fast_falls_back_offline_when_the_cache_is_cold(monkeypatch) -> None:
    from machina.core.provider import kiro_proxy

    monkeypatch.setattr(kiro_proxy, "cached_models", lambda: None)

    result = await feature._kiro_models("kiro", False, fast=True)

    assert result["source"] == "fallback"
    assert result["models"]


@pytest.mark.asyncio
async def test_models_list_fast_is_cache_only_and_explicit_refresh_wins(monkeypatch) -> None:
    seen: list[bool] = []

    async def fake_named(provider: str, refresh: bool, *, fast: bool = False):
        seen.append(fast)
        return feature._models_payload(provider, source="cache", models=[])

    monkeypatch.setattr(feature.store, "is_named_provider", lambda _name: True)
    monkeypatch.setattr(feature, "_named_models", fake_named)

    await feature.models_list(_Gateway(), {"provider": "acme", "fast": True})
    await feature.models_list(
        _Gateway(), {"provider": "acme", "fast": True, "refresh": True}
    )

    assert seen == [True, False]
