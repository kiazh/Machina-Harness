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


def test_compact_report_keeps_failures_and_drops_passing_noise() -> None:
    report = browser._compact_report(
        {
            "ok": False,
            "status": "failed",
            "url": "http://127.0.0.1:8000/",
            "checks": [
                {"check": "hero", "ok": True, "detail": "found"},
                {"check": "filter", "ok": False, "detail": "timed out"},
            ],
            "console_errors": ["boom"],
            "duration_ms": 42,
        }
    )

    assert report["checks_total"] == 2
    assert report["checks_passed"] == 1
    assert report["failed_checks"] == [{"check": "filter", "detail": "timed out"}]
    assert report["console_errors"] == ["boom"]
    assert "rerun web_verify" in report["next"]
    assert "checks" not in report


def test_verification_and_artifact_results_are_not_preview_capped() -> None:
    from machina.core.prompt import tool_message_limit

    assert tool_message_limit("web_verify") >= 9000
    assert tool_message_limit("browser_verify") >= 9000
    assert tool_message_limit("artifact_read") >= 9000
    assert tool_message_limit("artifact_search") >= 9000


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
