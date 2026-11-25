"""Provider-name aliases used by config, routing, and the provider store.

An alias is a legacy or convenience spelling for a built-in provider. It is
canonicalized before lookup so an old saved route cannot create a second
provider group with a different model cache.
"""

from __future__ import annotations

PROVIDER_ALIASES: dict[str, str] = {
    # Pre-2.1 Hermes was often added as a named OpenAI-compatible provider.
    # The built-in `hermes` route now owns the same loopback proxy.
    "hermes-proxy": "hermes",
}


def canonical_provider(name: object) -> str:
    """Return the canonical provider name for an alias (lowercase)."""
    normalized = str(name or "").strip().lower()
    return PROVIDER_ALIASES.get(normalized, normalized)
