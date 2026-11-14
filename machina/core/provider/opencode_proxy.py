"""Loopback OpenAI-compatible facade over the OpenCode session bridge.

Machina uses :class:`OpenCodeSessionProvider` directly. This tiny HTTP facade
exists for local harnesses or clients that expect `POST /v1/chat/completions`.
It is loopback-only, text-only, and intentionally rejects tool-enabled calls
with an actionable error instead of pretending to support arbitrary schemas.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from machina.core.provider.opencode_session import (
    OpenCodeBridgeError,
    OpenCodeSessionProvider,
    OpenCodeToolsUnsupported,
)


def _openai_usage(usage: dict[str, Any]) -> dict[str, Any]:
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(usage.get("total_tokens") or prompt + completion),
        "prompt_tokens_details": {
            "cached_tokens": int(usage.get("cache_read_tokens") or 0),
        },
    }


def _error(message: str, *, code: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": code, "code": code}},
    )


def create_app() -> FastAPI:
    app = FastAPI(title="Machina OpenCode Session Proxy", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "transport": "opencode-session",
            "tool_calls": False,
            "text_only": True,
        }

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        # The entitlement-aware list comes from OpenCode's CLI and is cached by
        # the bridge. The provider validates the exact model again at session
        # creation time, so this endpoint never invents availability.
        from machina.core.provider.opencode_session import available_models

        ids = list(available_models()) or [
            "deepseek/deepseek-flash",
            "deepseek/deepseek-v4-pro",
        ]
        return {
            "object": "list",
            "data": [
                {
                    "id": model,
                    "object": "model",
                    "owned_by": model.split("/", 1)[0],
                    "capabilities": {"tools": False, "reasoning": True},
                }
                for model in ids
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        try:
            payload = await request.json()
        except ValueError:
            return _error("invalid JSON body", code="invalid_request_error")
        if not isinstance(payload, dict):
            return _error("body must be a JSON object", code="invalid_request_error")
        if payload.get("tools"):
            return _error(
                "OpenCode session proxy is text-only; use the native/proxy route "
                "or MCP for tool-enabled workers",
                code="tools_unsupported",
            )
        model = str(payload.get("model") or "deepseek/deepseek-flash")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return _error("messages must be a non-empty list", code="invalid_request_error")
        provider = OpenCodeSessionProvider(model=model)
        try:
            response = await provider.complete(
                messages,
                model=model,
                temperature=float(payload.get("temperature") or 0.7),
                max_tokens=int(payload.get("max_tokens") or 4096),
            )
        except OpenCodeToolsUnsupported as error:
            return _error(str(error), code="tools_unsupported")
        except OpenCodeBridgeError as error:
            return _error(str(error), code="opencode_bridge_error", status=502)
        finally:
            await provider.close()
        response_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        if bool(payload.get("stream")):
            async def stream():
                first = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": response.content},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(first)}\n\n"
                final = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": _openai_usage(response.usage),
                }
                yield f"data: {json.dumps(final)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(stream(), media_type="text/event-stream")
        return {
            "id": response_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": response.content},
                    "finish_reason": "stop",
                }
            ],
            "usage": _openai_usage(response.usage),
        }

    return app


app = create_app()

__all__ = ["app", "create_app"]
