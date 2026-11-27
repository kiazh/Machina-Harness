from __future__ import annotations

import json
import stat
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from typer.testing import CliRunner

from machina.cli.auth import app as auth_app
from machina.core.exceptions import ValidationError
from machina.core.provider.factory import get_provider
from machina.core.provider.specs import PROVIDER_SPECS, PROVIDERS
from machina.core.routing import catalog as routing
from machina.security import opencode_go_auth as auth


def _response(request: httpx.Request, payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=request)


def _device() -> dict:
    return {
        "device_code": "device-code-placeholder",
        "user_code": "ABCD-EFGH",
        "verification_uri_complete": "https://opencode.ai/verify?code=ABCD-EFGH",
        "expires_in": 60,
        "interval": 1,
    }


def _login_transport(poll_payloads: list[tuple[int, dict]], calls: list[httpx.Request]):
    polls = iter(poll_payloads)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = urlparse(str(request.url)).path
        if path == "/console/auth/device/code":
            return _response(request, _device())
        if path == "/console/auth/device/token":
            status, payload = next(polls)
            return _response(request, payload, status)
        if path == "/console/api/user":
            return _response(request, {"id": "account-1", "email": "user@example.test"})
        if path == "/console/api/orgs":
            return _response(request, {"orgs": [{"id": "org-1", "name": "Primary"}]})
        raise AssertionError(f"unexpected endpoint path: {path}")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_device_login_uses_verified_json_protocol_and_persists_identity(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    client = _login_transport(
        [
            (400, {"error": "authorization_pending"}),
            (400, {"error": "slow_down"}),
            (
                200,
                {
                    "access_token": "test-access-token",
                    "refresh_token": "test-refresh-token",
                    "expires_in": 3600,
                    "org_id": "org-1",
                },
            ),
        ],
        calls,
    )
    waits: list[float] = []
    printed: list[str] = []
    try:
        saved = auth.login(
            path=tmp_path / "opencode-go.json",
            client=client,
            sleep=waits.append,
            output=printed.append,
        )
    finally:
        client.close()

    device_request = calls[0]
    assert device_request.method == "POST"
    assert json.loads(device_request.content) == {
        "client_id": "opencode-cli",
        "supports_org_scope": True,
    }
    token_requests = [request for request in calls if request.url.path.endswith("/device/token")]
    assert json.loads(token_requests[0].content) == {
        "grant_type": auth.DEVICE_GRANT_TYPE,
        "device_code": "device-code-placeholder",
        "client_id": "opencode-cli",
    }
    assert waits == [1.0, 1.0, 6.0]
    assert any("ABCD-EFGH" in line for line in printed)
    assert saved["email"] == "user@example.test"
    assert saved["org_id"] == "org-1"
    assert saved["org_name"] == "Primary"
    assert saved["account_id"] == "account-1"
    assert stat.S_IMODE((tmp_path / "opencode-go.json").stat().st_mode) == 0o600


def test_device_login_resolves_relative_verification_urls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert urlparse(str(request.url)).path == "/console/auth/device/code"
        return _response(
            request,
            {
                "device_code": "device-code-placeholder",
                "user_code": "ABCD-EFGH",
                "verification_uri": "/console/device",
                "verification_uri_complete": (
                    "/console/device?user_code=ABCD-EFGH&client_id=opencode-cli"
                ),
                "expires_in": 600,
                "interval": 5,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        device = auth.start_device_login(client=client)
    finally:
        client.close()
    assert device["verification_uri"] == "https://opencode.ai/console/device"
    assert device["verification_uri_complete"] == (
        "https://opencode.ai/console/device?user_code=ABCD-EFGH&client_id=opencode-cli"
    )


@pytest.mark.parametrize(
    ("error", "code"),
    [
        ("expired_token", "opencode_go_device_expired_token"),
        ("access_denied", "opencode_go_device_access_denied"),
    ],
)
def test_device_login_terminal_errors_fail_without_persisting(
    tmp_path: Path, error: str, code: str
) -> None:
    client = _login_transport([(400, {"error": error})], [])
    target = tmp_path / "opencode-go.json"
    try:
        with pytest.raises(auth.OpenCodeGoAuthError) as caught:
            auth.login(path=target, client=client, output=lambda _line: None)
    finally:
        client.close()
    assert caught.value.code == code
    assert not target.exists()


def test_device_login_selects_first_org_when_no_chooser(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    client = _login_transport(
        [
            (
                200,
                {
                    "access_token": "test-access-token",
                    "refresh_token": "test-refresh-token",
                    "expires_in": 1800,
                },
            )
        ],
        calls,
    )
    # Replace the single-organization endpoint with a two-org response.
    original = client._transport

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/orgs":
            return _response(
                request, {"orgs": [{"id": "org-first", "name": "First"}, {"id": "org-second"}]}
            )
        return original.handle_request(request)

    client.close()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    lines: list[str] = []
    try:
        saved = auth.login(path=tmp_path / "first-org.json", client=client, output=lines.append)
    finally:
        client.close()
    assert saved["org_id"] == "org-first"
    assert any("using the first" in line for line in lines)


def test_refresh_rotates_token_persists_private_file(tmp_path: Path) -> None:
    target = tmp_path / "opencode-go.json"
    auth.save_tokens(
        {
            "access_token": "old-access-placeholder",
            "refresh_token": "old-refresh-placeholder",
            "expires_at": time.time() + 30,
            "org_id": "org-1",
            "email": "user@example.test",
        },
        target,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/console/auth/device/token"
        assert json.loads(request.content) == {
            "grant_type": "refresh_token",
            "refresh_token": "old-refresh-placeholder",
            "client_id": "opencode-cli",
        }
        return _response(
            request,
            {
                "access_token": "rotated-access-placeholder",
                "refresh_token": "rotated-refresh-placeholder",
                "expires_in": 3600,
                "org_id": "org-2",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        tokens = auth.resolve_tokens(path=target, client=client)
    finally:
        client.close()
    persisted = json.loads(target.read_text())
    assert tokens["access_token"] == "rotated-access-placeholder"
    assert tokens["refresh_token"] == "rotated-refresh-placeholder"
    assert tokens["org_id"] == "org-2"
    assert persisted["access_token"] == "rotated-access-placeholder"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_failed_refresh_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "opencode-go.json"
    auth.save_tokens(
        {
            "access_token": "expired-access-placeholder",
            "refresh_token": "expired-refresh-placeholder",
            "expires_at": time.time() + 1,
            "org_id": "org-1",
        },
        target,
    )
    time.sleep(1.05)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: _response(request, {"error": "invalid_grant"}, status=401)
        )
    )
    try:
        with pytest.raises(auth.OpenCodeGoAuthError) as caught:
            auth.resolve_tokens(path=target, client=client)
    finally:
        client.close()
    assert caught.value.code == "opencode_go_refresh_failed"
    assert "expired-access-placeholder" not in str(caught.value)
    assert json.loads(target.read_text())["access_token"] == "expired-access-placeholder"


def test_factory_uses_go_gateway_and_required_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth,
        "resolve_tokens",
        lambda: {"access_token": "test-access-placeholder", "org_id": "org-test"},
    )
    provider = get_provider("opencode-go")
    try:
        assert provider.client.base_url == httpx.URL("https://opencode.ai/inference/go/v1/")
        headers = provider.client.headers
        assert headers["Authorization"] == "Bearer test-access-placeholder"
        assert headers["x-org-id"] == "org-test"
        assert headers["x-opencode-project"] == "machina"
        assert headers["User-Agent"] == "opencode/1.18.8"
        assert headers["x-opencode-session"]
        assert headers["x-opencode-request"]
        assert provider.model == "deepseek-v4.1-flash"
    finally:
        import asyncio

        asyncio.run(provider.close())


def test_factory_signed_out_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> None:
        raise auth.OpenCodeGoAuthError("Not signed in to OpenCode Go")

    monkeypatch.setattr(auth, "resolve_tokens", missing)
    with pytest.raises(ValidationError, match="machina auth opencode login"):
        get_provider("opencode-go")


def test_routing_readiness_and_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "status", lambda: {"signed_in": False, "expired": False})
    ready, reason = routing._provider_ready("opencode-go", object(), probe_kiro=False)
    assert not ready
    assert reason == "not signed in"

    monkeypatch.setattr(auth, "status", lambda: {"signed_in": True, "expired": False})
    ready, reason = routing._provider_ready("opencode-go", object(), probe_kiro=False)
    assert ready
    assert reason == "account signed in"

    class FakeConfig:
        def get(self, key: str) -> str:
            return {"provider": "opencode-go", "model": "deepseek-go-custom"}[key]

    rows = routing._rows_for_provider("opencode-go", FakeConfig())
    assert rows[0]["id"] == "deepseek-go-custom"
    assert "opencode-go" in PROVIDERS
    assert PROVIDER_SPECS["opencode-go"].requires_key is False


def test_opencode_auth_cli_registration_and_help() -> None:
    runner = CliRunner()
    result = runner.invoke(auth_app, ["opencode", "--help"])
    assert result.exit_code == 0
    assert "login" in result.stdout
    assert "status" in result.stdout
    assert "logout" in result.stdout


def test_opencode_status_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(auth, "status", lambda: {"signed_in": False})
    assert runner.invoke(auth_app, ["opencode", "status", "--json"]).exit_code == 1
    monkeypatch.setattr(auth, "status", lambda: {"signed_in": True})
    assert runner.invoke(auth_app, ["opencode", "status", "--json"]).exit_code == 0
