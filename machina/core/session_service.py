"""Shared session-creation semantics for every transport.

The HTTP API and the gateway RPC used to resolve agents/routes, validate
request fields, build initial state, and persist a session independently.
This module owns that common work; transports keep their request/response
shapes, transport defaults, and their own error envelopes. Validation failures
carry a stable, transport-neutral code plus the final user-facing message so a
caller can map them without rewriting text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from machina.core.agent_def import agent_registry
from machina.core.agent_factory import resolve_provider_model
from machina.core.config import config
from machina.core.models import Session
from machina.core.provider import PROVIDERS
from machina.core.session import session_manager

__all__ = [
    "INVALID_MODEL",
    "UNKNOWN_AGENT",
    "UNKNOWN_EMOTION",
    "UNKNOWN_PROVIDER",
    "SessionCreateError",
    "SessionCreateSpec",
    "SessionService",
    "session_service",
]

# Stable transport-neutral rejection codes. Transports map these to their
# native error codes while preserving ``message`` exactly.
UNKNOWN_AGENT = "unknown_agent"
UNKNOWN_PROVIDER = "unknown_provider"
INVALID_MODEL = "invalid_model"
UNKNOWN_EMOTION = "unknown_emotion"


class SessionCreateError(ValueError):
    """A session-creation request rejected by shared validation.

    ``code`` identifies the failure for transport mapping; ``message`` is the
    final user-facing text (the same text both transports returned before the
    shared seam existed).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SessionCreateSpec:
    """Transport-neutral description of a session to create.

    ``default_provider`` / ``default_model`` are transport defaults (the
    gateway passes its live route here); HTTP leaves them unset so the shared
    resolver falls back to global config exactly as before.
    """

    title: str | None = None
    agent_id: str = "harness"
    model: str | None = None
    provider: str | None = None
    goal: str | None = None
    emotion: str | None = None
    desktop_workspace_root: str | None = None
    default_provider: str | None = None
    default_model: str | None = None


def _validate(spec: SessionCreateSpec) -> None:
    """Validate fields in the order the HTTP path already applied.

    Keeping emotion first preserves the pre-seam response when a request has
    more than one invalid field; the RPC path never supplied these fields, so
    its only observable validation (agent) is unaffected.
    """
    if spec.emotion:
        from machina.memory.persona import EmotionTopology

        if spec.emotion not in EmotionTopology.EMOTION_PROFILES:
            raise SessionCreateError(UNKNOWN_EMOTION, "unknown emotion")
    if spec.provider is not None and spec.provider not in PROVIDERS:
        raise SessionCreateError(UNKNOWN_PROVIDER, f"unknown provider: {spec.provider}")
    if spec.model is not None and (not isinstance(spec.model, str) or not spec.model.strip()):
        raise SessionCreateError(INVALID_MODEL, "model must be a non-empty string")


async def _resolve_definition(agent_id: str):
    """Resolve an agent definition with each transport's existing semantics.

    ``harness`` tolerates a failed registry lookup (the factory's permitted
    fallback); any other agent fails closed because the turn factory would
    otherwise fail later.
    """
    if agent_id == "harness":
        try:
            return await agent_registry.get(agent_id)
        except Exception:
            return None
    try:
        definition = await agent_registry.get(agent_id)
    except Exception as exc:
        raise SessionCreateError(UNKNOWN_AGENT, f"unknown agent: {agent_id}") from exc
    if definition is None:
        raise SessionCreateError(UNKNOWN_AGENT, f"unknown agent: {agent_id}")
    return definition


def _initial_state(spec: SessionCreateSpec) -> dict[str, Any] | None:
    """Build the common initial state; absent/empty fields stay absent."""
    state: dict[str, Any] = {}
    if spec.emotion:
        state["emotion"] = spec.emotion
    if spec.desktop_workspace_root:
        state["desktop_workspace_root"] = spec.desktop_workspace_root
    return state or None


class SessionService:
    """Own session creation: validation, route resolution, and persistence."""

    async def create(self, spec: SessionCreateSpec) -> Session:
        """Validate *spec*, resolve its agent/route, and persist a session.

        Raises :class:`SessionCreateError` for request validation failures.
        The transport owns the error envelope and the post-create activation
        step (``grant_full_default``), matching the previous ordering and
        diagnostics for both callers.
        """
        _validate(spec)
        agent_id = str(spec.agent_id or "harness").strip() or "harness"
        definition = await _resolve_definition(agent_id)
        provider, model = resolve_provider_model(
            definition=definition,
            provider_override=spec.provider,
            model_override=spec.model,
            default_provider=spec.default_provider,
            default_model=spec.default_model,
        )
        return await session_manager.create(
            title=spec.title,
            agent_id=agent_id,
            model=model,
            provider=provider,
            goal=spec.goal,
            context_budget=config.get("context_budget"),
            state=_initial_state(spec),
        )


session_service = SessionService()
