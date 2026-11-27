from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest

from machina.gateway.features import METHODS
from machina.gateway.features import auth_subscriptions as auth_features
from machina.security import opencode_go_auth


class _Gateway:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    def event_payload(
        self, event_type: str, session_id: str, turn_id: str, **fields: Any
    ) -> dict[str, Any]:
        return {"type": event_type, "sessionId": session_id, "turnId": turn_id, **fields}

    def _write(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


@pytest.mark.asyncio
async def test_opencode_login_emits_device_metadata_and_one_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gw = _Gateway()
    monkeypatch.setattr(opencode_go_auth, "credential_generation", lambda: 7)
    monkeypatch.setattr(
        opencode_go_auth,
        "start_device_login",
        lambda: {
            "device_code": "private-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://opencode.ai/verify",
            "verification_uri_complete": "https://opencode.ai/verify?code=ABCD-EFGH",
            "expires_in": 60,
            "interval": 1,
        },
    )
    monkeypatch.setattr(
        opencode_go_auth,
        "complete_device_login",
        lambda device, **kwargs: {
            "account_id": "account-1",
            "expires_at": 1_900_000_000,
            "org_id": "org-1",
        },
    )

    result = await auth_features.auth_opencode_login(gw, {"no_browser": True})
    assert result == {
        "url": "https://opencode.ai/verify?code=ABCD-EFGH",
        "userCode": "ABCD-EFGH",
        "verificationUri": "https://opencode.ai/verify",
        "expiresIn": 60.0,
    }
    task = auth_features._PENDING["opencode"]
    await task
    assert len(gw.frames) == 1
    event = gw.frames[0]["params"]
    assert event["type"] == "auth.opencode.completed"
    assert event["account_id"] == "account-1"
    assert event["expiry"] == 1_900_000_000.0
    assert event["org_id"] == "org-1"
    assert "private-device-code" not in json.dumps(gw.frames)
    assert "opencode" not in auth_features._PENDING


@pytest.mark.asyncio
async def test_opencode_status_and_logout_cancel_pending_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gw = _Gateway()
    monkeypatch.setattr(
        opencode_go_auth,
        "status",
        lambda: {
            "signed_in": True,
            "expired": False,
            "expires_at": 1_900_000_000,
            "email": "user@example.test",
            "org_id": "org-1",
            "org_name": "Primary",
            "account_id": "account-1",
            "storage_path": "/private/auth.json",
        },
    )
    status = await auth_features.auth_opencode_status(gw, {})
    assert status == {
        "status": {
            "signedIn": True,
            "expired": False,
            "expiresAt": 1_900_000_000,
            "email": "user@example.test",
            "orgId": "org-1",
            "orgName": "Primary",
            "accountId": "account-1",
            "storagePath": "/private/auth.json",
        }
    }

    started = threading.Event()
    release = threading.Event()

    def wait_for_approval(device: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        started.set()
        release.wait(2)
        raise opencode_go_auth.OpenCodeGoAuthError("cancelled", code="opencode_go_auth_cancelled")

    monkeypatch.setattr(opencode_go_auth, "credential_generation", lambda: 7)
    monkeypatch.setattr(
        opencode_go_auth,
        "start_device_login",
        lambda: {
            "device_code": "private-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://opencode.ai/verify",
            "expires_in": 60,
            "interval": 1,
        },
    )
    monkeypatch.setattr(opencode_go_auth, "complete_device_login", wait_for_approval)
    monkeypatch.setattr(opencode_go_auth, "logout", lambda: True)

    await auth_features.auth_opencode_login(gw, {"no_browser": True})
    await asyncio.to_thread(started.wait, 1)
    result = await auth_features.auth_opencode_logout(gw, {})
    release.set()
    await asyncio.sleep(0)
    assert result["removed"] is True
    assert result["status"]["email"] == "user@example.test"
    events = [frame["params"]["type"] for frame in gw.frames]
    assert events.count("auth.opencode.failed") == 1


def test_opencode_gateway_methods_are_registered() -> None:
    assert METHODS["auth.opencode.status"] is auth_features.auth_opencode_status
    assert METHODS["auth.opencode.login"] is auth_features.auth_opencode_login
    assert METHODS["auth.opencode.logout"] is auth_features.auth_opencode_logout
