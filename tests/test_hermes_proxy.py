"""Hermes proxy detection, lifecycle, and provider catalog."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from machina.core import hermes_proxy


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    monkeypatch.setattr(hermes_proxy, "_process", None)
    monkeypatch.setattr(hermes_proxy, "_process_port", None)
    monkeypatch.setattr(hermes_proxy, "_last_error", None)
    monkeypatch.setattr(hermes_proxy, "_cache", None)
    monkeypatch.setattr(hermes_proxy, "_cache_at", 0.0)
    monkeypatch.setattr(hermes_proxy, "_login_watch_started", False)
    # Never touch a developer's real proxy on the default port.
    monkeypatch.setattr(hermes_proxy, "reaches", lambda timeout=1.5: False)
    yield


def test_ready_providers_parses_status_rows(monkeypatch):
    monkeypatch.setattr(hermes_proxy, "find_binary", lambda: "/usr/bin/hermes")
    monkeypatch.setattr(
        hermes_proxy,
        "_status_text",
        lambda _binary: (
            "[nous    ] Nous Portal — not logged in\n"
            "[xai     ] xAI Grok OAuth — signed in and ready\n"
        ),
    )
    assert hermes_proxy.ready_providers() == ["xai"]


def test_ready_providers_accepts_any_non_signed_out_state(monkeypatch):
    monkeypatch.setattr(hermes_proxy, "find_binary", lambda: "/usr/bin/hermes")
    monkeypatch.setattr(
        hermes_proxy,
        "_status_text",
        lambda _binary: (
            "[nous    ] Nous Portal — authenticated\n[xai     ] xAI Grok OAuth — token expired\n"
        ),
    )
    assert hermes_proxy.ready_providers() == ["nous"]


def test_start_adopts_a_live_owned_process_instead_of_respawning(monkeypatch):
    monkeypatch.setattr(hermes_proxy, "find_binary", lambda: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_proxy, "reaches", lambda timeout=1.5: False)
    monkeypatch.setattr(hermes_proxy, "ready_providers", lambda: ["nous"])
    monkeypatch.setattr(hermes_proxy, "_choose_provider", lambda _ready: "nous")

    class _Live:
        pid = 777

        def poll(self):
            return None

    live = _Live()
    monkeypatch.setattr(hermes_proxy, "_process", live)
    monkeypatch.setattr(hermes_proxy, "_process_port", hermes_proxy.port())
    launched: list[object] = []
    monkeypatch.setattr(hermes_proxy.subprocess, "Popen", lambda *a, **k: launched.append(a))

    # wait=False: adoption is observable through the returned message/state.
    ok, message = hermes_proxy.start(wait=False)
    assert ok is True
    assert "starting" in message
    assert launched == []
    assert hermes_proxy._process is live
    hermes_proxy._process = None


def test_start_refuses_when_no_upstream_is_signed_in(monkeypatch, tmp_path):
    monkeypatch.setattr(hermes_proxy, "find_binary", lambda: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_proxy, "reaches", lambda timeout=1.5: False)
    monkeypatch.setattr(
        hermes_proxy,
        "_status_text",
        lambda _binary: "[nous] not logged in\n[xai] not logged in\n",
    )
    ok, message = hermes_proxy.start(wait=False)
    assert ok is False
    assert "no proxy upstream is signed in" in message


def test_start_launches_the_ready_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(hermes_proxy, "find_binary", lambda: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_proxy, "reaches", lambda timeout=1.5: False)
    monkeypatch.setattr(hermes_proxy, "_log_path", lambda: tmp_path / "hermes.log")
    monkeypatch.setattr(
        hermes_proxy,
        "_status_text",
        lambda _binary: "[xai] xAI Grok OAuth — ready\n",
    )
    launched: dict = {}

    class _Process:
        pid = 4242

        def poll(self):
            return None

    def _popen(argv, **kwargs):
        launched["argv"] = argv
        launched["kwargs"] = kwargs
        return _Process()

    monkeypatch.setattr(hermes_proxy.subprocess, "Popen", _popen)
    ok, message = hermes_proxy.start(wait=False)
    assert ok is True
    assert "xai" in message
    assert launched["argv"][:4] == ["/usr/bin/hermes", "proxy", "start", "--provider"]
    assert launched["argv"][4] == "xai"
    assert "--host" in launched["argv"] and "127.0.0.1" in launched["argv"]
    hermes_proxy._process = None


def test_fetch_models_parses_openai_catalog(monkeypatch):
    monkeypatch.setattr(hermes_proxy, "reaches", lambda timeout=1.5: True)
    monkeypatch.setattr(hermes_proxy, "_cache", None)
    monkeypatch.setattr(hermes_proxy, "_cache_at", 0.0)

    def _get(url, **kwargs):
        assert url.endswith("/v1/models")
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {"id": "grok-4.7", "owned_by": "xai"},
                    {"id": "grok-4.7", "owned_by": "xai"},
                    {"id": "nous-hermes-5", "owned_by": "nous"},
                ]
            },
        )

    monkeypatch.setattr(hermes_proxy.httpx, "get", _get)
    rows = hermes_proxy.fetch_models(force=True)
    assert [row["id"] for row in rows] == ["grok-4.7", "nous-hermes-5"]
    assert rows[0]["displayName"] == "grok-4.7"
    assert rows[0]["source"] == "live"


def test_provider_factory_builds_the_managed_endpoint(monkeypatch):
    from machina.core.provider import factory

    monkeypatch.setattr(hermes_proxy, "start", lambda **_kwargs: (True, "ready"))
    monkeypatch.setattr(hermes_proxy, "base_url", lambda: "http://127.0.0.1:9999/v1")
    monkeypatch.setattr(hermes_proxy, "default_model", lambda: "grok-4.7")
    provider = factory.get_provider(provider="hermes", model=None)
    assert provider.model == "grok-4.7"
    assert provider.spec.base_url == "http://127.0.0.1:9999/v1"


def test_provider_factory_canonicalizes_legacy_hermes_and_free_suffix(monkeypatch):
    from machina.core.provider import factory

    monkeypatch.setattr(hermes_proxy, "start", lambda **_kwargs: (True, "ready"))
    monkeypatch.setattr(hermes_proxy, "base_url", lambda: "http://127.0.0.1:9999/v1")
    monkeypatch.setattr(hermes_proxy, "cached_models", lambda: [{"id": "qwen/qwen4-max"}])
    provider = factory.get_provider(provider="hermes-proxy", model="qwen/qwen4-max:free")
    assert provider.model == "qwen/qwen4-max"
    assert provider.spec.name == "hermes"


def test_schedule_autostart_respects_disabled_and_no_scheduler(monkeypatch):
    from machina.core.config import config

    monkeypatch.setenv("MACHINA_GATEWAY_NO_SCHEDULER", "1")
    assert hermes_proxy.schedule_autostart() is None
    monkeypatch.delenv("MACHINA_GATEWAY_NO_SCHEDULER", raising=False)
    monkeypatch.setitem(config._session_overrides, "hermes_proxy_autostart", False)
    assert hermes_proxy.schedule_autostart() is None


def test_install_uses_the_official_installer(monkeypatch):
    state = {"installed": False}
    monkeypatch.setattr(hermes_proxy, "installed", lambda: state["installed"])
    monkeypatch.setattr(hermes_proxy, "find_binary", lambda: "/usr/local/bin/hermes")
    monkeypatch.setattr(
        hermes_proxy.shutil, "which", lambda name: "/usr/bin/curl" if name == "curl" else None
    )
    calls: list[list[str]] = []

    def _run(argv, **kwargs):
        calls.append(list(argv))
        state["installed"] = True
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(hermes_proxy.subprocess, "run", _run)
    ok, message = hermes_proxy.install(wait=True)
    assert ok is True
    assert "installed" in message.lower()
    installer_url = hermes_proxy.INSTALL_PS1_URL if os.name == "nt" else hermes_proxy.INSTALL_SH_URL
    assert calls and installer_url in " ".join(calls[0])


def test_setup_starts_background_install_without_blocking(monkeypatch):
    started: list[object] = []

    class _Thread:
        def __init__(self, target=None, args=(), **_kwargs):
            started.append((target, args))

        def start(self):
            return None

    monkeypatch.setattr(hermes_proxy, "installed", lambda: False)
    monkeypatch.setattr(hermes_proxy, "install", lambda **_kwargs: (True, "installing"))
    monkeypatch.setattr(hermes_proxy.threading, "Thread", _Thread)
    ok, message = hermes_proxy.setup(interactive=False)
    assert ok is True
    assert "installing hermes" in message.lower()
    assert started and started[0][1] == ("nous",)


def test_setup_without_a_window_returns_the_single_command(monkeypatch):
    from machina.core.config import config

    monkeypatch.setattr(hermes_proxy, "installed", lambda: True)
    monkeypatch.setattr(hermes_proxy, "ready_providers", lambda: [])
    monkeypatch.setattr(hermes_proxy, "_login_argv", lambda provider: ["/usr/bin/hermes", "portal"])
    opened: list[list[str]] = []
    monkeypatch.setattr(
        hermes_proxy,
        "_open_login_terminal",
        lambda argv: opened.append(argv) or (True, "opened"),
    )
    monkeypatch.setitem(config._session_overrides, "hermes_proxy_provider", "nous")

    ok, message = hermes_proxy.setup(interactive=False, open_window=False)

    assert ok is False
    assert opened == []
    assert "/usr/bin/hermes portal" in message


def test_setup_interactive_installs_inline_then_signs_in(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(hermes_proxy, "installed", lambda: False)
    monkeypatch.setattr(
        hermes_proxy,
        "install",
        lambda **kwargs: calls.append(("install", kwargs)) or (True, "installed"),
    )
    monkeypatch.setattr(hermes_proxy, "ready_providers", lambda: [])
    monkeypatch.setattr(hermes_proxy, "start", lambda **_kwargs: (True, "started"))
    monkeypatch.setattr(
        hermes_proxy,
        "_login_argv",
        lambda provider: ["/usr/bin/hermes", "auth", "add", "nous", "--type", "oauth"],
    )

    def _run(argv, timeout=None):
        calls.append(("login", argv))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(hermes_proxy.subprocess, "run", _run)

    ok, message = hermes_proxy.setup(interactive=True)

    assert ok is False
    assert "did not report a ready upstream" in message
    assert calls[0] == ("install", {"wait": True})
    assert calls[1][0] == "login"


def test_setup_opens_the_login_window_when_installed(monkeypatch):
    opened: list[list[str]] = []
    monkeypatch.setattr(hermes_proxy, "installed", lambda: True)
    monkeypatch.setattr(hermes_proxy, "ready_providers", lambda: [])
    monkeypatch.setattr(hermes_proxy, "_login_argv", lambda provider: ["/usr/bin/hermes", "portal"])
    monkeypatch.setattr(
        hermes_proxy,
        "_open_login_terminal",
        lambda argv: opened.append(argv) or (True, "Sign-in window opened."),
    )

    class _Thread:
        def __init__(self, target=None, args=(), **_kwargs):
            self.target = target

        def start(self):
            return None

    monkeypatch.setattr(hermes_proxy.threading, "Thread", _Thread)
    ok, message = hermes_proxy.setup(interactive=False)
    assert ok is True
    assert opened == [["/usr/bin/hermes", "portal"]]
    assert "sign-in" in message.lower()


@pytest.mark.asyncio
async def test_gateway_fast_catalog_accepts_an_external_running_proxy(monkeypatch):
    from machina.gateway.features.providers_feature import _hermes_models

    monkeypatch.setattr(hermes_proxy, "cached_models", lambda: None)
    monkeypatch.setattr(hermes_proxy, "reaches", lambda timeout=1.5: True)
    monkeypatch.setattr(hermes_proxy, "fetch_models", lambda force=False: [{"id": "m"}])
    payload = await _hermes_models("hermes", False, fast=True)
    assert payload["models"] == [{"id": "m"}]


@pytest.mark.asyncio
async def test_gateway_setup_handler_is_bounded_and_serializes_status(monkeypatch):
    from machina.gateway.errors import RpcError
    from machina.gateway.features.auth_hermes import auth_hermes_setup

    seen: dict[str, object] = {}

    def _setup(provider, **kwargs):
        seen["provider"] = provider
        seen["kwargs"] = kwargs
        return True, "installing"

    monkeypatch.setattr(hermes_proxy, "setup", _setup)
    monkeypatch.setattr(hermes_proxy, "status", lambda: {"installed": False, "running": False})
    result = await auth_hermes_setup(object(), {"provider": "xai"})
    assert result["started"] is True
    assert result["status"]["running"] is False
    assert seen["provider"] == "xai"
    assert seen["kwargs"] == {"interactive": False}

    with pytest.raises(RpcError):
        await auth_hermes_setup(object(), {"provider": "openai"})


def test_config_validates_upstream_and_clamps_the_port(monkeypatch):
    from machina.core.config import config, validate_value

    with pytest.raises(ValueError):
        validate_value("hermes_proxy_provider", "openai")
    assert validate_value("hermes_proxy_provider", "XAI") == "xai"
    monkeypatch.setitem(config._session_overrides, "hermes_proxy_port", 99999)
    assert hermes_proxy.port() == hermes_proxy.DEFAULT_PORT


def test_missing_hermes_is_actionable(monkeypatch):
    monkeypatch.setattr(hermes_proxy, "find_binary", lambda: None)
    ok, message = hermes_proxy.start(wait=False)
    assert ok is False
    assert "Hermes is not installed" in message
    assert hermes_proxy.last_error() == message
