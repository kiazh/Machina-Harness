"""Provider failures name the cause instead of pointing at server logs."""

from __future__ import annotations

from types import SimpleNamespace

from httpx import HTTPStatusError, Request, Response

from machina.core.agent import _provider_failure_detail
from machina.core.turn_flow import failed_response


def test_http_402_names_status_provider_model_and_fix():
    request = Request("POST", "https://api.deepseek.com/chat/completions")
    error = HTTPStatusError(
        "Client error '402 Payment Required'",
        request=request,
        response=Response(402, request=request),
    )
    agent = SimpleNamespace(provider=SimpleNamespace(name="deepseek", model="deepseek-v4-pro"))

    detail = _provider_failure_detail(error, agent)

    assert "HTTP 402" in detail
    assert "deepseek/deepseek-v4-pro" in detail
    assert "Add credits" in detail
    response = failed_response([], 0, 1, detail)
    assert response.outcome == "failed"
    assert response.content.startswith("LLM provider error: HTTP 402")
    assert "Check server logs" not in response.content


def test_http_401_points_at_the_credential():
    request = Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    error = HTTPStatusError(
        "Client error '401 Unauthorized'",
        request=request,
        response=Response(401, request=request),
    )
    agent = SimpleNamespace(provider=SimpleNamespace(model="minimax/minimax-m3.1-flash:free"))

    detail = _provider_failure_detail(error, agent)

    assert "HTTP 401" in detail
    assert "Check the provider credential" in detail
