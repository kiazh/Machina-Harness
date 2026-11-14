"""Kiro's local-proxy provider: account auth, no Kiro API key."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from machina.core.config import DEFAULTS, LEGACY_ENV_VARS, SECRET_KEYS, config
from machina.core.exceptions import ProviderError
from machina.core.provider import kiro_proxy
from machina.core.provider.direct import DirectProvider
from machina.core.provider.factory import get_provider
from machina.core.provider.specs import PROVIDER_SPECS, PROVIDERS
from machina.gateway.features.providers_feature import models_list
from machina.security import providers as store


@pytest.fixture(autouse=True)
def _clear_kiro_catalog_cache():
    kiro_proxy.clear_catalog_cache()
    yield
    kiro_proxy.clear_catalog_cache()


def _override(monkeypatch: pytest.MonkeyPatch, key: str, value: object) -> None:
    """Set a config value without touching the user's real .env."""
    monkeypatch.setitem(config._session_overrides, key, value)


def test_kiro_spec_targets_the_local_gateway_without_an_api_key(monkeypatch) -> None:
    _override(monkeypatch, "kiro_api_key", "")
    spec = PROVIDER_SPECS["kiro"]

    assert spec.base_url == "http://127.0.0.1:8000/v1"
    assert spec.api_key_env == "KIRO_API_KEY"
    assert spec.default_model == "auto"
    assert spec.requires_key is False
    assert spec.reasoning == "none"
    assert "kiro" in PROVIDERS
    assert kiro_proxy.api_key() == "dummy-local-only"
    assert "kiro" in store.RESERVED_NAMES


@pytest.mark.asyncio
async def test_direct_provider_allows_long_reasoning_turns(monkeypatch) -> None:
    _override(monkeypatch, "kiro_api_key", "")
    provider = get_provider(provider="kiro", model="claude-opus-4.7")
    try:
        assert provider.client.timeout.read == 300.0
    finally:
        await provider.close()


def test_fallback_catalog_is_not_sonnet_only() -> None:
    ids = [row["id"] for row in kiro_proxy.fallback_models()]

    assert ids == list(kiro_proxy.MODEL_METADATA)
    assert len(ids) == 22
    assert "claude-opus-5.5" in ids
    assert "claude-sonnet-5.5" in ids
    assert "claude-haiku-5.5" in ids
    assert "gpt-5.6-sol" in ids
    assert "kimi-k3" in ids
    assert "claude-opus-4.8" in ids
    assert "claude-sonnet-4.6" in ids
    assert "claude-haiku-4.5" in ids
    assert "claude-opus-4.7" in ids
    assert "deepseek-3.2" in ids
    assert "glm-5" in ids
    assert "minimax-m2.5" in ids
    assert "qwen3-coder-next" in ids
    assert all(row["source"] == "fallback" for row in kiro_proxy.fallback_models())


def test_kiro_context_budgets_use_the_account_catalog() -> None:
    from machina.core.context_windows import context_window_for

    assert context_window_for("claude-opus-4.8", "kiro") == 1_000_000
    assert context_window_for("claude-sonnet-4.6", "kiro") == 1_000_000
    assert context_window_for("deepseek-3.2", "kiro") == 164_000
    assert context_window_for("minimax-m2.5", "kiro") == 196_000
    assert context_window_for("qwen3-coder-next", "kiro") == 256_000


def test_legacy_auto_kiro_alias_is_canonicalized(monkeypatch) -> None:
    from machina.core.context_windows import context_window_for

    _override(monkeypatch, "kiro_api_key", "")
    provider = get_provider("kiro", model="auto-kiro")
    try:
        assert provider.model == "auto"
    finally:
        asyncio.run(provider.close())
    assert context_window_for("auto-kiro", "kiro") == 1_000_000


def test_config_exposes_optional_local_proxy_settings() -> None:
    assert "kiro_api_key" in DEFAULTS
    assert "kiro_base_url" in DEFAULTS
    assert DEFAULTS["kiro_autostart"] is True
    assert "kiro_gateway_command" in DEFAULTS
    assert "kiro_gateway_dir" in DEFAULTS
    assert "kiro_api_key" in SECRET_KEYS
    assert LEGACY_ENV_VARS["kiro_api_key"] == "KIRO_API_KEY"
    assert LEGACY_ENV_VARS["kiro_base_url"] == "KIRO_BASE_URL"
    assert LEGACY_ENV_VARS["kiro_gateway_dir"] == "KIRO_GATEWAY_DIR"
    assert LEGACY_ENV_VARS["kiro_gateway_command"] == "KIRO_GATEWAY_COMMAND"
    assert LEGACY_ENV_VARS["kiro_autostart"] == "KIRO_AUTOSTART"


def test_factory_passes_the_dummy_local_password_and_override(monkeypatch) -> None:
    _override(monkeypatch, "kiro_api_key", "")
    _override(monkeypatch, "kiro_base_url", "http://127.0.0.1:8123/v1")

    provider = get_provider("kiro", model="claude-sonnet-4.6")
    try:
        assert isinstance(provider, DirectProvider)
        assert provider.spec.base_url == "http://127.0.0.1:8123/v1"
        assert provider.api_key == "dummy-local-only"
        assert provider.model == "claude-sonnet-4.6"
    finally:
        asyncio.run(provider.close())


def test_a_custom_local_proxy_password_is_optional(monkeypatch) -> None:
    _override(monkeypatch, "kiro_api_key", "my-loopback-password")

    provider = get_provider("kiro")
    try:
        assert provider.api_key == "my-loopback-password"
    finally:
        asyncio.run(provider.close())


def test_kiro_is_reserved_against_named_provider_shadowing() -> None:
    with pytest.raises(store.ProviderStoreError):
        store.validate_name("kiro")


def test_model_discovery_overlays_the_gateway_on_the_full_catalog(monkeypatch) -> None:
    _override(monkeypatch, "kiro_api_key", "")
    _override(monkeypatch, "kiro_base_url", "http://127.0.0.1:8123/v1")

    def fake_fetch(base_url: str, *, api_key: str, timeout: float):
        assert base_url == "http://127.0.0.1:8123/v1"
        assert api_key == "dummy-local-only"
        assert timeout == kiro_proxy.MODELS_TIMEOUT_SECONDS
        return [
            {"id": "claude-sonnet-4.5", "contextWindow": 123_456},
            {"id": "future-kiro-model"},
            {"id": "", "contextWindow": 1},
        ]

    monkeypatch.setattr(kiro_proxy, "_fetch_catalog_rows", fake_fetch)
    monkeypatch.setattr(kiro_proxy, "gateway_reachable", lambda *_a, **_k: True)

    def cli_must_not_run():
        raise AssertionError("the picker path must not invoke kiro-cli")

    monkeypatch.setattr(kiro_proxy, "_cli_models", cli_must_not_run)

    rows = kiro_proxy.fetch_models()

    assert len(rows) == len(kiro_proxy.MODEL_METADATA) + 1
    by_id = {row["id"]: row for row in rows}
    assert by_id["claude-sonnet-4.5"]["contextWindow"] == 123_456
    assert by_id["claude-sonnet-4.5"]["source"] == "live"
    assert by_id["auto"]["source"] == "fallback"
    assert by_id["future-kiro-model"]["source"] == "live"
    assert rows[-1]["id"] == "future-kiro-model"


def test_catalog_cache_avoids_repeated_gateway_calls(monkeypatch) -> None:
    _override(monkeypatch, "kiro_base_url", "http://127.0.0.1:8124/v1")
    calls = 0

    def fake_fetch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return [{"id": "auto"}]

    monkeypatch.setattr(kiro_proxy, "_fetch_catalog_rows", fake_fetch)
    monkeypatch.setattr(kiro_proxy, "gateway_reachable", lambda *_a, **_k: True)

    first = kiro_proxy.fetch_models()
    second = kiro_proxy.fetch_models()

    assert calls == 1
    assert first == second
    assert len(first) == 22


def test_dead_gateway_fails_before_the_http_timeout(monkeypatch) -> None:
    _override(monkeypatch, "kiro_base_url", "http://127.0.0.1:8125/v1")
    monkeypatch.setattr(kiro_proxy, "gateway_reachable", lambda *_a, **_k: False)

    def http_must_not_run(*_args, **_kwargs):
        raise AssertionError("the HTTP fetch must not run when the preflight fails")

    monkeypatch.setattr(kiro_proxy, "_fetch_catalog_rows", http_must_not_run)

    started = time.monotonic()
    with pytest.raises(kiro_proxy.KiroGatewayUnavailable, match="/auth kiro start"):
        kiro_proxy.fetch_models()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5


def test_cli_catalog_parses_auto_and_context_windows(monkeypatch) -> None:
    monkeypatch.setattr(kiro_proxy.shutil, "which", lambda _name: "kiro-cli")

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "models": [
                    {
                        "model_id": "auto",
                        "description": "Kiro chooses",
                        "context_window_tokens": 1_000_000,
                    },
                    {
                        "model_id": "gpt-5.6-sol",
                        "description": "GPT Sol",
                        "context_window_tokens": 986_000,
                    },
                ]
            }
        )

    monkeypatch.setattr(kiro_proxy.subprocess, "run", lambda *args, **kwargs: Completed())

    rows = kiro_proxy._cli_models()

    assert [row["id"] for row in rows] == ["auto", "gpt-5.6-sol"]
    assert rows[0]["displayName"] == "Kiro Auto"
    assert rows[0]["contextWindow"] == 1_000_000
    assert rows[1]["contextWindow"] == 986_000
    assert all(row["source"] == "catalog" for row in rows)


def test_gateway_and_cli_catalogs_merge_without_duplicates(monkeypatch) -> None:
    _override(monkeypatch, "kiro_base_url", "http://127.0.0.1:8126/v1")
    monkeypatch.setattr(kiro_proxy, "gateway_reachable", lambda *_a, **_k: True)
    monkeypatch.setattr(
        kiro_proxy,
        "_fetch_catalog_rows",
        lambda *args, **kwargs: [
            {"id": "auto-kiro"},
            {"id": "claude-sonnet-4.5", "contextWindow": 200_000},
            {"id": "claude-sonnet-4"},
        ],
    )
    monkeypatch.setattr(
        kiro_proxy,
        "_cli_models",
        lambda: [
            {
                "id": "auto",
                "displayName": "Kiro Auto",
                "description": "Kiro chooses",
                "contextWindow": 1_000_000,
                "maxContextWindow": None,
                "source": "catalog",
            },
            {
                "id": "claude-sonnet-4.5",
                "displayName": "Claude Sonnet 4.5",
                "description": "Claude Sonnet 4.5 model",
                "contextWindow": 200_000,
                "maxContextWindow": None,
                "source": "catalog",
            },
            {
                "id": "gpt-5.6-sol",
                "displayName": "GPT-5.6 Sol",
                "description": "OpenAI GPT 5.6 Sol with 1M context window",
                "contextWindow": 986_000,
                "maxContextWindow": None,
                "source": "catalog",
            },
        ],
    )

    rows = kiro_proxy.fetch_models(force=True)

    assert [row["id"] for row in rows] == ["auto", "claude-sonnet-4.5", "gpt-5.6-sol"]
    assert [row["source"] for row in rows] == ["live", "live", "catalog"]


@pytest.mark.asyncio
async def test_models_list_returns_live_gateway_catalog(monkeypatch) -> None:
    monkeypatch.setattr(
        kiro_proxy,
        "fetch_models",
        lambda: [{"id": "claude-opus-4.7", "displayName": "claude-opus-4.7"}],
    )

    class GatewayStub:
        def require_db(self) -> None:
            return None

    result = await models_list(GatewayStub(), {"provider": "kiro"})

    assert result["provider"] == "kiro"
    assert result["source"] == "live"
    assert result["models"] == [{"id": "claude-opus-4.7", "displayName": "claude-opus-4.7"}]
    assert result["fetchError"] is None


@pytest.mark.asyncio
async def test_models_list_falls_back_when_the_gateway_is_down(monkeypatch) -> None:
    def unreachable():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(kiro_proxy, "fetch_models", unreachable)

    class GatewayStub:
        def require_db(self) -> None:
            return None

    result = await models_list(GatewayStub(), {"provider": "kiro"})

    assert result["source"] == "fallback"
    assert result["fetchError"] == "connection refused"
    ids = [row["id"] for row in result["models"]]
    assert ids == list(kiro_proxy.MODEL_METADATA)
    assert len(ids) == 22


@pytest.mark.asyncio
async def test_models_list_dead_proxy_returns_fast(monkeypatch) -> None:
    _override(monkeypatch, "kiro_base_url", "http://127.0.0.1:8127/v1")
    monkeypatch.setattr(kiro_proxy, "gateway_reachable", lambda *_a, **_k: False)

    class GatewayStub:
        def require_db(self) -> None:
            return None

    started = time.monotonic()
    result = await models_list(GatewayStub(), {"provider": "kiro"})
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert result["source"] == "fallback"
    assert len(result["models"]) == 22


@pytest.mark.asyncio
async def test_models_list_honors_forced_refresh(monkeypatch) -> None:
    calls: list[bool] = []

    def fake_fetch(*, force: bool = False):
        calls.append(force)
        return [{"id": "auto", "source": "live"}]

    monkeypatch.setattr(kiro_proxy, "fetch_models", fake_fetch)

    class GatewayStub:
        def require_db(self) -> None:
            return None

    result = await models_list(GatewayStub(), {"provider": "kiro", "refresh": True})

    assert calls == [True]
    assert result["source"] == "live"


def test_readiness_checks_the_proxy_not_a_kiro_api_key(monkeypatch) -> None:
    from machina.core import runtime

    monkeypatch.setenv("MACHINA_PROVIDER", "kiro")
    _override(monkeypatch, "kiro_base_url", "")
    monkeypatch.setattr(runtime, "_endpoint_reachable", lambda _url: False)

    down = runtime._detect_providers()
    assert down["chat_provider"] == "unavailable"
    assert "unreachable" in down["chat_provider_reason"]

    monkeypatch.setattr(runtime, "_endpoint_reachable", lambda _url: True)
    up = runtime._detect_providers()
    assert up["chat_provider"] == "ready"
    assert up["chat_provider_reason"] == ""

@pytest.mark.asyncio
async def test_kiro_402_is_a_typed_usage_limit_error(monkeypatch) -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={
                "error": {
                    "message": "Monthly request limit exceeded. Account has reached its monthly quota."
                }
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "machina.core.provider.direct.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    provider = get_provider("kiro", "auto")
    try:
        with pytest.raises(ProviderError) as caught:
            await provider.complete(
                [{"role": "user", "content": "hello"}],
                max_tokens=8,
            )
    finally:
        await provider.close()

    assert "Kiro usage limit reached" in str(caught.value)

def test_turn_usage_event_carries_kiro_context_fields() -> None:
    from machina.core.models import StreamEvent
    from machina.core.turn_engine import TurnEngine

    engine = TurnEngine.__new__(TurnEngine)
    engine._tokens = 0
    translated = engine._translate(
        StreamEvent(
            type="token_usage",
            tokens_used=1381,
            context_tokens=1381,
            context_usage_percentage=0.6909,
            credits_used=0.1059,
        )
    )

    assert translated is not None
    assert translated.kind == "usage"
    assert translated.payload["tokens"] == 1381
    assert translated.payload["contextTokens"] == 1381
    assert translated.payload["contextPercent"] == 0.6909
    assert translated.payload["creditsUsed"] == 0.1059
