"""Unified, read-only capability profile for one configured route.

The profile composes the existing sources of truth (context windows, provider
prompt-cache profiles, and the dated price table) without selecting a route
and without inventing vendor economics. Unknown metadata stays unknown:
``verified_complete`` is false and paid warmers must not run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from machina.core.config import config
from machina.core.context_windows import MODEL_CONTEXT_WINDOWS, context_window_for
from machina.core.pricing import PriceEntry, lookup_price, parse_overrides
from machina.core.provider import prompt_cache as _profiles
from machina.core.provider.prompt_cache import CACHE_NONE, CacheProfile, cache_profile

__all__ = ["ModelCapabilityProfile", "resolve_capabilities"]


@dataclass(frozen=True)
class ModelCapabilityProfile:
    """Verified facts about an explicitly selected provider/model route."""

    provider: str
    model: str
    context_window: int
    context_verified: bool
    prompt_cache: CacheProfile
    prompt_cache_verified: bool
    cache_ttl_seconds: int | None
    price: PriceEntry | None
    pricing_verified: bool
    verified_complete: bool

    @property
    def supports_prompt_cache(self) -> bool:
        return self.prompt_cache.mode in ("explicit", "implicit")

    @property
    def supports_paid_warmers(self) -> bool:
        """Paid cache warming requires *both* cache and price metadata."""
        return self.verified_complete and self.supports_prompt_cache


def _provider_name(provider: Any) -> str:
    if provider is None:
        return ""
    if isinstance(provider, str):
        return provider.strip().lower()
    spec = getattr(provider, "spec", None)
    name = getattr(spec, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().lower()
    class_name = type(provider).__name__
    class_name = class_name.removesuffix("Provider")
    return class_name.lower()


def _context_verified(model: str) -> bool:
    """Whether the context window came from a documented curated entry.

    Catalog-resolved windows are not assumed verified here: without a live
    catalog read there is no dated proof. Unknown models stay unverified and
    paid warmers remain off.
    """
    name = model.lower()
    if not name:
        return False
    return any(key in name for key in MODEL_CONTEXT_WINDOWS)


def _cache_economics(provider_name: str, model_name: str, profile: CacheProfile) -> int | None:
    """Verified cache TTL; unknown stays ``None``.

    Anthropic documents a five-minute ephemeral cache. Read/write price
    multipliers are not recorded here because they vary by model and change
    over time; paid economics use the dated price table instead.
    """
    if profile.mode != "explicit":
        return None
    model = (model_name or "").lower()
    if provider_name == "anthropic" or (
        provider_name == "openrouter" and ("anthropic" in model or "claude" in model)
    ):
        return 300
    return None


def resolve_capabilities(
    provider: Any,
    model: str | None = None,
    *,
    account_id: str = "",
    base_url: str = "",
    overrides: dict[str, dict[str, Any]] | None = None,
) -> ModelCapabilityProfile:
    """Resolve one profile without network I/O or route selection."""
    provider_name = _provider_name(provider)
    model_name = str(model or getattr(provider, "model", "") or "").strip()
    if isinstance(provider, str):
        # The gateway stores provider names as strings; resolve the same
        # verified profiles a constructed transport would use without
        # instantiating credentials or clients.
        if provider_name == "openrouter":
            candidate = model_name.lower()
            if "anthropic" in candidate or "claude" in candidate:
                profile = _profiles._OPENROUTER_ANTHROPIC
            elif any(
                marker in candidate
                for marker in ("openai/", "gpt-", "/gpt", "deepseek", "gemini", "google/")
            ):
                profile = _profiles._OPENAI_IMPLICIT
            else:
                profile = _profiles.CACHE_NONE
        elif provider_name == "anthropic":
            profile = _profiles._ANTHROPIC_EXPLICIT
        elif provider_name in ("chatgpt", "copilot"):
            profile = _profiles._OPENAI_IMPLICIT
        else:
            profile = _profiles._direct_profile(provider_name)
    else:
        profile = cache_profile(provider, model_name) if model_name else CACHE_NONE
    prompt_cache_verified = profile is not CACHE_NONE and profile.verified_at != "unverified"

    if overrides is None:
        raw = config.get("pricing_table_json")
        overrides = parse_overrides(raw if isinstance(raw, str) else "")
    price = lookup_price(provider_name, model_name, overrides=overrides)
    pricing_verified = bool(price and price.verified_at != "unverified")
    context_verified = _context_verified(model_name)
    context_window = context_window_for(
        model_name or None,
        provider,
        account_id=account_id,
        base_url=base_url,
    )
    cache_ttl = _cache_economics(provider_name, model_name, profile)
    return ModelCapabilityProfile(
        provider=provider_name,
        model=model_name,
        context_window=int(context_window),
        context_verified=context_verified,
        prompt_cache=profile,
        prompt_cache_verified=prompt_cache_verified,
        cache_ttl_seconds=cache_ttl,
        price=price,
        pricing_verified=pricing_verified,
        verified_complete=context_verified and prompt_cache_verified and pricing_verified,
    )
