"""Headless browser verification for generated web artifacts.

The tool is loopback-only and uses Playwright when installed. It returns a
structured JSON report so the agent/verifier can gate completion on real DOM,
console, storage, and responsive behavior instead of an HTTP 200 alone.
"""

from __future__ import annotations

import functools
import json
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from machina.core.exceptions import ToolError
from machina.observability.diagnostics import record_failure
from machina.tools.base import registry

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _validate_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        raise ToolError("url must not be empty")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ToolError("browser_verify only accepts http:// or https:// URLs")
    host = (parsed.hostname or "").strip().lower()
    if host not in _LOOPBACK_HOSTS:
        raise ToolError("browser_verify is loopback-only (localhost or 127.0.0.1)")
    if parsed.username or parsed.password:
        raise ToolError("credentials in browser_verify URLs are not allowed")
    return raw


def _channel_candidates() -> tuple[str | None, ...]:
    configured = os.environ.get("MACHINA_BROWSER_CHANNEL", "").strip()
    if configured:
        return (configured,)
    return ("chrome", "msedge", None)


async def _launch_browser(playwright):
    last_error: Exception | None = None
    for channel in _channel_candidates():
        try:
            if channel:
                return await playwright.chromium.launch(headless=True, channel=channel)
            return await playwright.chromium.launch(headless=True)
        except Exception as error:  # try the next installed channel
            last_error = error
    raise ToolError(f"no usable headless browser: {last_error}")


def _check_result(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "ok": bool(ok), "detail": detail}


async def _run_checks(page, checks: list[dict[str, Any]], timeout_ms: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(checks):
        if not isinstance(raw, dict):
            results.append(_check_result(f"check[{index}]", False, "check must be an object"))
            continue
        action = str(raw.get("action") or raw.get("type") or "").strip()
        name = str(raw.get("name") or action or f"check[{index}]")
        timeout = max(1, min(int(raw.get("timeout_ms") or timeout_ms), 120_000))
        try:
            if action == "click":
                selector = str(raw.get("selector") or "")
                if not selector:
                    raise ValueError("click requires selector")
                await page.click(selector, timeout=timeout)
                results.append(_check_result(name, True, f"clicked {selector}"))
            elif action == "fill":
                selector = str(raw.get("selector") or "")
                value = str(raw.get("value") or "")
                if not selector:
                    raise ValueError("fill requires selector")
                await page.fill(selector, value, timeout=timeout)
                results.append(_check_result(name, True, f"filled {selector}"))
            elif action == "press":
                key = str(raw.get("key") or raw.get("value") or "")
                if not key:
                    raise ValueError("press requires key")
                selector = str(raw.get("selector") or "")
                if selector:
                    await page.press(selector, key, timeout=timeout)
                else:
                    await page.keyboard.press(key)
                results.append(_check_result(name, True, f"pressed {key}"))
            elif action in {"wait", "wait_for_selector"}:
                selector = str(raw.get("selector") or "")
                if not selector:
                    await page.wait_for_timeout(min(timeout, 10_000))
                    results.append(_check_result(name, True, "waited"))
                else:
                    await page.wait_for_selector(selector, timeout=timeout)
                    results.append(_check_result(name, True, f"visible {selector}"))
            elif action == "reload":
                await page.reload(wait_until="domcontentloaded", timeout=timeout)
                results.append(_check_result(name, True, "reloaded"))
            elif action == "set_viewport":
                width = int(raw.get("width") or 1280)
                height = int(raw.get("height") or 800)
                await page.set_viewport_size({"width": width, "height": height})
                results.append(_check_result(name, True, f"viewport {width}x{height}"))
            elif action in {"assert_selector", "expect_selector"}:
                selector = str(raw.get("selector") or "")
                if not selector:
                    raise ValueError("assert_selector requires selector")
                await page.wait_for_selector(selector, timeout=timeout)
                results.append(_check_result(name, True, f"found {selector}"))
            elif action in {"assert_text", "expect_text"}:
                text = str(raw.get("text") or raw.get("expected") or raw.get("value") or "")
                if not text:
                    raise ValueError("assert_text requires text")
                body = await page.inner_text("body")
                results.append(_check_result(name, text in body, f"text {text!r}"))
            elif action in {"assert_attribute", "expect_attribute"}:
                selector = str(raw.get("selector") or "")
                attribute = str(raw.get("attribute") or "")
                expected = str(raw.get("expected") or raw.get("value") or "")
                if not selector or not attribute:
                    raise ValueError("assert_attribute requires selector and attribute")
                actual = await page.get_attribute(selector, attribute)
                results.append(
                    _check_result(name, actual == expected, f"{attribute}={actual!r}")
                )
            elif action in {"assert_local_storage", "expect_local_storage"}:
                key = str(raw.get("key") or raw.get("name") or "")
                expected = str(raw.get("expected") or raw.get("value") or "")
                if not key:
                    raise ValueError("assert_local_storage requires key")
                actual = await page.evaluate("(key) => window.localStorage.getItem(key)", key)
                results.append(_check_result(name, actual == expected, f"{key}={actual!r}"))
            elif action in {"assert_url", "expect_url"}:
                expected = str(raw.get("expected") or raw.get("value") or "")
                actual = page.url
                results.append(_check_result(name, expected in actual, actual))
            elif action == "screenshot":
                await page.screenshot(full_page=bool(raw.get("full_page", True)))
                results.append(_check_result(name, True, "screenshot captured"))
            else:
                results.append(_check_result(name, False, f"unknown action {action!r}"))
        except Exception as error:
            results.append(_check_result(name, False, f"{type(error).__name__}: {error}"))
    return results


async def _verify(
    url: str,
    checks: list[dict[str, Any]],
    *,
    timeout_ms: int,
    viewport: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "ok": False,
            "status": "unavailable",
            "url": url,
            "error": "Playwright is not installed",
            "install": "pip install 'Machina[browser]' && python -m playwright install chromium",
        }

    started = time.monotonic()
    raw_console_errors: list[str] = []
    page_errors: list[str] = []
    failed_responses: list[str] = []
    browser = None
    playwright = None
    try:
        playwright = await async_playwright().start()
        browser = await _launch_browser(playwright)
        page = await browser.new_page(viewport=viewport or {"width": 1280, "height": 800})
        page.on(
            "console",
            lambda message: raw_console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "response",
            lambda response: failed_responses.append(
                f"{response.status} {response.url}"
            )
            if response.status >= 400 and "favicon.ico" not in response.url
            else None,
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        body = (await page.inner_text("body")).strip()
        results = await _run_checks(page, checks, timeout_ms)
        if not body:
            results.insert(0, _check_result("page_has_body", False, "body is empty"))
        else:
            results.insert(0, _check_result("page_has_body", True, f"{len(body)} chars"))
        # Chromium emits a generic "Failed to load resource" console line for
        # a missing favicon before the page has any app code. Keep that noise
        # out only when there is no real failed response; app asset failures
        # stay visible through failed_responses.
        console_errors = [
            text
            for text in raw_console_errors
            if not (text.startswith("Failed to load resource") and not failed_responses)
        ]
        ok = (
            all(result.get("ok") for result in results)
            and not console_errors
            and not page_errors
            and not failed_responses
        )
        return {
            "ok": ok,
            "status": "passed" if ok else "failed",
            "url": page.url,
            "title": await page.title(),
            "checks": results,
            "console_errors": console_errors[-20:],
            "page_errors": page_errors[-20:],
            "failed_responses": failed_responses[-20:],
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as error:
        record_failure("tools.browser_verify", error)
        return {
            "ok": False,
            "status": "error",
            "url": url,
            "error": f"{type(error).__name__}: {error}",
            "console_errors": raw_console_errors[-20:],
            "page_errors": page_errors[-20:],
            "failed_responses": failed_responses[-20:],
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        try:
            if browser is not None:
                await browser.close()
        finally:
            if playwright is not None:
                await playwright.stop()


@registry.register(
    name="browser_verify",
    description=(
        "Run a real headless-browser check against a local web app. Verifies "
        "DOM, clicks, form input, reload/localStorage persistence, console/page "
        "errors, and responsive viewports. Loopback URLs only."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Loopback URL, e.g. http://127.0.0.1:8000",
            },
            "checks": {
                "type": "array",
                "description": "Ordered browser actions and assertions",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "action": {
                            "type": "string",
                            "description": (
                                "click|fill|press|wait|reload|set_viewport|"
                                "assert_selector|assert_text|assert_attribute|"
                                "assert_local_storage|assert_url|screenshot"
                            ),
                        },
                        "selector": {"type": "string"},
                        "key": {"type": "string"},
                        "attribute": {"type": "string"},
                        "text": {"type": "string"},
                        "expected": {"type": "string"},
                        "value": {"type": "string"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                        "timeout_ms": {"type": "integer"},
                    },
                    "additionalProperties": True,
                },
            },
            "viewport": {
                "type": "object",
                "properties": {
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 120000},
        },
        "required": ["url"],
    },
)
async def browser_verify(
    url: str,
    checks: list[dict[str, Any]] | None = None,
    viewport: dict[str, Any] | None = None,
    timeout_ms: int = 15_000,
) -> str:
    """Verify local web behavior with a real headless browser."""
    safe_url = _validate_url(url)
    timeout = max(100, min(int(timeout_ms or 15_000), 120_000))
    report = await _verify(
        safe_url,
        list(checks or []),
        timeout_ms=timeout,
        viewport=viewport,
    )
    return json.dumps(report, ensure_ascii=False)


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


@registry.register(
    name="web_verify",
    description=(
        "Start a temporary loopback static server for a built web artifact and "
        "run a real headless-browser check. Use this as the verification step for "
        "portfolio pages, dashboards, and other HTML/CSS/JS deliverables."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace-relative directory containing index.html (default .)",
            },
            "checks": {
                "type": "array",
                "description": "Optional browser actions/assertions; same shape as browser_verify",
                "items": {"type": "object", "additionalProperties": True},
            },
            "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 120000},
        },
        "additionalProperties": False,
    },
)
async def web_verify(
    path: str = ".",
    checks: list[dict[str, Any]] | None = None,
    timeout_ms: int = 15_000,
) -> str:
    """Serve a local web artifact and verify it in a real headless browser."""
    from machina.tools.file import resolve_path

    root = resolve_path(path or ".")
    if not root.is_dir():
        raise ToolError(f"web_verify path is not a directory: {path}")
    if not (root / "index.html").is_file():
        raise ToolError("web_verify requires index.html in the target directory")
    handler = functools.partial(_QuietStaticHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        report = await _verify(
            url,
            list(checks or []),
            timeout_ms=max(100, min(int(timeout_ms or 15_000), 120_000)),
            viewport=None,
        )
        report["root"] = str(root)
        return json.dumps(report, ensure_ascii=False)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


__all__ = ["browser_verify", "web_verify"]
