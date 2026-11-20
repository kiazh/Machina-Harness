"""OIDC resource-server fallback alongside the local API key."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from machina.api import auth, oidc


def _run(coro):
    return asyncio.run(coro)


def _keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return private, private_pem, private.public_key()


def test_oidc_is_off_unless_issuer_and_audience_are_both_set(monkeypatch) -> None:
    monkeypatch.delenv(oidc.OIDC_ISSUER_ENV, raising=False)
    monkeypatch.delenv(oidc.OIDC_AUDIENCE_ENV, raising=False)
    assert oidc.settings() is None
    assert oidc.configured() is False
    monkeypatch.setenv(oidc.OIDC_ISSUER_ENV, "https://issuer.example")
    assert oidc.configured() is False


def test_principal_accepts_the_local_api_key(monkeypatch) -> None:
    monkeypatch.setattr(auth, "configured_key", lambda: "local-key")
    monkeypatch.delenv(oidc.OIDC_ISSUER_ENV, raising=False)
    monkeypatch.delenv(oidc.OIDC_AUDIENCE_ENV, raising=False)
    assert _run(auth.require_principal(None, "local-key")) == "http"
    with pytest.raises(HTTPException) as raised:
        _run(auth.require_principal("Bearer wrong", None))
    assert raised.value.status_code == 401


def test_unconfigured_auth_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(auth, "configured_key", lambda: None)
    monkeypatch.delenv(oidc.OIDC_ISSUER_ENV, raising=False)
    monkeypatch.delenv(oidc.OIDC_AUDIENCE_ENV, raising=False)
    with pytest.raises(HTTPException) as raised:
        _run(auth.require_principal(None, None))
    assert raised.value.status_code == 503


def test_principal_uses_oidc_when_the_local_key_is_absent(monkeypatch) -> None:
    monkeypatch.setattr(auth, "configured_key", lambda: None)
    monkeypatch.setenv(oidc.OIDC_ISSUER_ENV, "https://issuer.example")
    monkeypatch.setenv(oidc.OIDC_AUDIENCE_ENV, "machina-api")
    monkeypatch.setattr(oidc, "verify_token", lambda token: f"oidc:{token}")
    assert _run(auth.require_principal("Bearer alice", None)) == "oidc:alice"
    with pytest.raises(HTTPException) as raised:
        _run(auth.require_principal(None, None))
    assert raised.value.status_code == 401


def test_oidc_verifies_signature_audience_issuer_and_returns_subject(monkeypatch) -> None:
    jwt = pytest.importorskip("jwt")
    private, _, public = _keypair()
    monkeypatch.setenv(oidc.OIDC_ISSUER_ENV, "https://issuer.example")
    monkeypatch.setenv(oidc.OIDC_AUDIENCE_ENV, "machina-api")
    monkeypatch.setenv(oidc.OIDC_JWKS_ENV, "https://issuer.example/jwks.json")
    monkeypatch.setenv(oidc.OIDC_SUBJECT_ENV, "sub")
    oidc.reset_cache()
    monkeypatch.setattr(
        oidc,
        "_jwk_client",
        lambda _url: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public)
        ),
    )
    token = jwt.encode(
        {
            "sub": "user-123",
            "iss": "https://issuer.example",
            "aud": "machina-api",
            "exp": int(time.time()) + 60,
        },
        private,
        algorithm="RS256",
        headers={"kid": "test"},
    )
    assert oidc.verify_token(token) == "oidc:user-123"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"aud": "other-api"}, "bearer token rejected"),
        ({"exp": 1}, "bearer token rejected"),
        ({"sub": ""}, "token has no sub claim"),
    ],
)
def test_oidc_rejects_bad_claims(monkeypatch, overrides, message) -> None:
    jwt = pytest.importorskip("jwt")
    private, _, public = _keypair()
    monkeypatch.setenv(oidc.OIDC_ISSUER_ENV, "https://issuer.example")
    monkeypatch.setenv(oidc.OIDC_AUDIENCE_ENV, "machina-api")
    monkeypatch.setenv(oidc.OIDC_JWKS_ENV, "https://issuer.example/jwks.json")
    oidc.reset_cache()
    monkeypatch.setattr(
        oidc,
        "_jwk_client",
        lambda _url: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public)
        ),
    )
    claims = {
        "sub": "user-123",
        "iss": "https://issuer.example",
        "aud": "machina-api",
        "exp": int(time.time()) + 60,
        **overrides,
    }
    token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test"})
    with pytest.raises(oidc.OIDCError, match=message):
        oidc.verify_token(token)
