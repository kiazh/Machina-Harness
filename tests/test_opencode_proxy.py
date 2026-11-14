"""Loopback OpenCode proxy contract."""

from __future__ import annotations

from fastapi.testclient import TestClient

from machina.core.provider.opencode_proxy import _openai_usage, create_app


def test_proxy_health_advertises_text_only() -> None:
    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["tool_calls"] is False
    assert response.json()["transport"] == "opencode-session"


def test_proxy_rejects_tools_with_actionable_error() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "deepseek/deepseek-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "x"}}],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "tools_unsupported"


def test_openai_usage_maps_cache_details() -> None:
    usage = _openai_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "total_tokens": 105,
            "cache_read_tokens": 60,
        }
    )

    assert usage["prompt_tokens_details"]["cached_tokens"] == 60
