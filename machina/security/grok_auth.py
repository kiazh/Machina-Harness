"""xAI Grok subscription credentials — OAuth device flow.

Grok CLI OAuth tokens are accepted by xAI's CLI chat proxy
(``https://cli-chat-proxy.grok.com/v1``), not by the API-key endpoint.
Tokens live in ``~/.machina/auth/grok.json`` (0600, atomic replace).
The flow uses xAI's public Grok CLI OAuth client and the RFC 8628 device
grant, so no browser callback or client secret is required.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from machina.core.config import config
from machina.core.exceptions import ProviderError
from machina.security import store_fence
from machina.security.secrets import remember_secret_value

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
ISSUER = "https://auth.x.ai"
DEVICE_CODE_URL = f"{ISSUER}/oauth2/device/code"
TOKEN_URL = f"{ISSUER}/oauth2/token"
SCOPES = "openid profile email offline_access grok-cli:access api:access"
PROXY_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
CLIENT_IDENTIFIER = "grok-shell"
CLIENT_VERSION = "1.0.62"
USER_AGENT = f"{CLIENT_IDENTIFIER}/{CLIENT_VERSION} (machina)"
TOKEN_AUTH_HEADER = "xai-grok-cli"
DEFAULT_MODEL = "grok-4.7"
MODELS = (
    "grok-4.7",
    "grok-4.7-build-fast",
    "grok-4.6",
    "grok-4.5",
    "grok-4.3",
    "grok-build-0.1",
)
AUTH_FILE_ENV = "MACHINA_GROK_AUTH_FILE"
LOGIN_HINT = "run `machina auth grok login`"
_REFRESH_SKEW_SECONDS = 120
_DEFAULT_TIMEOUT = 20.0


class GrokAuthError(ProviderError):
    """Typed Grok auth failure. Carries a stable ``code``, never tokens."""

    def __init__(self, message: str, *, code: str = "grok_auth_error") -> None:
        super().__init__(message)
        self.code = code


def auth_path() -> Path:
    override = os.environ.get(AUTH_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    home = ""
    with suppress(Exception):
        home = str(config.get("machina_home") or "")
    root = Path(home).expanduser() if home else Path.home() / ".machina"
    return root / "auth" / "grok.json"


def credential_generation(path: str | Path | None = None) -> int:
    return store_fence.credential_generation(Path(path) if path is not None else auth_path())


def _normalize_store(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    access = raw.get("access_token") or raw.get("access")
    refresh = raw.get("refresh_token") or raw.get("refresh")
    if not isinstance(access, str) or not access.strip():
        return None
    expires = raw.get("expires_at")
    try:
        expires_at = float(expires) if expires is not None else 0.0
    except (TypeError, ValueError):
        expires_at = 0.0
    account = raw.get("account_id")
    return {
        "access_token": access.strip(),
        "refresh_token": refresh.strip() if isinstance(refresh, str) and refresh.strip() else None,
        "expires_at": expires_at,
        "account_id": account.strip() if isinstance(account, str) and account.strip() else None,
        "last_refresh": str(raw.get("last_refresh") or ""),
    }


def _save(tokens: dict[str, Any], path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else auth_path()
    normalized = _normalize_store(tokens)
    if normalized is None:
        raise GrokAuthError("invalid Grok token response", code="grok_token_invalid")
    remember_secret_value(normalized["access_token"])
    if normalized["refresh_token"]:
        remember_secret_value(normalized["refresh_token"])
    normalized["last_refresh"] = datetime.now(UTC).isoformat()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        store_fence.chmod_temp_file(fd, temp_name, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        os.chmod(target, 0o600)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp_name)
        raise
    return target


def load_tokens(path: str | Path | None = None) -> dict[str, Any] | None:
    target = Path(path) if path is not None else auth_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    normalized = _normalize_store(raw)
    if normalized:
        remember_secret_value(normalized["access_token"])
        if normalized["refresh_token"]:
            remember_secret_value(normalized["refresh_token"])
    return normalized


def status(path: str | Path | None = None) -> dict[str, Any]:
    tokens = load_tokens(path)
    if not tokens:
        return {"signed_in": False, "source": None, "expired": False}
    expires = float(tokens.get("expires_at") or 0)
    return {
        "signed_in": True,
        "source": "machina",
        "expired": bool(expires and expires <= time.time()),
        "expires_at": expires or None,
        "account_id": tokens.get("account_id"),
        "last_refresh": tokens.get("last_refresh") or None,
    }


def logout(path: str | Path | None = None) -> bool:
    target = Path(path) if path is not None else auth_path()
    with suppress(OSError):
        target.unlink()
        return True
    return False


def _account_from_access(access: str) -> str | None:
    try:
        payload = access.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    sub = data.get("sub") if isinstance(data, dict) else None
    return sub.strip() if isinstance(sub, str) and sub.strip() else None


def _token_request(data: dict[str, str]) -> dict[str, Any]:
    try:
        response = httpx.post(
            TOKEN_URL,
            data=data,
            headers={"Accept": "application/json"},
            timeout=_DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise GrokAuthError(
            f"xAI token endpoint unavailable ({type(exc).__name__})",
            code="grok_token_unavailable",
        ) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code != 200:
        error = payload.get("error") if isinstance(payload, dict) else ""
        raise GrokAuthError(
            f"xAI token request failed (HTTP {response.status_code}"
            + (f": {error}" if error else ")"),
            code="grok_token_rejected",
        )
    return payload if isinstance(payload, dict) else {}


def start_device_login() -> dict[str, Any]:
    """Request a device code. Returns public device metadata."""
    try:
        response = httpx.post(
            DEVICE_CODE_URL,
            data={"client_id": CLIENT_ID, "scope": SCOPES},
            headers={"Accept": "application/json"},
            timeout=_DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise GrokAuthError(
            f"xAI device login unavailable ({type(exc).__name__})",
            code="grok_device_unavailable",
        ) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code != 200 or not isinstance(payload, dict):
        raise GrokAuthError(
            f"xAI device login failed (HTTP {response.status_code})",
            code="grok_device_rejected",
        )
    required = ("device_code", "user_code", "verification_uri", "expires_in", "interval")
    if any(not payload.get(key) for key in required):
        raise GrokAuthError("xAI device login response was incomplete", code="grok_device_invalid")
    return payload


def poll_device_login(
    device: dict[str, Any], *, timeout: float = 300.0, path: str | Path | None = None
) -> dict[str, Any]:
    """Poll the token endpoint until the user approves, then publish tokens."""
    device_code = str(device.get("device_code") or "")
    interval = max(1.0, float(device.get("interval") or 5))
    deadline = time.monotonic() + min(timeout, float(device.get("expires_in") or timeout))
    while time.monotonic() < deadline:
        try:
            payload = _token_request(
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                }
            )
        except GrokAuthError as exc:
            # RFC 8628 pending/slow_down are retryable token errors.
            if "authorization_pending" not in str(exc) and "slow_down" not in str(exc):
                raise
        else:
            access = str(payload.get("access_token") or "")
            if not access:
                raise GrokAuthError(
                    "xAI token response missing access_token", code="grok_token_invalid"
                )
            tokens = {
                "access_token": access,
                "refresh_token": payload.get("refresh_token"),
                "expires_at": time.time() + float(payload.get("expires_in") or 3600),
                "account_id": _account_from_access(access),
            }
            _save(tokens, path)
            return tokens
        time.sleep(interval)
    raise GrokAuthError("xAI device login timed out", code="grok_device_timeout")


def refresh(tokens: dict[str, Any], *, path: str | Path | None = None) -> dict[str, Any]:
    refresh_token = str(tokens.get("refresh_token") or "")
    if not refresh_token:
        raise GrokAuthError(
            f"Grok session expired; {LOGIN_HINT}", code="grok_reauthorization_required"
        )
    payload = _token_request(
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
        }
    )
    access = str(payload.get("access_token") or "")
    if not access:
        raise GrokAuthError("xAI refresh response missing access_token", code="grok_refresh_failed")
    refreshed = {
        "access_token": access,
        "refresh_token": payload.get("refresh_token") or refresh_token,
        "expires_at": time.time() + float(payload.get("expires_in") or 3600),
        "account_id": _account_from_access(access) or tokens.get("account_id"),
    }
    _save(refreshed, path)
    return refreshed


def resolve_tokens(path: str | Path | None = None) -> dict[str, Any]:
    tokens = load_tokens(path)
    if not tokens:
        raise GrokAuthError(
            f"no Grok subscription credential; {LOGIN_HINT}",
            code="grok_not_signed_in",
        )
    if float(tokens.get("expires_at") or 0) <= time.time() + _REFRESH_SKEW_SECONDS:
        try:
            tokens = refresh(tokens, path=path)
        except GrokAuthError:
            raise
        except Exception as exc:
            raise GrokAuthError(
                f"Grok token refresh failed ({type(exc).__name__}); {LOGIN_HINT}",
                code="grok_refresh_failed",
            ) from exc
    return tokens
