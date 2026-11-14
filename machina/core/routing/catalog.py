"""Connected-provider model inventory.

The inventory is deliberately price/capability aware but never invents a
universal quality score. Role fit is a policy prior over a small, explicit
table of model families; observed task outcomes can override it later.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from machina.core.provider.models_dev import provider_models
from machina.core.provider.specs import PROVIDERS, provider_names
from machina.core.routing.types import ModelProfile, ModelRoute

logger = logging.getLogger(__name__)

#: Config key carrying each direct provider's credential.
_PROVIDER_KEY: dict[str, str] = {
    "openrouter": "openrouter_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "google": "google_api_key",
    "mistral": "mistral_api_key",
    "groq": "groq_api_key",
    "together": "together_api_key",
    "deepseek": "deepseek_api_key",
    "xai": "xai_api_key",
    "opencode": "opencode_api_key",
    "kimi": "kimi_api_key",
}

#: Role priors by model family. Planner, supervisor, worker, utility.
#: These are starting points, not universal claims. A connected-model
#: evaluation can move a family up or down.
_FAMILY_ROLE_SCORES: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("claude-fable", (0.99, 0.98, 0.88, 0.55)),
    ("claude-opus", (0.95, 0.96, 0.84, 0.55)),
    ("claude-sonnet", (0.84, 0.86, 0.91, 0.66)),
    ("claude-haiku", (0.60, 0.64, 0.78, 0.95)),
    ("gpt-astra", (0.99, 0.97, 0.88, 0.48)),
    ("gpt-pro", (0.98, 0.98, 0.86, 0.45)),
    ("gpt-sol", (0.97, 0.95, 0.92, 0.62)),
    ("gpt-codex", (0.86, 0.84, 0.94, 0.55)),
    ("gpt-luna", (0.63, 0.67, 0.81, 0.97)),
    ("gpt-nano", (0.46, 0.51, 0.67, 0.99)),
    ("gpt-mini", (0.63, 0.66, 0.80, 0.91)),
    ("gpt-terra", (0.84, 0.84, 0.86, 0.72)),
    ("gpt", (0.91, 0.89, 0.86, 0.60)),
    ("deepseek-thinking", (0.80, 0.82, 0.88, 0.50)),
    ("deepseek-flash", (0.56, 0.61, 0.83, 0.97)),
    ("deepseek", (0.79, 0.80, 0.86, 0.70)),
    ("gemini-flash-lite", (0.45, 0.50, 0.66, 0.98)),
    ("gemini-flash", (0.61, 0.64, 0.81, 0.95)),
    ("gemini", (0.89, 0.87, 0.85, 0.66)),
    ("glm-flash", (0.50, 0.54, 0.73, 0.98)),
    ("glm", (0.78, 0.78, 0.82, 0.82)),
    ("qwen", (0.74, 0.74, 0.82, 0.86)),
    ("kimi-k3", (0.82, 0.82, 0.86, 0.70)),
    ("kimi", (0.71, 0.72, 0.80, 0.84)),
    ("minimax", (0.72, 0.72, 0.82, 0.86)),
    ("grok-build", (0.80, 0.78, 0.90, 0.55)),
    ("grok", (0.84, 0.82, 0.84, 0.70)),
    ("mistral", (0.61, 0.62, 0.74, 0.88)),
    ("mimo", (0.50, 0.54, 0.72, 0.98)),
    ("longcat", (0.50, 0.53, 0.70, 0.97)),
    ("nemotron", (0.52, 0.55, 0.71, 0.92)),
    ("muse", (0.62, 0.64, 0.75, 0.86)),
    ("big-pickle", (0.72, 0.73, 0.80, 0.82)),
    ("flint", (0.48, 0.52, 0.70, 0.96)),
    ("beam", (0.50, 0.53, 0.70, 0.95)),
    ("iceman", (0.48, 0.52, 0.68, 0.94)),
    ("inkling", (0.45, 0.49, 0.66, 0.95)),
)

#: Approximate USD per million tokens. Unknown is deliberately ``None``.
_FAMILY_COST: tuple[tuple[str, tuple[float, float]], ...] = (
    ("claude-fable", (10.0, 50.0)),
    ("claude-opus", (5.0, 25.0)),
    ("claude-sonnet", (2.0, 10.0)),
    ("claude-haiku", (0.1, 0.5)),
    ("gpt-astra", (10.0, 40.0)),
    ("gpt-pro", (30.0, 120.0)),
    ("gpt-sol", (2.0, 10.0)),
    ("gpt-codex", (1.75, 14.0)),
    ("gpt-luna", (0.1, 0.4)),
    ("gpt-nano", (0.05, 0.2)),
    ("gpt-mini", (0.75, 4.5)),
    ("gpt-terra", (2.0, 8.0)),
    ("gpt", (2.5, 10.0)),
    ("deepseek-thinking", (1.74, 3.48)),
    ("deepseek-flash", (0.3, 1.2)),
    ("gemini-flash-lite", (0.3, 1.2)),
    ("gemini-flash", (0.75, 3.0)),
    ("gemini", (2.0, 8.0)),
    ("glm-flash", (0.15, 0.6)),
    ("glm", (1.4, 4.4)),
    ("qwen", (0.2, 1.0)),
    ("kimi-k3", (3.0, 12.0)),
    ("kimi", (0.95, 4.0)),
    ("minimax", (0.3, 1.2)),
    ("grok", (2.0, 10.0)),
    ("mistral", (0.68, 2.0)),
    ("mimo", (0.2, 0.8)),
    ("big-pickle", (0.0, 0.0)),
)

#: Families that are usually subscription/flat-rate. They can still be
#: expensive in practice because they consume a finite quota.
_QUOTA_SENSITIVE = frozenset({"chatgpt", "kiro", "copilot", "grok"})

_FAMILY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"claude-(fable|opus|sonnet|haiku)", r"claude-\1"),
    (r"gpt[-_ ]?(?:\d+(?:[.-]\d+)*[-_ ]?)?(astra|sol|luna|terra|pro|nano|mini|codex)", r"gpt-\1"),
    (r"deepseek[-_ ]?(?:v?\d+(?:[.-]\d+)*[-_ ]?)?(thinking|flash|pro)", r"deepseek-\1"),
    (r"gemini[-_ ]?(?:\d+(?:[.-]\d+)*[-_ ]?)?(flash-lite|flash|pro)", r"gemini-\1"),
    (r"glm[-_ ]?(?:\d+(?:[.-]\d+)*[-_ ]?)?flash", "glm-flash"),
    (r"kimi[-_ ]?k?(\d+(?:[.-]\d+)*)", r"kimi-k\1"),
    (r"grok[-_ ]?(?:build|\d+(?:[.-]\d+)*)", "grok"),
)

#: Transport for a route. OpenCode free models are only usable inside the
#: OpenCode session/MCP path; the direct Zen endpoint rejects them.
_OPENCODE_FREE_SENTINELS = ("-free", ":free")


@dataclass
class ModelInventory:
    """All connected models and their callable routes."""

    profiles: tuple[ModelProfile, ...] = ()
    provider_status: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    current: ModelProfile | None = None

    @property
    def routes(self) -> tuple[ModelRoute, ...]:
        return tuple(
            ModelRoute(
                profile=profile,
                transport=_transport_for(profile),
                available=True,
                reason=_route_reason(profile),
            )
            for profile in self.profiles
        )

    def find(self, provider: str, model: str) -> ModelProfile | None:
        needle = _normalize_token(model)
        for profile in self.profiles:
            if profile.provider == provider and (
                _normalize_token(profile.model) == needle
                or _normalize_token(profile.canonical) == needle
            ):
                return profile
        return None

    def by_role(self, role: str) -> tuple[ModelProfile, ...]:
        return tuple(
            profile for profile in self.profiles if float(profile.role_scores.get(role, 0.0)) > 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": dict(self.provider_status),
            "models": [profile.to_dict() for profile in self.profiles],
            "current": self.current.to_dict() if self.current else None,
            "warnings": list(self.warnings),
        }


def _normalize_token(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("_", "-")
        .replace(".", "-")
        .replace(":", "-")
        .replace("/", "-")
    )


def _canonical_family(value: str) -> str:
    token = _normalize_token(value)
    for pattern, replacement in _FAMILY_PATTERNS:
        match = re.search(pattern, token)
        if match:
            try:
                return match.expand(replacement)
            except re.error:
                return match.group(0)
    return token


def _family_for(profile_row: dict[str, Any]) -> str:
    model = _normalize_token(str(profile_row.get("id") or ""))
    explicit = _canonical_family(str(profile_row.get("family") or ""))
    if explicit:
        return explicit
    for family, _scores in _FAMILY_ROLE_SCORES:
        if family in model:
            return family
    return _canonical_family(model) or "unknown"


def _role_scores(family: str, model_id: str, description: str) -> dict[str, float]:
    scores = (0.55, 0.58, 0.66, 0.72)
    for prefix, candidate in _FAMILY_ROLE_SCORES:
        if family.startswith(prefix) or prefix in family:
            scores = candidate
            break
    else:
        model = _normalize_token(model_id)
        for prefix, candidate in _FAMILY_ROLE_SCORES:
            if prefix in model:
                scores = candidate
                break

    text = f"{model_id} {description}".lower()
    coding = any(
        token in text for token in ("code", "coder", "codex", "agent", "tool", "swe", "dev")
    )
    reasoning = any(token in text for token in ("reason", "thinking", "complex", "plan"))
    review = any(token in text for token in ("review", "audit", "verif", "analysis"))

    planner, supervisor, worker, utility = scores
    if coding:
        worker = min(0.99, worker + 0.03)
        supervisor = min(0.99, supervisor + 0.01)
    if reasoning:
        planner = min(0.99, planner + 0.02)
        supervisor = min(0.99, supervisor + 0.02)
    if review:
        supervisor = min(0.99, supervisor + 0.02)

    return {
        "planner": round(planner, 4),
        "supervisor": round(supervisor, 4),
        "worker": round(worker, 4),
        "utility": round(utility, 4),
    }


def _task_scores(
    family: str, model_id: str, description: str, context_window: int
) -> dict[str, float]:
    text = f"{family} {model_id} {description}".lower()
    base = 0.55
    coding = 0.72 if any(
        token in text for token in ("code", "coder", "codex", "swe", "dev", "agent", "tool")
    ) else base
    review = 0.70 if any(token in text for token in ("review", "audit", "reason", "think")) else 0.62
    research = 0.72 if any(
        token in text for token in ("research", "knowledge", "long", "vision")
    ) else 0.62
    web = min(0.92, coding + 0.08)
    debug = min(0.95, coding + 0.06)
    docs = 0.68 if "mini" in text or "flash" in text or "luna" in text else 0.62
    long_context = min(0.99, 0.55 + min(context_window, 2_000_000) / 2_000_000 * 0.4)
    return {
        "coding": round(coding, 4),
        "web-build": round(web, 4),
        "debug": round(debug, 4),
        "review": round(review, 4),
        "research": round(research, 4),
        "documentation": round(docs, 4),
        "long-context": round(long_context, 4),
    }


def _costs_for(family: str, model_id: str) -> tuple[float | None, float | None]:
    lowered = _normalize_token(model_id)
    if any(sentinel in lowered for sentinel in _OPENCODE_FREE_SENTINELS):
        return (0.0, 0.0)
    for prefix, (input_cost, output_cost) in _FAMILY_COST:
        if family.startswith(prefix) or prefix in family or prefix in lowered:
            return (input_cost, output_cost)
    return (None, None)


def _is_free(model_id: str, input_cost: float | None, output_cost: float | None) -> bool:
    lowered = _normalize_token(model_id)
    if any(sentinel in lowered for sentinel in _OPENCODE_FREE_SENTINELS):
        return True
    return input_cost == 0.0 and output_cost == 0.0


def _transport_for(profile: ModelProfile) -> str:
    if profile.provider == "opencode_session":
        return "native"
    if profile.provider == "opencode" and profile.free:
        return "mcp"
    if profile.provider == "opencode":
        return "proxy"
    return "native"


def _route_reason(profile: ModelProfile) -> str:
    if profile.provider == "opencode_session":
        return "OpenCode local session bridge (text-only)"
    if profile.provider == "opencode" and profile.free:
        return "free OpenCode model: requires OpenCode session/MCP transport"
    if profile.provider in _QUOTA_SENSITIVE:
        return "subscription/quota-backed route"
    return "connected provider"


def profile_from_row(provider: str, row: dict[str, Any]) -> ModelProfile:
    """Normalize one provider catalog row into a :class:`ModelProfile`."""
    model = str(row.get("id") or "").strip()
    family = _family_for(row)
    description = str(row.get("description") or "")
    context_window = int(row.get("contextWindow") or row.get("context") or 0) or 128_000
    max_output = int(row.get("maxOutputTokens") or row.get("output") or 0) or 32_000
    tools = bool(row.get("tools", True))
    if provider == "opencode_session":
        # The session bridge cannot carry arbitrary Machina tool schemas.
        tools = False
    reasoning = bool(row.get("reasoning", bool(row.get("efforts"))))
    vision = bool(row.get("vision")) or any(
        token in f"{description} {model}".lower()
        for token in ("vision", "multimodal", "image", "visual")
    )
    input_cost, output_cost = _costs_for(family, model)
    source = str(row.get("source") or "models.dev")
    confidence = "high" if source in {"live", "models.dev"} else "medium"
    quota = 0.10 if provider in _QUOTA_SENSITIVE else 0.0
    if provider == "opencode" and _is_free(model, input_cost, output_cost):
        quota = 0.5
    return ModelProfile(
        provider=provider,
        model=model,
        canonical=f"{family}:{model}" if family and family != model else model,
        display_name=str(row.get("displayName") or row.get("name") or model),
        family=family,
        description=description,
        context_window=context_window,
        max_output_tokens=max_output,
        tools=tools,
        reasoning=reasoning,
        vision=vision,
        input_cost_per_mtok=input_cost,
        output_cost_per_mtok=output_cost,
        cache_read_per_mtok=None,
        cache_write_per_mtok=None,
        free=_is_free(model, input_cost, output_cost),
        quota_pressure=quota,
        source=source,
        confidence=confidence,
        role_scores=_role_scores(family, model, description),
        task_scores=_task_scores(family, model, description, context_window),
    )


def _config_value(config: Any, key: str) -> str:
    try:
        return str(config.get(key) or "").strip()
    except Exception:
        return ""


def _provider_ready(name: str, config: Any, *, probe_kiro: bool) -> tuple[bool, str]:
    if name in _PROVIDER_KEY:
        key = _config_value(config, _PROVIDER_KEY[name])
        return bool(key), "key configured" if key else "no API key"
    if name == "chatgpt":
        try:
            from machina.security import chatgpt_auth

            status = chatgpt_auth.status()
            signed = bool(status.get("signed_in")) and not status.get("expired")
            return signed, "account signed in" if signed else "not signed in"
        except Exception as exc:
            return False, f"auth unavailable: {type(exc).__name__}"
    if name == "copilot":
        try:
            from machina.security import copilot_auth

            status = copilot_auth.status(probe_gh=False)
            signed = bool(status.get("signed_in"))
            return signed, "account signed in" if signed else "not signed in"
        except Exception as exc:
            return False, f"auth unavailable: {type(exc).__name__}"
    if name in {"grok", "xai"}:
        key = _config_value(config, "xai_api_key")
        if key:
            return True, "key configured"
        try:
            from machina.security import grok_auth

            status = grok_auth.status()
            signed = bool(status.get("signed_in")) and not status.get("expired")
            return signed, "account signed in" if signed else "not signed in"
        except Exception as exc:
            return False, f"auth unavailable: {type(exc).__name__}"
    if name == "kiro":
        try:
            from machina.core.provider import kiro_proxy

            cached = kiro_proxy.cached_models()
            if cached:
                return True, "cached gateway catalog"
            if probe_kiro and kiro_proxy.gateway_reachable(timeout=0.25):
                return True, "gateway reachable"
            return False, "gateway not already connected"
        except Exception as exc:
            return False, f"gateway unavailable: {type(exc).__name__}"
    if name == "opencode_session":
        try:
            from machina.core.provider.opencode_session import service_password

            service_password()
            return True, "local OpenCode service credentials available"
        except Exception as exc:
            return False, f"OpenCode service unavailable: {type(exc).__name__}"
    if name == "custom":
        base = _config_value(config, "custom_base_url")
        return bool(base), "base URL configured" if base else "no base URL"
    if name == "ollama":
        try:
            from machina.core.provider.ollama import OllamaProvider

            reachable = OllamaProvider().reachable()
            return bool(reachable), "daemon reachable" if reachable else "daemon unavailable"
        except Exception:
            return False, "daemon unavailable"
    if name in provider_names():
        try:
            from machina.security.providers import get_provider_entry, is_named_provider

            if not is_named_provider(name):
                return False, "not a named provider"
            entry = get_provider_entry(name) or {}
            return bool(entry.get("base_url")), "named provider configured"
        except Exception:
            return False, "provider store unavailable"
    return False, "unknown provider"


def _rows_for_provider(name: str, config: Any) -> list[dict[str, Any]]:
    if name == "opencode_session":
        try:
            from machina.core.provider.opencode_session import available_models

            available = available_models()
        except Exception:
            available = ()
        catalog_rows = {
            str(row.get("id")): row for row in provider_models("opencode") if row.get("id")
        }
        result: list[dict[str, Any]] = []
        for model_id in available:
            provider_id, _, bare_id = model_id.partition("/")
            row = dict(catalog_rows.get(bare_id) or catalog_rows.get(model_id) or {})
            row.setdefault("id", model_id)
            row.setdefault("displayName", model_id)
            row.setdefault("description", f"OpenCode available model ({provider_id})")
            row["tools"] = False
            row.setdefault("reasoning", True)
            row.setdefault("source", "opencode-models")
            result.append(row)
        if result:
            return result
        try:
            configured = str(config.get("model") or "").strip()
        except Exception:
            configured = ""
        if configured:
            return [
                {
                    "id": configured,
                    "displayName": configured,
                    "description": "Configured OpenCode session model",
                    "tools": False,
                    "source": "configured",
                }
            ]
        return []
    rows = provider_models(name)
    if rows:
        return rows
    if name == "chatgpt":
        try:
            from machina.core.provider import catalog

            cached = catalog.cached_catalog()
            if cached:
                return [
                    {
                        "id": entry.slug,
                        "displayName": entry.display_name,
                        "description": entry.description,
                        "contextWindow": entry.context_window,
                        "maxOutputTokens": 128_000,
                        "family": _canonical_family(entry.slug),
                        "tools": True,
                        "reasoning": bool(
                            getattr(entry, "supported_efforts", None)
                            or getattr(entry, "advertised_efforts", None)
                        ),
                        "source": "chatgpt-catalog",
                    }
                    for entry in cached
                    if str(getattr(entry, "visibility", "")).lower()
                    not in {"hide", "hidden"}
                ]
            return [
                {
                    "id": row["slug"],
                    "displayName": row.get("display_name") or row["slug"],
                    "description": row.get("description") or "",
                    "contextWindow": row.get("context_window")
                    or row.get("max_context_window")
                    or 400_000,
                    "maxOutputTokens": 128_000,
                    "source": "chatgpt-fallback",
                }
                for row in catalog.VERIFIED_FALLBACK_MODELS
            ]
        except Exception:
            return []
    if name == "kiro":
        try:
            from machina.core.provider import kiro_proxy

            return kiro_proxy.cached_models() or kiro_proxy.fallback_models()
        except Exception:
            return []
    if name in {"custom", "ollama"} or name in provider_names():
        default_model = ""
        try:
            default_model = str(config.get("model") or "").strip()
        except Exception:
            default_model = ""
        if default_model and (name in {"custom", "ollama"} or name == config.get("provider")):
            return [
                {
                    "id": default_model,
                    "displayName": default_model,
                    "description": "Configured endpoint default model",
                    "contextWindow": 128_000,
                    "maxOutputTokens": 32_000,
                    "source": "configured",
                }
            ]
    return []


def build_inventory(
    config: Any = None,
    *,
    providers: Iterable[str] | None = None,
    probe_kiro: bool = True,
    include_unavailable: bool = False,
) -> ModelInventory:
    """Build the connected-model inventory without making a model call.

    ``providers`` is an explicit test/administration seam. Normal callers use
    the configured credentials plus auth stores. Catalog rows come from the
    bundled models.dev snapshot; live refresh is a separate operation so this
    function can run on every turn without network latency.
    """
    if config is None:
        from machina.core.config import config as live_config

        config = live_config
    names = tuple(dict.fromkeys(providers or (*PROVIDERS, *provider_names())))
    profiles: list[ModelProfile] = []
    status: dict[str, str] = {}
    warnings: list[str] = []
    for name in names:
        ready, reason = _provider_ready(name, config, probe_kiro=probe_kiro)
        status[name] = "ready" if ready else reason
        if not ready and not include_unavailable:
            continue
        rows = _rows_for_provider(name, config)
        if not rows:
            if ready:
                warnings.append(f"{name}: connected but no model catalog available")
            continue
        seen: set[str] = set()
        for row in rows:
            profile = profile_from_row(name, row)
            if not profile.model or profile.key in seen:
                continue
            seen.add(profile.key)
            profiles.append(profile)

    # Always include the effective session/global route even when its
    # provider has no discovery endpoint. This preserves single-model mode and
    # makes the inventory honest about the one route the user explicitly chose.
    current: ModelProfile | None = None
    try:
        provider = str(config.get("provider") or "").strip()
        model = str(config.get("model") or "").strip()
    except Exception:
        provider, model = "", ""
    if provider and model:
        current = next(
            (profile for profile in profiles if profile.provider == provider and profile.model == model),
            None,
        )
        if current is None:
            row = {
                "id": model,
                "displayName": model,
                "description": "Currently configured route",
                "source": "configured",
            }
            current = profile_from_row(provider, row)
            profiles.append(current)

    return ModelInventory(
        profiles=tuple(profiles),
        provider_status=status,
        warnings=tuple(warnings),
        current=current,
    )


__all__ = ["ModelInventory", "build_inventory", "profile_from_row"]
