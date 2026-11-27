from __future__ import annotations

import os
from pathlib import Path

import pytest

from machina.api import auth
from machina.gateway.errors import RpcError
from machina.gateway.features._common import _require_local_transport
from machina.gateway.local_transport import allow_notch_local
from machina.resident import token


def test_notch_token_is_created_private_and_stable(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "notch.token"
    monkeypatch.setenv("MACHINA_NOTCH_TOKEN_FILE", str(target))
    monkeypatch.delenv("MACHINA_NOTCH_TOKEN", raising=False)

    first = token.ensure_notch_token()
    second = token.ensure_notch_token()

    assert first == second
    assert target.read_text(encoding="utf-8").strip() == first
    assert oct(os.stat(target).st_mode & 0o777) == "0o600"
    assert token.is_notch_token(first)
    assert token.is_notch_token(" " + first + "\n")
    assert not token.is_notch_token("wrong")


def test_notch_transport_allows_local_only_methods() -> None:
    class RemoteGateway:
        _local_only = False

    with pytest.raises(RpcError):
        _require_local_transport(RemoteGateway(), "resident.status")

    with allow_notch_local():
        _require_local_transport(RemoteGateway(), "resident.status")


@pytest.mark.asyncio
async def test_rpc_auth_accepts_the_notch_token(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "notch.token"
    monkeypatch.setenv("MACHINA_NOTCH_TOKEN_FILE", str(target))
    monkeypatch.delenv("MACHINA_NOTCH_TOKEN", raising=False)
    value = token.ensure_notch_token()

    await auth.require_rpc_auth(None, None, value)
