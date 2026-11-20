"""Optional OIDC resource-server authentication.

The local API key remains the zero-config path. When
``MACHINA_OIDC_ISSUER`` and ``MACHINA_OIDC_AUDIENCE`` are configured, a
Bearer JWT is verified against the issuer's JWKS and used as the human
principal for approvals. This module is import-safe when the optional
``oidc`` extra is not installed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

import httpx

OIDC_ISSUER_ENV = "MACHINA_OIDC_ISSUER"
OIDC_AUDIENCE_ENV = "MACHINA_OIDC_AUDIENCE"
OIDC_JWKS_ENV = "MACHINA_OIDC_JWKS_URL"
OIDC_ALGORITHMS_ENV = "MACHINA_OIDC_ALGORITHMS"
OIDC_SUBJECT_ENV = "MACHINA_OIDC_SUBJECT_CLAIM"


class OIDCError(ValueError):
    """A bearer token failed OIDC validation."""


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    audience: str
    jwks_url: str = ""
    algorithms: tuple[str, ...] = ("RS256",)
    subject_claim: str = "sub"


def settings() -> OIDCSettings | None:
    issuer = os.environ.get(OIDC_ISSUER_ENV, "").strip().rstrip("/")
    audience = os.environ.get(OIDC_AUDIENCE_ENV, "").strip()
    if not issuer or not audience:
        return None
    algorithms = tuple(
        part.strip()
        for part in os.environ.get(OIDC_ALGORITHMS_ENV, "RS256").split(",")
        if part.strip()
    )
    return OIDCSettings(
        issuer=issuer,
        audience=audience,
        jwks_url=os.environ.get(OIDC_JWKS_ENV, "").strip(),
        algorithms=algorithms or ("RS256",),
        subject_claim=os.environ.get(OIDC_SUBJECT_ENV, "sub").strip() or "sub",
    )


def configured() -> bool:
    return settings() is not None


_ASYMMETRIC_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)


def _safe_endpoint(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}


@lru_cache(maxsize=8)
def _discover_jwks_url(issuer: str, explicit: str) -> str:
    if explicit:
        if not _safe_endpoint(explicit):
            raise OIDCError("OIDC JWKS URL must use HTTPS or loopback HTTP")
        return explicit
    url = f"{issuer}/.well-known/openid-configuration"
    response = httpx.get(url, timeout=10.0, follow_redirects=False, trust_env=False)
    response.raise_for_status()
    payload = response.json()
    jwks_url = payload.get("jwks_uri")
    if not isinstance(jwks_url, str) or not _safe_endpoint(jwks_url):
        raise OIDCError("issuer did not advertise an HTTPS JWKS URI")
    return jwks_url


@lru_cache(maxsize=8)
def _jwk_client(jwks_url: str) -> Any:
    try:
        from jwt import PyJWKClient
    except ImportError as error:  # pragma: no cover - optional extra
        raise OIDCError("OIDC requires the optional 'oidc' dependency") from error
    return PyJWKClient(jwks_url)


def verify_token(token: str) -> str:
    """Verify *token* and return a human-facing OIDC principal string."""
    config = settings()
    if config is None:
        raise OIDCError("OIDC is not configured")
    try:
        import jwt
    except ImportError as error:  # pragma: no cover - optional extra
        raise OIDCError("OIDC requires the optional 'oidc' dependency") from error
    algorithms = tuple(part for part in config.algorithms if part in _ASYMMETRIC_ALGORITHMS)
    if not algorithms:
        raise OIDCError("OIDC algorithms must be asymmetric signing algorithms")
    try:
        jwks_url = _discover_jwks_url(config.issuer, config.jwks_url)
        signing_key = _jwk_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(algorithms),
            audience=config.audience,
            issuer=config.issuer,
            options={"require": ["exp", "iss", "aud"]},
        )
    except OIDCError:
        raise
    except Exception as error:
        raise OIDCError("bearer token rejected") from error
    subject = claims.get(config.subject_claim)
    if not isinstance(subject, str) or not subject.strip():
        raise OIDCError(f"token has no {config.subject_claim} claim")
    return f"oidc:{subject.strip()[:200]}"


def reset_cache() -> None:
    """Test seam: clear discovery/JWKS caches after env changes."""
    _discover_jwks_url.cache_clear()
    _jwk_client.cache_clear()
