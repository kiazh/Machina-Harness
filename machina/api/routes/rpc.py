"""Generic JSON-RPC dispatch endpoint.

The handler reads the shared embedded ``Gateway`` from ``request.app.state``
and calls its typed ``Gateway.call`` seam. The route owns the HTTP envelope
(auth, response shape, error mapping) only; it never parses or synthesizes
JSON-RPC frames.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from machina.api.auth import require_rpc_auth
from machina.api.routes.models import RpcRequest
from machina.api.routes.shared import _redact_value, _rpc_http_status
from machina.gateway.errors import RpcError
from machina.gateway.local_transport import allow_notch_local
from machina.resident.token import is_notch_token

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/rpc", dependencies=[Depends(require_rpc_auth)])
async def rpc_dispatch(req: RpcRequest, request: Request) -> dict[str, Any]:
    """Generic JSON-RPC dispatch to any gateway feature method.

    NOTE: the gateway is shared across HTTP requests. ``session.create``
    and ``config.set`` mutate that shared state (model/provider writes are
    serialized by ``Gateway._config_lock``); prefer the REST session
    endpoints when per-request isolation matters.
    """
    if req.method in {"prompt.submit", "prompt.cancel", "shutdown"}:
        raise HTTPException(400, "use the session prompt stream for turns")
    if req.method == "initialize":
        # The embedded gateway is already initialized by the HTTP lifespan.
        # Re-running the stdio handshake would mutate shared model/provider/
        # protocol state and the process-global config for every caller.
        raise HTTPException(400, "the HTTP gateway is initialized at startup")
    gw = getattr(request.app.state, "gateway", None)
    if gw is None:
        raise HTTPException(503, "RPC gateway not initialized")
    try:
        notch = is_notch_token(request.headers.get("x-machina-notch"))
        if notch and req.method.startswith("resident."):
            with allow_notch_local():
                result = await gw.call(req.method, req.params)
        else:
            result = await gw.call(req.method, req.params)
        # AH-002: REST context previews are redacted, so the generic RPC
        # bridge must apply the same policy — otherwise context.get
        # previews and session.export markdown leak raw transcript
        # secrets through /rpc.
        return {"result": _redact_value(result)}
    except RpcError as e:
        status = _rpc_http_status(e.code)
        raise HTTPException(status, e.message if status != 500 else "RPC dispatch failed") from None
    except Exception:
        logger.exception("RPC dispatch failed for method %s", req.method)
        raise HTTPException(500, "internal error") from None
