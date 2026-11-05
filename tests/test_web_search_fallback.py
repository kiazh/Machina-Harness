"""Search survives an unavailable primary search service."""

import base64

import httpx
import pytest

from machina.tools import builtins


def _bing_link(url):
    encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"https://www.bing.com/ck/a?u=a1{encoded}"


@pytest.mark.asyncio
async def test_search_falls_back_to_bing_and_returns_public_source_urls(monkeypatch):
    calls = []
    monkeypatch.setattr(builtins.config, "get", lambda key: "" if key == "searxng_url" else None)
    html = (
        '<ol id="b_results"><li class="b_algo"><h2><a href="'
        + _bing_link("https://docs.python.org/3/")
        + '">Python <b>docs</b></a></h2></li>'
        + '<li class="b_algo"><h2><a href="http://[invalid">Bad</a></h2></li>'
        + '<li class="b_algo"><h2><a href="'
        + _bing_link("http://127.0.0.1/admin")
        + '">Internal</a></h2></li></ol>'
    )

    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(429) if "duckduckgo" in url else httpx.Response(200, text=html)

    monkeypatch.setattr(builtins.httpx, "get", get)
    result = await builtins.web_search("Python reference")
    assert "https://docs.python.org/3/" in result
    assert "Python docs" in result
    assert "127.0.0.1" not in result
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_primary_results_do_not_trigger_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(builtins.config, "get", lambda key: "" if key == "searxng_url" else None)

    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(
            200, text='<a class="result__a" href="https://example.com">Example</a>'
        )

    monkeypatch.setattr(builtins.httpx, "get", get)
    assert "https://example.com" in await builtins.web_search("query")
    assert len(calls) == 1
