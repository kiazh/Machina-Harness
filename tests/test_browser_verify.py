"""Browser verification tool contract (real browser optional in CI)."""

from __future__ import annotations

import json

import pytest

from machina.core.exceptions import ToolError
from machina.tools import browser


def test_browser_verify_is_loopback_only() -> None:
    with pytest.raises(ToolError, match="loopback"):
        browser._validate_url("https://example.com")
    assert browser._validate_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert browser._validate_url("http://localhost:3000/app") == "http://localhost:3000/app"


@pytest.mark.asyncio
async def test_browser_verify_returns_structured_report(monkeypatch) -> None:
    seen = {}

    async def fake_verify(url, checks, *, timeout_ms, viewport):
        seen.update(url=url, checks=checks, timeout_ms=timeout_ms, viewport=viewport)
        return {
            "ok": True,
            "status": "passed",
            "url": url,
            "checks": [{"check": "page_has_body", "ok": True}],
            "console_errors": [],
            "page_errors": [],
            "duration_ms": 12,
        }

    monkeypatch.setattr(browser, "_verify", fake_verify)
    payload = json.loads(
        await browser.browser_verify(
            "http://127.0.0.1:8000",
            checks=[{"action": "click", "selector": "#theme", "name": "toggle"}],
            viewport={"width": 375, "height": 812},
            timeout_ms=5000,
        )
    )

    assert payload["ok"] is True
    assert seen["viewport"] == {"width": 375, "height": 812}
    assert seen["checks"][0]["selector"] == "#theme"
    assert seen["timeout_ms"] == 5000
