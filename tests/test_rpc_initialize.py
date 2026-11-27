"""The HTTP RPC bridge cannot re-run the stdio initialize handshake."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from machina.api.routes.models import RpcRequest
from machina.api.routes.rpc import rpc_dispatch


class _Request:
    class app:
        class state:
            gateway = object()


@pytest.mark.asyncio
async def test_initialize_is_refused_over_rpc():
    with pytest.raises(HTTPException) as error:
        await rpc_dispatch(RpcRequest(method="initialize", params={}), _Request())

    assert error.value.status_code == 400
    assert "initialized at startup" in error.value.detail
