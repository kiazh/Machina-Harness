"""API-key authentication for the HTTP server.

The key is read from the ``MACHINA_API_KEY`` environment variable (via
``.env``). If it is unset the server refuses every authenticated request with
503 rather than silently running wide open.
"""

from __future__ import annotations

import asyncio
import hmac
import logging

from fastapi import Header, HTTPException, status

from machina.security.secrets import get_secret

logger = logging.getLogger(__name__)

API_KEY_ENV = "MACHINA_API_KEY"


def configured_key() -> str | None:
    try:
        return get_secret(API_KEY_ENV)
    except Exception as exc:
        # Fail closed, but leave a non-secret diagnostic for operators:
        # a backend outage must not look identical to a missing setting.
        logger.warning("API key backend unavailable (%s)", type(exc).__name__)
        return None


def _extract(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """FastAPI dependency: accept the request only with the right key."""
    # Vault/AWS backends use synchronous HTTP; keep that potentially
    # blocking work off the event loop on cache expiry.
    expected = await asyncio.to_thread(configured_key)
    # An empty configured value is not a key. Treating it as configured would
    # let an empty presented value compare equal and bypass auth.
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"API key not configured; set {API_KEY_ENV}",
        )
    presented = _extract(authorization, x_api_key)
    # Constant-time comparison so a wrong key cannot be guessed by timing.
    try:
        ok = (
            hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
            if presented is not None
            else False
        )
    except (TypeError, UnicodeError):
        ok = False
    if not ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
