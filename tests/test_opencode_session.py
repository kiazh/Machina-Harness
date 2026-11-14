"""OpenCode session bridge contracts (no live server required)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from machina.core.provider import opencode_session as bridge


def test_model_ref_splits_provider_and_variant() -> None:
    assert bridge._model_ref("deepseek/deepseek-flash") == {
        "providerID": "deepseek",
        "id": "deepseek-flash",
    }
    assert bridge._model_ref("opencode/big-pickle#max") == {
        "providerID": "opencode",
        "id": "big-pickle",
        "variant": "max",
    }
    assert bridge._model_ref("big-pickle") == {
        "providerID": "opencode",
        "id": "big-pickle",
    }


def test_usage_keeps_uncached_and_cache_split() -> None:
    usage = bridge._usage(
        {
            "tokens": {
                "input": 143,
                "output": 2,
                "cache": {"read": 6400, "write": 0},
            }
        }
    )

    assert usage["prompt_tokens"] == 6543
    assert usage["completion_tokens"] == 2
    assert usage["total_tokens"] == 6545
    assert usage["uncached_input_tokens"] == 143
    assert usage["cache_read_tokens"] == 6400


def test_service_password_reads_override_file(monkeypatch, tmp_path) -> None:
    target = tmp_path / "service.json"
    target.write_text(json.dumps({"password": "local-secret"}), encoding="utf-8")
    monkeypatch.setenv(bridge.SERVICE_ENV, str(target))

    assert bridge.service_password() == "local-secret"


def test_available_models_uses_entitlement_list(monkeypatch) -> None:
    bridge._MODELS_CACHE = None
    monkeypatch.setattr(bridge.shutil, "which", lambda _name: "opencode")
    monkeypatch.setattr(
        bridge.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout="deepseek/deepseek-flash\nopencode/big-pickle\n"
        ),
    )

    assert bridge.available_models(force=True) == (
        "deepseek/deepseek-flash",
        "opencode/big-pickle",
    )
    bridge._MODELS_CACHE = None


def test_bridge_advertises_text_only_capability() -> None:
    from machina.core.usage import provider_identity

    provider = object.__new__(bridge.OpenCodeSessionProvider)
    assert getattr(provider, "supports_tools", False) is False
    assert provider_identity(provider) == "opencode_session"
