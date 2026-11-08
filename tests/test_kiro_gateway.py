"""Kiro local-gateway lifecycle: bounded startup, no request-path blocking."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from machina.core.config import config
from machina.core.provider import kiro_gateway, kiro_proxy
from machina.core.provider.usage_map import parse_openai_usage
from machina.gateway.errors import OPERATION_FAILED, RpcError
from machina.gateway.features import METHODS
from machina.gateway.features.auth_kiro import (
    auth_kiro_fallback,
    auth_kiro_start,
    auth_kiro_status,
    auth_kiro_stop,
    auth_kiro_usage,
)


def _set_config(monkeypatch: pytest.MonkeyPatch, key: str, value: object) -> None:
    monkeypatch.setitem(config._session_overrides, key, value)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _set_config(monkeypatch, "machina_home", str(tmp_path / "home"))
    _set_config(monkeypatch, "kiro_gateway_dir", "")
    _set_config(monkeypatch, "kiro_gateway_command", "")
    _set_config(monkeypatch, "kiro_autostart", True)
    monkeypatch.delenv("KIRO_CLI_DB_FILE", raising=False)
    monkeypatch.delenv("PROFILE_ARN", raising=False)
    kiro_gateway._reset_for_tests()
    yield
    kiro_gateway._reset_for_tests()


def _checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "kiro-gateway"
    checkout.mkdir()
    (checkout / "main.py").write_text("print('gateway')\n", encoding="utf-8")
    return checkout


class _FakeProcess:
    pid = 4242

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return int(self.returncode or 0)


def test_detect_launch_uses_the_checkout_venv(tmp_path, monkeypatch) -> None:
    checkout = _checkout(tmp_path)
    python = checkout / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    _set_config(monkeypatch, "kiro_gateway_dir", str(checkout))

    spec, error = kiro_gateway.detect_launch()

    assert error is None
    assert spec is not None
    assert spec.cwd == checkout
    assert spec.argv == (str(python), str(checkout / "main.py"))


def test_detect_launch_is_actionable_when_the_checkout_is_missing(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "absent"
    _set_config(monkeypatch, "kiro_gateway_dir", str(missing))

    spec, error = kiro_gateway.detect_launch()

    assert spec is None
    assert str(missing) in (error or "")


def test_login_detection_uses_only_the_credential_file_presence(tmp_path, monkeypatch) -> None:
    database = tmp_path / "data.sqlite3"
    database.write_bytes(b"sqlite")
    monkeypatch.setenv("KIRO_CLI_DB_FILE", str(database))

    assert kiro_gateway.login_database_path() == database
    assert kiro_gateway.login_detected() is True


def test_profile_arn_uses_explicit_env_first(monkeypatch) -> None:
    monkeypatch.setenv(
        "PROFILE_ARN", "arn:aws:codewhisperer:us-east-1:111111111111:profile/env"
    )

    assert (
        kiro_gateway.profile_arn()
        == "arn:aws:codewhisperer:us-east-1:111111111111:profile/env"
    )


def test_profile_arn_is_read_from_kiro_ide_state(tmp_path, monkeypatch) -> None:
    app_data = tmp_path / "AppData"
    profile = (
        app_data
        / "Kiro"
        / "User"
        / "globalStorage"
        / "kiro.kiroagent"
        / "profile.json"
    )
    profile.parent.mkdir(parents=True)
    profile.write_text(
        json.dumps(
            {"arn": "arn:aws:codewhisperer:us-east-1:222222222222:profile/ide"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(app_data))

    assert (
        kiro_gateway.profile_arn()
        == "arn:aws:codewhisperer:us-east-1:222222222222:profile/ide"
    )


def test_spawn_injects_utf8_and_profile_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "PROFILE_ARN", "arn:aws:codewhisperer:us-east-1:333333333333:profile/spawn"
    )
    captured: dict[str, Any] = {}

    class _CapturePopen(_FakeProcess):
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            super().__init__()
            captured["argv"] = argv
            captured.update(kwargs)

    monkeypatch.setattr(kiro_gateway.subprocess, "Popen", _CapturePopen)
    spec = kiro_gateway.LaunchSpec(("python", "main.py"), tmp_path, "configured")

    kiro_gateway._spawn(spec)

    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert (
        captured["env"]["PROFILE_ARN"]
        == "arn:aws:codewhisperer:us-east-1:333333333333:profile/spawn"
    )


def test_spawn_does_not_override_a_checkout_profile_arn(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        'PROFILE_ARN="arn:aws:codewhisperer:us-east-1:444444444444:profile/envfile"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        kiro_gateway,
        "profile_arn",
        lambda: "arn:aws:codewhisperer:us-east-1:555555555555:profile/ide",
    )
    captured: dict[str, Any] = {}

    class _CapturePopen(_FakeProcess):
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(kiro_gateway.subprocess, "Popen", _CapturePopen)
    spec = kiro_gateway.LaunchSpec(("python", "main.py"), tmp_path, "configured")

    kiro_gateway._spawn(spec)

    assert "PROFILE_ARN" not in captured["env"]


def test_spawn_injects_a_detected_profile_when_unconfigured(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        kiro_gateway,
        "profile_arn",
        lambda: "arn:aws:codewhisperer:us-east-1:666666666666:profile/detected",
    )
    captured: dict[str, Any] = {}

    class _CapturePopen(_FakeProcess):
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(kiro_gateway.subprocess, "Popen", _CapturePopen)
    spec = kiro_gateway.LaunchSpec(("python", "main.py"), tmp_path, "configured")

    kiro_gateway._spawn(spec)

    assert (
        captured["env"]["PROFILE_ARN"]
        == "arn:aws:codewhisperer:us-east-1:666666666666:profile/detected"
    )


def test_status_is_fast_and_does_not_run_the_cli(tmp_path, monkeypatch) -> None:
    _set_config(monkeypatch, "kiro_gateway_dir", str(_checkout(tmp_path)))
    monkeypatch.setattr(kiro_proxy, "gateway_reachable", lambda *_a, **_k: False)
    monkeypatch.setattr(kiro_proxy, "cli_path", lambda: "")

    started = time.monotonic()
    report = kiro_gateway.status()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert report["state"] == "stopped"
    assert report["reachable"] is False
    assert report["managed"] is False
    assert report["launchConfigured"] is True
    assert report["autostart"] is True


def test_fallback_route_honors_explicit_configuration(monkeypatch) -> None:
    _set_config(monkeypatch, "kiro_fallback", "deepseek:deepseek-v4-flash")
    monkeypatch.setattr(kiro_gateway, "_provider_available", lambda provider: provider == "deepseek")

    assert kiro_gateway.fallback_route() == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "reason": None,
    }


def test_fallback_route_auto_picks_a_ready_provider(monkeypatch) -> None:
    _set_config(monkeypatch, "kiro_fallback", "")
    monkeypatch.setattr(kiro_gateway, "_provider_available", lambda provider: provider == "chatgpt")

    route = kiro_gateway.fallback_route()

    assert route["provider"] == "chatgpt"
    assert route["model"] == "gpt-6.1-sol"
    assert route["reason"] is None


def test_fallback_route_is_honest_when_nothing_is_ready(monkeypatch) -> None:
    _set_config(monkeypatch, "kiro_fallback", "")
    monkeypatch.setattr(kiro_gateway, "_provider_available", lambda _provider: False)

    route = kiro_gateway.fallback_route()

    assert route["provider"] is None
    assert route["model"] is None
    assert "MACHINA_KIRO_FALLBACK" in (route["reason"] or "")


def test_usage_reads_the_gateway_admin_endpoint(monkeypatch) -> None:
    response = {
        "object": "list",
        "accounts": [
            {
                "account_id": "acct_test",
                "usage": {
                    "subscription_title": "KIRO STUDENT",
                    "subscription_type": "Q_DEVELOPER_STANDALONE_STUDENT",
                    "current_usage": 37.5,
                    "usage_limit": 1000.0,
                    "remaining": 962.5,
                    "percent_used": 3.75,
                    "unit": "Credits",
                    "next_reset_at": "2026-12-01T00:00:00+00:00",
                },
                "stale": False,
                "error": None,
            }
        ],
    }

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.path == "/admin/accounts/usage"
            assert self.headers.get("Authorization") == "Bearer dummy-local-only"
            body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return None

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    _set_config(monkeypatch, "kiro_base_url", f"http://127.0.0.1:{port}/v1")
    try:
        report = kiro_gateway.usage()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert report["subscription"] == "KIRO STUDENT"
    assert report["used"] == 37.5
    assert report["limit"] == 1000.0
    assert report["remaining"] == 962.5
    assert report["percentUsed"] == 3.75
    assert report["unit"] == "Credits"
    assert report["nextResetAt"] == "2026-12-01T00:00:00+00:00"


def test_kiro_context_usage_extras_survive_normalization() -> None:
    usage = parse_openai_usage(
        {
            "prompt_tokens": 1362,
            "completion_tokens": 16,
            "total_tokens": 1378,
            "context_usage_percentage": 0.6894,
            "context_tokens": 1378,
            "credits_used": 0.1059,
        }
    )

    assert usage["total_tokens"] == 1378
    assert usage["context_usage_percentage"] == 0.6894
    assert usage["context_tokens"] == 1378
    assert usage["credits_used"] == 0.1059


def test_start_spawns_once_and_marks_ready(tmp_path, monkeypatch) -> None:
    checkout = _checkout(tmp_path)
    _set_config(monkeypatch, "kiro_gateway_dir", str(checkout))
    _set_config(monkeypatch, "kiro_base_url", "http://127.0.0.1:9/v1")
    process = _FakeProcess()
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def fake_popen(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        return process

    probe_calls = 0

    def probe(*_args, **_kwargs):
        nonlocal probe_calls
        probe_calls += 1
        return probe_calls > 1

    monkeypatch.setattr(kiro_gateway.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(kiro_gateway, "gateway_reachable", probe)

    result = kiro_gateway.start(wait=1.0)

    assert result["started"] is True
    assert result["ready"] is True
    assert result["status"]["state"] == "running"
    assert calls and calls[0][0][1].endswith("main.py")
    assert calls[0][1]["cwd"] == str(checkout)
    assert process.pid == 4242


def test_stop_only_stops_a_managed_child(tmp_path, monkeypatch) -> None:
    _set_config(monkeypatch, "kiro_gateway_dir", str(_checkout(tmp_path)))
    process = _FakeProcess()
    monkeypatch.setattr(kiro_gateway.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(kiro_gateway, "gateway_reachable", lambda *_a, **_k: True)

    stopped = kiro_gateway.stop()
    assert stopped["stopped"] is False

    # Seed a managed child directly; stop must terminate exactly that process.
    kiro_gateway._process = process
    monkeypatch.setattr(kiro_gateway, "gateway_reachable", lambda *_a, **_k: False)
    stopped = kiro_gateway.stop()

    assert stopped["stopped"] is True
    assert process.terminated is True


def test_autostart_requires_a_detected_login(tmp_path, monkeypatch) -> None:
    _set_config(monkeypatch, "kiro_gateway_dir", str(_checkout(tmp_path)))
    monkeypatch.setattr(kiro_proxy, "gateway_reachable", lambda *_a, **_k: False)
    monkeypatch.setattr(kiro_gateway, "login_detected", lambda: False)

    def start_must_not_run(**_kwargs):
        raise AssertionError("auto-start must wait for a detected Kiro login")

    monkeypatch.setattr(kiro_gateway, "start", start_must_not_run)

    assert kiro_gateway.autostart_if_ready() is None


def test_autostart_uses_a_detected_login_and_launcher(tmp_path, monkeypatch) -> None:
    _set_config(monkeypatch, "kiro_gateway_dir", str(_checkout(tmp_path)))
    monkeypatch.setattr(kiro_proxy, "gateway_reachable", lambda *_a, **_k: False)
    monkeypatch.setattr(kiro_gateway, "login_detected", lambda: True)
    monkeypatch.setattr(
        kiro_gateway,
        "start",
        lambda **_kwargs: {"started": True, "ready": True, "status": {"state": "running"}},
    )

    result = kiro_gateway.autostart_if_ready()

    assert result == {"started": True, "ready": True, "status": {"state": "running"}}


def test_scheduler_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("MACHINA_GATEWAY_NO_SCHEDULER", "1")

    assert kiro_gateway.schedule_autostart() is None


@pytest.mark.asyncio
async def test_scheduler_runs_autostart_off_the_event_loop(monkeypatch) -> None:
    monkeypatch.delenv("MACHINA_GATEWAY_NO_SCHEDULER", raising=False)
    _set_config(monkeypatch, "kiro_autostart", True)
    monkeypatch.setattr(
        kiro_gateway,
        "autostart_if_ready",
        lambda: {"started": True, "ready": True, "status": {"state": "running"}},
    )

    task = kiro_gateway.schedule_autostart()

    assert task is not None
    assert (await task) == {
        "started": True,
        "ready": True,
        "status": {"state": "running"},
    }


@pytest.mark.asyncio
async def test_auth_kiro_handlers_are_registered_and_bounded(monkeypatch) -> None:
    assert "auth.kiro.status" in METHODS
    assert "auth.kiro.start" in METHODS
    assert "auth.kiro.stop" in METHODS
    assert "auth.kiro.usage" in METHODS
    assert "auth.kiro.fallback" in METHODS

    monkeypatch.setattr(
        kiro_gateway,
        "status",
        lambda: {"state": "stopped", "reachable": False},
    )
    assert await auth_kiro_status(object(), {}) == {
        "status": {"state": "stopped", "reachable": False}
    }

    monkeypatch.setattr(
        kiro_gateway,
        "start",
        lambda *, wait: {"started": True, "ready": True, "wait": wait, "status": {}},
    )
    assert (await auth_kiro_start(object(), {"wait": 1.5}))["wait"] == 1.5

    monkeypatch.setattr(
        kiro_gateway,
        "stop",
        lambda: {"stopped": True, "status": {}},
    )
    assert (await auth_kiro_stop(object(), {}))["stopped"] is True

    monkeypatch.setattr(
        kiro_gateway,
        "usage",
        lambda: {"remaining": 962.5, "unit": "Credits"},
    )
    assert (await auth_kiro_usage(object(), {}))["usage"]["remaining"] == 962.5

    monkeypatch.setattr(
        kiro_gateway,
        "fallback_route",
        lambda: {"provider": "chatgpt", "model": "gpt-6.1-sol", "reason": None},
    )
    assert (await auth_kiro_fallback(object(), {}))["provider"] == "chatgpt"


@pytest.mark.asyncio
async def test_auth_kiro_start_surfaces_actionable_failure(monkeypatch) -> None:
    def fail(**_kwargs):
        raise kiro_gateway.KiroGatewayError("set MACHINA_KIRO_GATEWAY_DIR")

    monkeypatch.setattr(kiro_gateway, "start", fail)

    with pytest.raises(RpcError) as caught:
        await auth_kiro_start(object(), {})

    assert caught.value.code == OPERATION_FAILED
    assert "MACHINA_KIRO_GATEWAY_DIR" in caught.value.message


@pytest.mark.asyncio
async def test_real_process_start_and_stop(tmp_path, monkeypatch) -> None:
    """The manager really spawns/kills a loopback listener (not just fakes)."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    checkout = _checkout(tmp_path)
    (checkout / "main.py").write_text(
        "\n".join(
            (
                "from http.server import BaseHTTPRequestHandler, HTTPServer",
                "class Handler(BaseHTTPRequestHandler):",
                "    def do_GET(self):",
                "        body = b'{\"object\":\"list\",\"data\":[{\"id\":\"auto\"}]}'",
                "        self.send_response(200)",
                "        self.send_header('Content-Type', 'application/json')",
                "        self.send_header('Content-Length', str(len(body)))",
                "        self.end_headers()",
                "        self.wfile.write(body)",
                "    def log_message(self, *args):",
                "        pass",
                f"HTTPServer(('127.0.0.1', {port}), Handler).serve_forever()",
            )
        ),
        encoding="utf-8",
    )
    _set_config(monkeypatch, "kiro_gateway_dir", str(checkout))
    _set_config(monkeypatch, "kiro_base_url", f"http://127.0.0.1:{port}/v1")

    result = await asyncio.to_thread(kiro_gateway.start, wait=5.0)
    try:
        assert result["ready"] is True
        assert result["status"]["reachable"] is True
        assert result["status"]["managed"] is True
        rows = await asyncio.to_thread(kiro_proxy.fetch_models)
        assert len(rows) == 22
        assert {row["id"]: row for row in rows}["auto"]["source"] == "live"
    finally:
        stopped = await asyncio.to_thread(kiro_gateway.stop)

    assert stopped["stopped"] is True
    assert stopped["status"]["reachable"] is False
