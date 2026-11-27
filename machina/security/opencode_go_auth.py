"""OpenCode Go subscription credentials — device flow and rotating refresh.

Credentials are stored in ``~/.machina/auth/opencode-go.json`` using atomic
0600 writes. Device login uses OpenCode's public ``opencode-cli`` client.
When an account has multiple organizations the CLI may ask which one to use;
non-interactive callers select the first organization deterministically.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from machina.core.config import config
from machina.core.exceptions import ProviderError
from machina.security import store_fence
from machina.security.secrets import remember_secret_value

CONSOLE_BASE_URL = "https://opencode.ai/console"
DEVICE_CODE_URL = f"{CONSOLE_BASE_URL}/auth/device/code"
TOKEN_URL = f"{CONSOLE_BASE_URL}/auth/device/token"
USER_URL = f"{CONSOLE_BASE_URL}/api/user"
ORGS_URL = f"{CONSOLE_BASE_URL}/api/orgs"
CLIENT_ID = "opencode-cli"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
AUTH_FILE_ENV = "MACHINA_OPENCODE_GO_AUTH_FILE"
LOGIN_HINT = "run `machina auth opencode login`"
REFRESH_SKEW_SECONDS = 120
DEFAULT_TIMEOUT = 20.0
DEFAULT_DEVICE_TIMEOUT = 300.0


class OpenCodeGoAuthError(ProviderError):
    """Typed OpenCode Go auth failure. Messages and codes never include tokens."""

    def __init__(self, message: str, *, code: str = "opencode_go_auth_error") -> None:
        super().__init__(message)
        self.code = code


_refresh_lock = threading.RLock()


def auth_path() -> Path:
    override = os.environ.get(AUTH_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    home = ""
    with suppress(Exception):
        home = str(config.get("machina_home") or "")
    root = Path(home).expanduser() if home else Path.home() / ".machina"
    return root / "auth" / "opencode-go.json"


def credential_generation(path: str | Path | None = None) -> int:
    return store_fence.credential_generation(Path(path) if path is not None else auth_path())


def _normalize(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    access = raw.get("access_token")
    refresh = raw.get("refresh_token")
    if not isinstance(access, str) or not access.strip():
        return None
    expires = raw.get("expires_at")
    try:
        expires_at = float(expires) if expires is not None else 0.0
    except (TypeError, ValueError):
        expires_at = 0.0
    return {
        "access_token": access.strip(),
        "refresh_token": refresh.strip() if isinstance(refresh, str) and refresh.strip() else None,
        "expires_at": expires_at,
        "email": _optional_text(raw.get("email")),
        "org_id": _optional_text(raw.get("org_id")),
        "org_name": _optional_text(raw.get("org_name")),
        "account_id": _optional_text(raw.get("account_id")),
        "last_refresh": _optional_text(raw.get("last_refresh")),
    }


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _remember(tokens: dict[str, Any] | None) -> None:
    if tokens:
        for name in ("access_token", "refresh_token"):
            value = tokens.get(name)
            if isinstance(value, str) and value.strip():
                remember_secret_value(value.strip())


def load_tokens(path: str | Path | None = None) -> dict[str, Any] | None:
    target = Path(path) if path is not None else auth_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    tokens = _normalize(raw)
    _remember(tokens)
    return tokens


def save_tokens(tokens: dict[str, Any], path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else auth_path()
    normalized = _normalize(tokens)
    if normalized is None:
        raise OpenCodeGoAuthError(
            "OpenCode Go returned an invalid token response", code="opencode_go_token_invalid"
        )
    _remember(normalized)
    normalized["last_refresh"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
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


def status(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else auth_path()
    tokens = load_tokens(target)
    result: dict[str, Any] = {
        "signed_in": False,
        "expired": False,
        "expires_at": None,
        "email": None,
        "org_id": None,
        "org_name": None,
        "account_id": None,
        "storage_path": str(target),
    }
    if tokens is None:
        return result
    expires = float(tokens.get("expires_at") or 0)
    result.update(
        signed_in=True,
        expired=bool(expires and expires <= time.time()),
        expires_at=expires or None,
        email=tokens.get("email"),
        org_id=tokens.get("org_id"),
        org_name=tokens.get("org_name"),
        account_id=tokens.get("account_id"),
    )
    return result


def logout(path: str | Path | None = None) -> bool:
    target = Path(path) if path is not None else auth_path()
    return store_fence.fenced_logout(target)


def _client_or_new(client: httpx.Client | None, timeout: float) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    return (
        httpx.Client(timeout=httpx.Timeout(float(timeout), connect=min(10.0, float(timeout)))),
        True,
    )


def _json_response(response: httpx.Response, operation: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenCodeGoAuthError(
            f"OpenCode {operation} returned invalid JSON (HTTP {response.status_code})",
            code=f"opencode_go_{operation}_invalid_json",
        ) from exc
    if not isinstance(payload, dict):
        raise OpenCodeGoAuthError(
            f"OpenCode {operation} returned an invalid response",
            code=f"opencode_go_{operation}_invalid_response",
        )
    return payload


def start_device_login(
    *, client: httpx.Client | None = None, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    http, owns_client = _client_or_new(client, timeout)
    try:
        response = http.post(
            DEVICE_CODE_URL,
            json={"client_id": CLIENT_ID, "supports_org_scope": True},
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise OpenCodeGoAuthError(
            f"OpenCode device authorization is unavailable ({type(exc).__name__}); retry when online",
            code="opencode_go_device_unavailable",
        ) from exc
    finally:
        if owns_client:
            http.close()
    if response.status_code != 200:
        raise OpenCodeGoAuthError(
            f"OpenCode device authorization failed (HTTP {response.status_code})",
            code="opencode_go_device_failed",
        )
    payload = _json_response(response, "device")
    required = ("device_code", "user_code", "verification_uri_complete", "expires_in", "interval")
    if any(not payload.get(key) for key in required):
        raise OpenCodeGoAuthError(
            "OpenCode device authorization response was incomplete",
            code="opencode_go_device_invalid",
        )
    return payload


def _token_post(
    data: dict[str, str], *, client: httpx.Client, timeout: float
) -> tuple[httpx.Response, dict[str, Any]]:
    try:
        response = client.post(
            TOKEN_URL,
            json=data,
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise OpenCodeGoAuthError(
            f"OpenCode token endpoint is unavailable ({type(exc).__name__})",
            code="opencode_go_token_unavailable",
        ) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response, payload if isinstance(payload, dict) else {}


def _fetch_identity(
    access_token: str, *, client: httpx.Client, timeout: float
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    try:
        user_response = client.get(USER_URL, headers=headers, timeout=timeout)
        org_response = client.get(ORGS_URL, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise OpenCodeGoAuthError(
            f"OpenCode account lookup is unavailable ({type(exc).__name__})",
            code="opencode_go_account_unavailable",
        ) from exc
    if user_response.status_code != 200 or org_response.status_code != 200:
        raise OpenCodeGoAuthError(
            "OpenCode account lookup failed; complete sign-in again",
            code="opencode_go_account_failed",
        )
    try:
        user = user_response.json()
        orgs_payload = org_response.json()
    except ValueError as exc:
        raise OpenCodeGoAuthError(
            "OpenCode account lookup returned invalid JSON",
            code="opencode_go_account_invalid_json",
        ) from exc
    if not isinstance(user, dict):
        user = {}
    orgs = (
        orgs_payload
        if isinstance(orgs_payload, list)
        else orgs_payload.get("orgs", [])
        if isinstance(orgs_payload, dict)
        else []
    )
    if not isinstance(orgs, list):
        orgs = []
    return user, [org for org in orgs if isinstance(org, dict)]


def _org_id(org: dict[str, Any]) -> str | None:
    return _optional_text(org.get("org_id") or org.get("id"))


def _publish_login(
    tokens: dict[str, Any], *, generation: int, path: str | Path | None
) -> dict[str, Any]:
    target = Path(path) if path is not None else auth_path()
    with store_fence.store_lock(target):
        if store_fence.credential_generation(target) != generation:
            raise OpenCodeGoAuthError(
                "OpenCode sign-in was cancelled by a concurrent logout; start login again",
                code="opencode_go_auth_cancelled",
            )
        save_tokens(tokens, target)
        published = load_tokens(target)
        if store_fence.credential_generation(target) != generation:
            if published and published.get("access_token") == tokens.get("access_token"):
                target.unlink(missing_ok=True)
            raise OpenCodeGoAuthError(
                "OpenCode sign-in was cancelled by a concurrent logout; start login again",
                code="opencode_go_auth_cancelled",
            )
        assert published is not None
        return published


def login(
    *,
    path: str | Path | None = None,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_DEVICE_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    output: Callable[[str], None] = print,
    select_org: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None,
    open_browser: bool = False,
) -> dict[str, Any]:
    """Complete device login; multiple organizations default to the first.

    ``select_org`` is an optional interactive chooser. Without one, the first
    API-listed organization is used, which keeps headless login deterministic.
    """
    target = Path(path) if path is not None else auth_path()
    generation = credential_generation(target)
    http, owns_client = _client_or_new(client, DEFAULT_TIMEOUT)
    try:
        device = start_device_login(client=http)
        output(f"Open this URL to sign in to OpenCode Go: {device['verification_uri_complete']}")
        output(f"Device code: {device['user_code']}")
        if open_browser:
            with suppress(Exception):
                webbrowser.open(str(device["verification_uri_complete"]))
        return complete_device_login(
            device,
            path=target,
            timeout=timeout,
            sleep=sleep,
            output=output,
            select_org=select_org,
            client=http,
            _generation=generation,
        )
    finally:
        if owns_client:
            http.close()


def complete_device_login(
    device: dict[str, Any],
    *,
    path: str | Path | None = None,
    timeout: float = DEFAULT_DEVICE_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    output: Callable[[str], None] = print,
    select_org: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None,
    client: httpx.Client | None = None,
    _generation: int | None = None,
) -> dict[str, Any]:
    """Poll an existing device authorization and publish its account identity."""
    target = Path(path) if path is not None else auth_path()
    generation = credential_generation(target) if _generation is None else _generation
    http, owns_client = _client_or_new(client, DEFAULT_TIMEOUT)
    try:
        deadline = time.monotonic() + min(float(timeout), float(device["expires_in"]))
        interval = max(1.0, float(device["interval"]))
        token_payload: dict[str, Any] | None = None
        sleep(min(interval, max(0.0, deadline - time.monotonic())))
        while time.monotonic() < deadline:
            response, payload = _token_post(
                {
                    "grant_type": DEVICE_GRANT_TYPE,
                    "device_code": str(device["device_code"]),
                    "client_id": CLIENT_ID,
                },
                client=http,
                timeout=min(DEFAULT_TIMEOUT, max(1.0, deadline - time.monotonic())),
            )
            if response.status_code == 200 and payload.get("access_token"):
                token_payload = payload
                break
            error = str(payload.get("error") or "")
            if error == "authorization_pending":
                pass
            elif error == "slow_down":
                interval += 5.0
            elif error in {"expired_token", "access_denied"}:
                raise OpenCodeGoAuthError(
                    f"OpenCode device authorization {error.replace('_', ' ')}; start login again",
                    code=f"opencode_go_device_{error}",
                )
            else:
                raise OpenCodeGoAuthError(
                    f"OpenCode device token request failed (HTTP {response.status_code})",
                    code="opencode_go_device_token_failed",
                )
            sleep(min(interval, max(0.0, deadline - time.monotonic())))
        if token_payload is None:
            raise OpenCodeGoAuthError(
                "OpenCode device authorization timed out; start login again",
                code="opencode_go_device_timeout",
            )

        access = str(token_payload["access_token"])
        user, orgs = _fetch_identity(access, client=http, timeout=DEFAULT_TIMEOUT)
        token_org = _optional_text(token_payload.get("org_id"))
        matching = next((item for item in orgs if _org_id(item) == token_org), None)
        selected: dict[str, Any] | None = matching
        if selected is None and len(orgs) == 1:
            selected = orgs[0]
        elif selected is None and len(orgs) > 1:
            selected = select_org(orgs) if select_org else orgs[0]
            if _org_id(selected or {}) not in {_org_id(item) for item in orgs}:
                raise OpenCodeGoAuthError(
                    "The selected OpenCode organization was not in the account list",
                    code="opencode_go_org_invalid",
                )
            if select_org is None:
                output(
                    f"Multiple OpenCode organizations found; using the first: "
                    f"{_optional_text(selected.get('name')) or _org_id(selected) or 'organization'}"
                )
        org_id = _org_id(selected or {})
        org_name = _optional_text((selected or {}).get("name"))
        if not org_id:
            raise OpenCodeGoAuthError(
                "No OpenCode organization is available for Go inference; check account access and retry",
                code="opencode_go_org_missing",
            )
        tokens = {
            "access_token": access,
            "refresh_token": token_payload.get("refresh_token"),
            "expires_at": time.time() + float(token_payload.get("expires_in") or 3600),
            "email": _optional_text(user.get("email")),
            "org_id": org_id,
            "org_name": org_name,
            "account_id": _optional_text(user.get("id") or user.get("account_id")),
        }
        return _publish_login(tokens, generation=generation, path=target)
    finally:
        if owns_client:
            http.close()


async def login_async(**kwargs: Any) -> dict[str, Any]:
    """Async adapter for callers that need not block their event loop."""
    return await asyncio.to_thread(login, **kwargs)


def _refresh_locked(
    current: dict[str, Any], *, target: Path, client: httpx.Client, timeout: float
) -> dict[str, Any]:
    generation = credential_generation(target)
    snapshot = store_fence.store_fingerprint(target)
    refresh_token = str(current.get("refresh_token") or "")
    if not refresh_token:
        raise OpenCodeGoAuthError(
            f"OpenCode Go session expired and cannot be refreshed; {LOGIN_HINT}",
            code="opencode_go_refresh_token_missing",
        )
    response, payload = _token_post(
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID},
        client=client,
        timeout=timeout,
    )
    if response.status_code != 200 or not payload.get("access_token"):
        raise OpenCodeGoAuthError(
            f"OpenCode Go token refresh failed (HTTP {response.status_code}); {LOGIN_HINT}",
            code="opencode_go_refresh_failed",
        )
    updated = {
        **current,
        "access_token": str(payload["access_token"]),
        "refresh_token": str(payload.get("refresh_token") or refresh_token),
        "expires_at": time.time() + float(payload.get("expires_in") or 3600),
        "org_id": _optional_text(payload.get("org_id")) or current.get("org_id"),
    }
    if credential_generation(target) != generation:
        raise OpenCodeGoAuthError(
            "OpenCode Go credentials were signed out during refresh; sign in again",
            code="opencode_go_auth_cancelled",
        )
    if store_fence.store_fingerprint(target) != snapshot:
        latest = load_tokens(target)
        if latest and float(latest.get("expires_at") or 0) > time.time():
            return latest
        raise OpenCodeGoAuthError(
            "OpenCode Go credentials changed during refresh; retry or sign in again",
            code="opencode_go_refresh_raced",
        )
    save_tokens(updated, target)
    return load_tokens(target) or updated


def resolve_tokens(
    *,
    path: str | Path | None = None,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Read credentials, refreshing within 120 seconds of expiry, fenced."""
    target = Path(path) if path is not None else auth_path()
    with _refresh_lock:
        with store_fence.store_lock(target):
            current = load_tokens(target)
            if current is None:
                raise OpenCodeGoAuthError(
                    f"Not signed in to OpenCode Go; {LOGIN_HINT}",
                    code="opencode_go_auth_missing",
                )
            expires = float(current.get("expires_at") or 0)
            if not expires or expires > time.time() + REFRESH_SKEW_SECONDS:
                return current
            http, owns_client = _client_or_new(client, timeout)
            try:
                return _refresh_locked(current, target=target, client=http, timeout=timeout)
            finally:
                if owns_client:
                    http.close()


def access_token() -> str:
    return str(resolve_tokens()["access_token"])


def org_id() -> str:
    value = resolve_tokens().get("org_id")
    if not value:
        raise OpenCodeGoAuthError(
            f"OpenCode Go organization is missing; sign in again with `{LOGIN_HINT}`",
            code="opencode_go_org_missing",
        )
    return str(value)


__all__ = [
    "OpenCodeGoAuthError",
    "access_token",
    "auth_path",
    "credential_generation",
    "complete_device_login",
    "login",
    "login_async",
    "logout",
    "org_id",
    "resolve_tokens",
    "start_device_login",
    "status",
]
