"""Grok subscription provider over xAI's CLI chat proxy.

xAI OAuth tokens issued by the Grok device flow are accepted by the CLI
Responses proxy, not by the API-key endpoint. The wire shape follows the Grok
CLI: flat text turns, a leading system message, detailed reasoning summaries,
and the ``x-grok-*`` client headers.
"""

from __future__ import annotations

from typing import Any

from machina.core.config import config
from machina.core.exceptions import ValidationError
from machina.core.provider.chatgpt import ChatGPTProvider, _tools_payload
from machina.security import grok_auth


class GrokProvider(ChatGPTProvider):
    """Grok subscription via OAuth device login (no API key)."""

    PROVIDER_NAME = "grok"
    AUTH = grok_auth
    BASE_URL = grok_auth.PROXY_BASE_URL
    RESPONSES_PATH = "/responses"
    DEFAULT_MODEL = grok_auth.DEFAULT_MODEL
    DEFAULT_INSTRUCTIONS = "You are a helpful assistant."

    @staticmethod
    def _normalize_model(model: str | None) -> str:
        name = str(model or "").strip()
        return name[len("grok/") :] if name.lower().startswith("grok/") else name

    def _validate_model(self, model: str) -> None:
        if model not in grok_auth.MODELS and not model.startswith("grok-"):
            raise ValidationError(
                f"unknown Grok model {model!r}; supported: {', '.join(grok_auth.MODELS)}"
            )

    def _headers(self, tokens: dict[str, Any]) -> dict[str, str]:
        access = str(tokens.get("access_token") or "")
        account = str(tokens.get("account_id") or "") or grok_auth._account_from_access(access)
        headers = {
            "Authorization": f"Bearer {access}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": grok_auth.USER_AGENT,
            "x-grok-client-identifier": grok_auth.CLIENT_IDENTIFIER,
            "x-grok-client-version": grok_auth.CLIENT_VERSION,
            "x-grok-client-mode": "interactive",
            "x-xai-token-auth": grok_auth.TOKEN_AUTH_HEADER,
            "x-authenticateresponse": "authenticate-response",
        }
        if account:
            headers["x-grok-user-id"] = account
        return headers

    def _account_hint(self) -> str:
        try:
            return str(self.AUTH.status(path=self._auth_path).get("account_id") or "")
        except Exception:
            return ""

    async def _resolve_tokens(self) -> dict[str, Any]:
        import asyncio

        return await asyncio.to_thread(self.AUTH.resolve_tokens, path=self._auth_path)

    async def _refresh_tokens(self, tokens: dict[str, Any]) -> dict[str, Any]:
        import asyncio

        try:
            return await asyncio.to_thread(self.AUTH.refresh, tokens, path=self._auth_path)
        except self.AUTH.GrokAuthError:
            raise
        except Exception as exc:
            raise self.AUTH.GrokAuthError(
                f"Grok token refresh failed ({type(exc).__name__}); {self.AUTH.LOGIN_HINT}",
                code="grok_refresh_failed",
            ) from exc

    def _unauthorized(self, model: str) -> Exception:
        return self.AUTH.GrokAuthError(
            f"Grok rejected the stored credential (HTTP 401) for {model}; {self.AUTH.LOGIN_HINT}",
            code="grok_unauthorized",
        )

    def _resolve_effort(self, model: str, explicit: str | None) -> str:
        effort = str(
            explicit or self._default_effort or config.get("reasoning_effort") or ""
        ).lower()
        if effort in ("", "none", "default", "auto"):
            return ""
        if effort not in ("low", "high"):
            raise ValidationError("Grok reasoning_effort must be low or high")
        return effort

    def _payload(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[Any] | None,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        instructions, items = self._to_responses_input(messages)
        input_items: list[dict[str, Any]] = [
            {"role": "system", "content": instructions or self.DEFAULT_INSTRUCTIONS}
        ]
        input_items.extend(items)
        payload: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "store": False,
            "stream": True,
            "reasoning": {"summary": "detailed"},
        }
        effort = self._resolve_effort(model, reasoning_effort)
        if effort:
            payload["reasoning"]["effort"] = effort
        if tools:
            payload["tools"] = _tools_payload(tools)
            payload["tool_choice"] = "auto"
        return payload


__all__ = ["GrokProvider"]
