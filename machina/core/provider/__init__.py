"""LLM provider abstraction — facade over the split ``provider`` package.

The former single module was split into focused modules (W5 review item):

* ``reasoning``  — effort normalization and precedence
* ``specs``      — direct-provider table + chat-provider registry
* ``audit``      — sanitized audit events
* ``rate_limit`` — process-wide async token buckets
* ``caches``     — TTL/size-bounded response cache
* ``transport``  — retry/quota policy, SSE helpers, payload shaping
* ``base`` / ``openrouter`` / ``ollama`` / ``direct`` / ``anthropic`` / ``chatgpt``
* ``factory``    — ``get_provider``

``__all__`` below is byte-identical to the pre-split module; consumers keep
importing ``from machina.core.provider import ...`` unchanged.
"""

from importlib import import_module
from typing import Any

from machina.core.models import (  # noqa: F401 — historical re-export
    LLMResponse,
    StreamEvent,
    ToolDefinition,
)

from .audit import audit_log
from .base import LLMProvider
from .caches import _cache_clear  # noqa: F401 — documented test-only seam (tests/conftest.py)
from .rate_limit import AsyncTokenBucket, reload_rate_limiters
from .reasoning import (
    ANTHROPIC_THINKING_BUDGETS,  # noqa: F401 — pre-split import surface
    REASONING_EFFORTS,
    normalize_reasoning_effort,
)
from .specs import PROVIDER_SPECS, PROVIDERS, ProviderSpec

# Provider transports import the full HTTP/credential stack. Keep the package
# facade cheap so `provider.specs` and CLI help do not load every transport.
_LAZY_EXPORTS = {
    "AnthropicProvider": (".anthropic", "AnthropicProvider"),
    "ChatGPTProvider": (".chatgpt", "ChatGPTProvider"),
    "CopilotProvider": (".copilot", "CopilotProvider"),
    "DirectProvider": (".direct", "DirectProvider"),
    "GrokProvider": (".grok", "GrokProvider"),
    "OllamaProvider": (".ollama", "OllamaProvider"),
    "OpenRouterProvider": (".openrouter", "OpenRouterProvider"),
    "get_provider": (".factory", "get_provider"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


__all__ = [
    "audit_log",
    "AsyncTokenBucket",
    "LLMProvider",
    "get_provider",
    "PROVIDERS",
    "PROVIDER_SPECS",
    "ProviderSpec",
    "REASONING_EFFORTS",
    "normalize_reasoning_effort",
    "reload_rate_limiters",
]
