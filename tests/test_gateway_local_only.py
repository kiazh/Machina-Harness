"""Local-only gateway capabilities are refused on the shared HTTP surface."""

from __future__ import annotations

import pytest

from machina.gateway.errors import UNAUTHORIZED, RpcError
from machina.gateway.features._common import _require_local_transport
from machina.gateway.features.host import host_credentials
from machina.gateway.features.interop import interop_run


class _Gateway:
    def __init__(self, local_only: bool):
        self._local_only = local_only


def test_stdio_transport_is_allowed():
    _require_local_transport(_Gateway(True), "host.credentials")


@pytest.mark.asyncio
async def test_host_credentials_is_refused_over_http():
    with pytest.raises(RpcError) as error:
        await host_credentials(_Gateway(False), {})

    assert error.value.code == UNAUTHORIZED
    assert "local terminal channel" in error.value.message


@pytest.mark.asyncio
async def test_interop_run_is_refused_over_http():
    with pytest.raises(RpcError) as error:
        await interop_run(_Gateway(False), {"platform": "opencode", "prompt": "hello"})

    assert error.value.code == UNAUTHORIZED
