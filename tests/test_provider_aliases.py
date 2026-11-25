"""Provider-name aliases and legacy Hermes route migration."""

from __future__ import annotations

import json

import pytest

from machina.core import hermes_proxy
from machina.core.config import config
from machina.core.provider.aliases import canonical_provider
from machina.security import providers as store


def _store_payload() -> dict:
    return {
        "version": 1,
        "providers": {
            "hermes-proxy": {
                "base_url": "http://127.0.0.1:8645/v1",
                "api_key_env": "MACHINA_PROVIDER_HERMES_PROXY_API_KEY",
                "default_model": "qwen/qwen4-max:free",
                "models": [{"id": "qwen/qwen4-max:free"}],
                "models_fetched_at": "2026-11-19T18:41:12Z",
            }
        },
    }


def test_hermes_proxy_is_canonicalized_to_the_builtin(monkeypatch, tmp_path) -> None:
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(_store_payload()), encoding="utf-8")
    monkeypatch.setenv("MACHINA_PROVIDERS_FILE", str(path))

    assert canonical_provider("hermes-proxy") == "hermes"
    assert canonical_provider(" Hermes-Proxy ") == "hermes"
    assert canonical_provider("hermes") == "hermes"
    assert "hermes-proxy" in store.RESERVED_NAMES
    assert "hermes-proxy" not in store.provider_names()
    assert store.is_named_provider("hermes-proxy") is False


def test_legacy_hermes_named_provider_migrates_to_current_catalog(monkeypatch, tmp_path) -> None:
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(_store_payload()), encoding="utf-8")
    monkeypatch.setenv("MACHINA_PROVIDERS_FILE", str(path))
    monkeypatch.setattr(store, "find_env_file", lambda: tmp_path / "missing.env")
    monkeypatch.setitem(config._session_overrides, "provider", "hermes-proxy")
    monkeypatch.setitem(config._session_overrides, "model", "qwen/qwen4-max:free")
    monkeypatch.setattr(hermes_proxy, "cached_models", lambda: [{"id": "qwen/qwen4-max"}])
    persisted: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "machina.core.config.persist_provider_fallback",
        lambda provider, model=None: persisted.append((provider, model)),
    )

    assert hermes_proxy.migrate_legacy_provider() is True
    assert store.get_provider_entry("hermes-proxy") is None
    assert persisted == [("hermes", "qwen/qwen4-max")]
    assert hermes_proxy.migrate_legacy_provider() is False


@pytest.mark.asyncio
async def test_providers_list_returns_one_canonical_hermes_group(monkeypatch) -> None:
    from machina.gateway.features import providers_feature

    monkeypatch.setattr(hermes_proxy, "migrate_legacy_provider", lambda: False)
    monkeypatch.setattr(providers_feature.store, "provider_names", lambda: ())
    monkeypatch.setattr(
        providers_feature.config,
        "get",
        lambda key: {
            "orchestration_mode": "single_model",
            "model_selection": "auto",
            "auto_parallel_delegation": False,
            "single_model_auto_parallel": False,
        }.get(key),
    )

    class Gateway:
        provider = "hermes-proxy"
        require_db = staticmethod(lambda: None)

    payload = await providers_feature.providers_list(Gateway(), {})

    assert payload["selected"] == "hermes"
    names = [row["name"] for row in payload["providers"]]
    assert names.count("hermes") == 1
    assert "hermes-proxy" not in names
