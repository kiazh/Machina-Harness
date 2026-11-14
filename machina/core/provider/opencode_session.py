"""OpenCode local-session provider bridge.

OpenCode's free Zen models are only callable inside OpenCode. This provider
uses OpenCode's documented v2 HTTP API (session create/prompt/messages) as a
loopback bridge, so Machina can use those models for text-only planning,
review, and summarization without spoofing the client or bypassing limits.

The bridge deliberately advertises ``tool_calls=false``: OpenCode sessions
execute their own tools, and Machina's in-process worker loop must not assume
it can pass arbitrary tool schemas through a session prompt. Tool-using work
belongs on the native/proxy route or an explicit MCP worker.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx

from machina.core.models import LLMResponse, StreamEvent, ToolDefinition
from machina.core.provider.base import LLMProvider

SERVICE_ENV = "MACHINA_OPENCODE_SERVICE_FILE"
SERVER_ENV = "MACHINA_OPENCODE_SERVER_URL"
DEFAULT_BASE_URL = "http://127.0.0.1:45278"
POLL_INTERVAL = 0.5
PROMPT_TIMEOUT = 300.0
_MODELS_CACHE: tuple[float, tuple[str, ...]] | None = None
_MODELS_TTL = 60.0


class OpenCodeBridgeError(RuntimeError):
    """The local OpenCode session could not complete a text turn."""


class OpenCodeToolsUnsupported(OpenCodeBridgeError):
    """A tool-enabled request reached the text-only session bridge."""


def service_file() -> Path:
    override = os.environ.get(SERVICE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "opencode" / "service.json"


def service_password(path: str | Path | None = None) -> str:
    target = Path(path) if path is not None else service_file()
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise OpenCodeBridgeError(
            "OpenCode service credentials are unavailable; run `opencode` once "
            "to start its background service"
        ) from error
    password = str(payload.get("password") or "").strip()
    if not password:
        raise OpenCodeBridgeError("OpenCode service password is empty")
    return password


def _status_url() -> str | None:
    binary = shutil.which("opencode")
    if not binary:
        return None
    try:
        completed = subprocess.run(
            [binary, "service", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for token in (completed.stdout or "").split():
        if token.startswith("http://") or token.startswith("https://"):
            return token.strip().rstrip("/")
    return None


def service_url() -> str:
    override = os.environ.get(SERVER_ENV, "").strip().rstrip("/")
    if override:
        return override
    discovered = _status_url()
    return discovered or DEFAULT_BASE_URL


def available_models(*, force: bool = False) -> tuple[str, ...]:
    """Return OpenCode's *available* model list without network calls.

    The public Zen catalog is much larger than what this account can actually
    use; ``opencode models`` is the entitlement-aware list. Cached briefly so a
    routing inventory build cannot spawn the CLI on every turn.
    """
    global _MODELS_CACHE
    now = time.monotonic()
    if not force and _MODELS_CACHE is not None and now - _MODELS_CACHE[0] < _MODELS_TTL:
        return _MODELS_CACHE[1]
    binary = shutil.which("opencode")
    if not binary:
        return ()
    try:
        completed = subprocess.run(
            [binary, "models"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    models: list[str] = []
    for line in (completed.stdout or "").splitlines():
        token = line.strip().split()[0] if line.strip() else ""
        if "/" in token and len(token) <= 200:
            models.append(token)
    result = tuple(dict.fromkeys(models))
    _MODELS_CACHE = (now, result)
    return result


def _auth_header(password: str) -> dict[str, str]:
    token = base64.b64encode(f"opencode:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


def _model_ref(model: str) -> dict[str, str]:
    raw = str(model or "").strip()
    variant = ""
    if "#" in raw:
        raw, variant = raw.split("#", 1)
    if "/" in raw:
        provider, model_id = raw.split("/", 1)
    else:
        provider, model_id = "opencode", raw
    ref = {"providerID": provider or "opencode", "id": model_id or "big-pickle"}
    if variant:
        ref["variant"] = variant
    return ref


def _prompt_from_messages(messages: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user").strip().lower()
        content = message.get("content")
        if isinstance(content, list):
            text = "\n".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") in {"text", "input_text"}
            )
        else:
            text = str(content or "")
        if not text.strip():
            continue
        if role == "system":
            blocks.append(f"System instructions:\n{text.strip()}")
        elif role == "assistant":
            blocks.append(f"Assistant:\n{text.strip()}")
        else:
            blocks.append(f"User:\n{text.strip()}")
    return "\n\n".join(blocks)


def _assistant_text(message: dict[str, Any]) -> str:
    chunks: list[str] = []
    for part in message.get("content") or ():
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            chunks.append(str(part.get("text") or ""))
    return "".join(chunks).strip()


def _usage(message: dict[str, Any]) -> dict[str, int]:
    tokens = message.get("tokens") if isinstance(message.get("tokens"), dict) else {}
    uncached_input = int(tokens.get("input") or 0)
    output_tokens = int(tokens.get("output") or 0)
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    cache_read = int(cache.get("read") or 0)
    cache_write = int(cache.get("write") or 0)
    # OpenCode reports uncached input plus cache categories separately. The
    # Machina ledger's logical prompt total is their sum; preserve the split
    # so cost accounting can price cache reads correctly.
    logical_input = uncached_input + cache_read + cache_write
    usage = {
        "prompt_tokens": logical_input,
        "completion_tokens": output_tokens,
        "total_tokens": logical_input + output_tokens,
    }
    if cache_read or cache_write:
        usage["cache_reported"] = True
        usage["uncached_input_tokens"] = uncached_input
        usage["cache_read_tokens"] = cache_read
        usage["cache_write_tokens"] = cache_write
    return usage


class OpenCodeSessionProvider(LLMProvider):
    """Text-only OpenAI-provider facade over an OpenCode local session."""

    provider_name = "opencode_session"
    supports_tools = False
    text_only = True

    def __init__(
        self,
        *,
        model: str = "deepseek/deepseek-flash",
        base_url: str | None = None,
        password: str | None = None,
        timeout: float = PROMPT_TIMEOUT,
    ) -> None:
        self.model = model or "deepseek/deepseek-flash"
        self.base_url = (base_url or service_url()).rstrip("/")
        self.password = password or service_password()
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=_auth_header(self.password),
            timeout=httpx.Timeout(30.0, connect=5.0),
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[ToolDefinition] | None = None,
        reasoning_effort: str | None = None,
        session_id: Any = None,
        agent_id: str | None = None,
        usage_reserved: bool = False,
    ) -> LLMResponse:
        # The bridge cannot round-trip arbitrary Machina tool schemas, but the
        # normal ReAct loop always advertises its catalog. Ignoring the schemas
        # lets the route serve chat/planning/verification turns; it must never
        # claim Machina tool execution. The route inventory already marks this
        # provider tools=False so it cannot become a worker.
        ignored_tools = len(tools or [])
        prompt = _prompt_from_messages(messages)
        if not prompt.strip():
            raise OpenCodeBridgeError("empty prompt")
        selected = str(model or self.model)
        session_id = await asyncio.to_thread(self._create_session, selected)
        try:
            await asyncio.to_thread(self._send, session_id, prompt)
            message = await self._wait_for_assistant(session_id)
        finally:
            try:
                self._client.delete(f"/api/session/{session_id}")
            except Exception:
                pass
        content = _assistant_text(message)
        if not content:
            error = message.get("error")
            raise OpenCodeBridgeError(
                f"OpenCode returned no visible text (finish={message.get('finish')}, "
                f"error={error!r})"
            )
        return LLMResponse(
            content=content,
            model=selected,
            usage=_usage(message),
            raw={
                "opencode_session": session_id,
                "finish": message.get("finish"),
                "tools_ignored": ignored_tools,
            },
        )

    async def stream_complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[ToolDefinition] | None = None,
        reasoning_effort: str | None = None,
        session_id: Any = None,
        agent_id: str | None = None,
        usage_reserved: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        response = await self.complete(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            reasoning_effort=reasoning_effort,
            session_id=session_id,
            agent_id=agent_id,
            usage_reserved=usage_reserved,
        )
        if response.content:
            yield StreamEvent(type="text", content=response.content)
        yield StreamEvent(type="done", response=response)

    def _create_session(self, model: str) -> str:
        response = self._client.post(
            "/api/session",
            json={"title": "Machina bridge", "model": _model_ref(model)},
        )
        if response.status_code != 200:
            raise OpenCodeBridgeError(
                f"OpenCode session create failed (HTTP {response.status_code})"
            )
        payload = response.json()
        session_id = str((payload.get("data") or {}).get("id") or "")
        if not session_id:
            raise OpenCodeBridgeError("OpenCode session create returned no id")
        return session_id

    def _send(self, session_id: str, prompt: str) -> None:
        response = self._client.post(
            f"/api/session/{session_id}/prompt",
            json={"text": prompt, "delivery": "queue"},
        )
        if response.status_code != 200:
            raise OpenCodeBridgeError(
                f"OpenCode prompt failed (HTTP {response.status_code})"
            )

    async def _wait_for_assistant(self, session_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            response = await asyncio.to_thread(
                self._client.get,
                f"/api/session/{session_id}/message",
                params={"limit": "100", "order": "asc"},
            )
            if response.status_code != 200:
                raise OpenCodeBridgeError(
                    f"OpenCode message read failed (HTTP {response.status_code})"
                )
            data = response.json().get("data") or []
            assistants = [
                message for message in data if isinstance(message, dict) and message.get("type") == "assistant"
            ]
            if assistants:
                latest = assistants[-1]
                if latest.get("time", {}).get("completed") or latest.get("finish"):
                    return latest
            idle = [
                message for message in data if isinstance(message, dict) and message.get("type") == "idle"
            ]
            if idle:
                if assistants:
                    return assistants[-1]
                raise OpenCodeBridgeError("OpenCode session went idle without an assistant message")
            await asyncio.sleep(POLL_INTERVAL)
        raise OpenCodeBridgeError("OpenCode session timed out waiting for a response")

    async def close(self) -> None:
        self._client.close()


__all__ = [
    "OpenCodeBridgeError",
    "OpenCodeSessionProvider",
    "OpenCodeToolsUnsupported",
    "available_models",
    "service_password",
    "service_url",
]
