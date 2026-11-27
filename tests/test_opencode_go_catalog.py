from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from machina.core.provider import opencode_go_catalog as catalog


def _entry(**overrides) -> dict:
    entry = {
        "id": "deepseek-v4.1-flash",
        "object": "model",
        "type": "chat",
        "name": "DeepSeek V4.1 Flash",
        "description": "Fast coding model",
        "family": "deepseek-flash",
        "release_date": "2026-10-17",
        "capabilities": {
            "tools": {"supported": True},
            "reasoning": {"supported": True, "effort": ["low", "high", "max"]},
        },
        "limit": {"context": 1_000_000, "output": 384_000},
    }
    entry.update(overrides)
    return entry


def test_normalize_payload_keeps_chat_models_and_drops_non_chat() -> None:
    rows = catalog.normalize_payload(
        {
            "data": [
                _entry(),
                _entry(id="text-embedding-3-small", type="embedding"),
                _entry(id="jev-1.14", type="decision"),
                _entry(id="deepseek-v4.1-flash", name="duplicate"),
                {"id": ""},
                "not-a-model",
            ]
        }
    )

    assert [row["id"] for row in rows] == ["deepseek-v4.1-flash"]
    assert rows[0] == {
        "id": "deepseek-v4.1-flash",
        "displayName": "DeepSeek V4.1 Flash",
        "description": "Fast coding model",
        "efforts": ["low", "high", "max"],
        "contextWindow": 1_000_000,
        "maxOutputTokens": 384_000,
        "family": "deepseek-flash",
        "tools": True,
        "reasoning": True,
        "releaseDate": "2026-10-17",
        "source": "opencode-go",
    }


def test_bundled_snapshot_lists_the_full_chat_library() -> None:
    rows = catalog.cached_models()
    ids = {row["id"] for row in rows}

    assert len(rows) >= 31
    assert "deepseek-v4.1-flash" in ids
    assert "deepseek-v4-pro" in ids
    assert "kimi-k3" in ids
    assert "text-embedding-3-small" not in ids
    assert rows[0]["id"] == "deepseek-v4.1-flash"


def test_fetch_models_uses_the_stored_account(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [_entry()]}, request=request)

    monkeypatch.setattr(
        "machina.security.opencode_go_auth.load_tokens",
        lambda: {"access_token": "test-token"},
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        rows = catalog.fetch_models(client=client)
    finally:
        client.close()

    assert [row["id"] for row in rows] == ["deepseek-v4.1-flash"]
    assert seen[0].url == "https://opencode.ai/inference/go/v1/models"
    assert seen[0].headers["Authorization"] == "Bearer test-token"


def test_fetch_models_without_account_is_offline() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "machina.security.opencode_go_auth.load_tokens",
        lambda: None,
    )
    try:
        assert catalog.fetch_models() == []
    finally:
        monkeypatch.undo()


def test_models_caches_live_rows_and_falls_back_when_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "opencode-go-models.json"
    monkeypatch.setenv("MACHINA_OPENCODE_GO_CATALOG", str(cache))
    live = [catalog.normalize_entry(_entry())]
    monkeypatch.setattr(catalog, "fetch_models", lambda **kwargs: live)

    first = catalog.models(now=1_000.0)
    assert [row["id"] for row in first] == ["deepseek-v4.1-flash"]
    stored = json.loads(cache.read_text())
    assert stored["fetched_at"] == 1_000.0

    fresh = catalog.models(now=1_000.0 + 60)
    assert [row["id"] for row in fresh] == ["deepseek-v4.1-flash"]

    monkeypatch.setattr(catalog, "fetch_models", lambda **kwargs: [])
    stale = catalog.models(now=1_000.0 + catalog.CACHE_TTL_SECONDS + 1)
    assert [row["id"] for row in stale] == ["deepseek-v4.1-flash"]


def test_context_window_reads_the_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MACHINA_OPENCODE_GO_CATALOG", str(tmp_path / "missing.json"))

    assert catalog.context_window("deepseek-v4.1-flash") == 1_000_000
    assert catalog.context_window("unknown-model") is None


def test_routing_rows_lead_with_the_configured_model() -> None:
    from machina.core.routing import catalog as routing

    rows = routing._rows_for_provider(
        "opencode-go", {"provider": "opencode-go", "model": "deepseek-v4-pro"}
    )
    assert rows[0]["id"] == "deepseek-v4-pro"
    assert len(rows) >= 31

    custom = routing._rows_for_provider(
        "opencode-go", {"provider": "opencode-go", "model": "my-private-route"}
    )
    assert custom[0]["id"] == "my-private-route"
    assert custom[1]["id"] == "deepseek-v4.1-flash"
