"""Mode resolution, eligibility decisions, and bounded failure breakers.

No policy function performs I/O. Callers pass the context they have; a missing
guard fact rejects the optimization rather than guessing.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from machina.core.config import config
from machina.core.optimization.cache_context import CacheContext
from machina.core.optimization.canonicalizer import CanonicalRequest, has_freshness_markers
from machina.memory.redaction import redact_secrets

__all__ = [
    "CircuitBreaker",
    "Eligibility",
    "OptimizationMode",
    "Reason",
    "breakers",
    "config_mode",
    "config_summary",
    "effective_mode",
    "evaluate_compression",
    "evaluate_semantic_cache",
    "prompt_cache_min_prefix_tokens",
    "prompt_cache_warming_enabled",
    "reset_breakers",
]


class OptimizationMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    WIRED = "wired"


class Reason(StrEnum):
    MODE_OFF = "mode_off"
    AUXILIARY_NOT_ALLOWLISTED = "auxiliary_not_allowlisted"
    MISSING_SESSION = "missing_session"
    TOOL_REQUEST = "tool_request"
    TEMPERATURE_TOO_HIGH = "temperature_too_high"
    FRESHNESS_SENSITIVE = "freshness_sensitive"
    ATTACHMENTS = "attachments"
    SECRET_DETECTED = "secret_detected"
    PII_DETECTED = "pii_detected"
    CODE_CONTENT = "code_content"
    REQUEST_TOO_LONG = "request_too_long"
    INELIGIBLE = "ineligible"
    ELIGIBLE = "eligible"
    BELOW_TRIGGER = "below_trigger"


@dataclass(frozen=True)
class Eligibility:
    """One deterministic decision with bounded reason codes."""

    allowed: bool
    mode: OptimizationMode
    reasons: tuple[str, ...] = ()

    @property
    def should_compute(self) -> bool:
        return self.allowed and self.mode in (OptimizationMode.SHADOW, OptimizationMode.WIRED)

    @property
    def should_serve(self) -> bool:
        return self.allowed and self.mode is OptimizationMode.WIRED


_MODE_KEYS = {
    "semantic_cache": "optimization_semantic_cache_mode",
    "compression": "optimization_compression_mode",
    "prompt_cache": "optimization_warmers_prompt_cache_mode",
}


def effective_mode(subsystem: str) -> OptimizationMode:
    """Resolve one subsystem's mode, honoring the emergency kill switch."""
    if _config_bool("optimization_emergency_disabled"):
        return OptimizationMode.OFF
    key = _MODE_KEYS.get(subsystem, f"optimization_{subsystem}_mode")
    try:
        raw = str(config.get(key) or "off").strip().lower()
    except Exception:
        return OptimizationMode.OFF
    try:
        return OptimizationMode(raw)
    except ValueError:
        return OptimizationMode.OFF


def _config_bool(name: str) -> bool:
    try:
        return bool(config.get(name))
    except Exception:
        return False


def _config_float(name: str, default: float) -> float:
    try:
        return float(config.get(name))
    except (TypeError, ValueError):
        return default


def _redacted(text: str) -> bool:
    try:
        return redact_secrets(text).was_redacted
    except Exception:
        # A broken redactor must fail closed for cache writes.
        return True


_PII_RE = re.compile(
    r"(?:\b[\w.+-]+@[\w-]+\.[\w.-]+\b"
    r"|\b\d{3}-\d{2}-\d{4}\b"
    r"|\b\+?\d{10,15}\b)"
)


def _contains_pii(text: str) -> bool:
    return bool(_PII_RE.search(text or ""))


_CODE_RE = re.compile(r"```|(?:^|\n)diff --git|(?:^|\n)<<<<<<<")


def _contains_code(text: str) -> bool:
    return bool(_CODE_RE.search(text or ""))


def _auxiliary_cross_session_allowed() -> bool:
    return _config_bool("optimization_semantic_cache_allow_cross_session_aux")


def evaluate_semantic_cache(
    context: CacheContext | None,
    canonical: CanonicalRequest | None,
    *,
    tools_present: bool,
) -> Eligibility:
    """Decide whether a request may participate in the semantic cache.

    The decision is intentionally stricter than "looks read-only": caller-
    supplied scope facts, a bounded request, and the auxiliary allowlist are
    all required. Shadow mode still computes the decision but callers must
    never serve its result.
    """
    mode = effective_mode("semantic_cache")
    if mode is OptimizationMode.OFF:
        return Eligibility(False, mode, (Reason.MODE_OFF,))
    if context is None or canonical is None:
        return Eligibility(False, mode, (Reason.INELIGIBLE,))
    if not context.is_auxiliary_read_only:
        return Eligibility(False, mode, (Reason.AUXILIARY_NOT_ALLOWLISTED,))

    if tools_present or context.tools_present:
        return Eligibility(False, mode, (Reason.TOOL_REQUEST,))
    if context.temperature > 0.3:
        return Eligibility(False, mode, (Reason.TEMPERATURE_TOO_HIGH,))
    if context.freshness_sensitive or has_freshness_markers(canonical.text):
        return Eligibility(False, mode, (Reason.FRESHNESS_SENSITIVE,))
    if context.attachments:
        return Eligibility(False, mode, (Reason.ATTACHMENTS,))
    # Embedding adapters truncate their input; a longer canonical request
    # would compare only a prefix and could produce a false hit.
    if canonical.char_count > 2000:
        return Eligibility(False, mode, (Reason.REQUEST_TOO_LONG,))
    if _redacted(canonical.text):
        return Eligibility(False, mode, (Reason.SECRET_DETECTED,))
    if _contains_pii(canonical.text):
        return Eligibility(False, mode, (Reason.PII_DETECTED,))
    if _contains_code(canonical.text):
        return Eligibility(False, mode, (Reason.CODE_CONTENT,))

    if context.session_id is None:
        if not _auxiliary_cross_session_allowed():
            return Eligibility(False, mode, (Reason.MISSING_SESSION,))

    return Eligibility(True, mode, (Reason.ELIGIBLE,))


def evaluate_compression(
    *,
    mode: OptimizationMode,
    tokens_before: int,
    context_window: int,
    messages_present: bool,
) -> Eligibility:
    """Whether learned compression may run for this request."""
    if mode is OptimizationMode.OFF:
        return Eligibility(False, mode, (Reason.MODE_OFF,))
    if not messages_present:
        return Eligibility(False, mode, (Reason.INELIGIBLE,))
    min_tokens = int(_config_float("optimization_compression_min_trigger_tokens", 8192))
    ratio = (tokens_before / context_window) if context_window > 0 else 0.0
    trigger_ratio = _config_float("optimization_compression_trigger_ratio", 0.5)
    if tokens_before < min_tokens and ratio < trigger_ratio:
        return Eligibility(False, mode, (Reason.BELOW_TRIGGER,))
    return Eligibility(True, mode, (Reason.ELIGIBLE,))


@dataclass
class _BreakerState:
    failures: int = 0
    opened_at: float = 0.0


@dataclass
class CircuitBreaker:
    """Per-subsystem failure breaker; no cross-subsystem coupling."""

    failure_threshold: int = 3
    cooldown_seconds: float = 60.0
    _states: dict[str, _BreakerState] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def allow(self, subsystem: str) -> bool:
        with self._lock:
            state = self._states.get(subsystem)
            if state is None or state.failures < self.failure_threshold:
                return True
            if time.monotonic() - state.opened_at >= self.cooldown_seconds:
                self._states.pop(subsystem, None)
                return True
            return False

    def record_success(self, subsystem: str) -> None:
        with self._lock:
            self._states.pop(subsystem, None)

    def record_failure(self, subsystem: str) -> None:
        with self._lock:
            state = self._states.setdefault(subsystem, _BreakerState())
            state.failures += 1
            if state.failures >= self.failure_threshold:
                state.opened_at = time.monotonic()

    def reset(self, subsystem: str | None = None) -> None:
        with self._lock:
            if subsystem is None:
                self._states.clear()
            else:
                self._states.pop(subsystem, None)


_BREAKERS = CircuitBreaker()


def breakers() -> CircuitBreaker:
    return _BREAKERS


def reset_breakers() -> None:
    _BREAKERS.reset()


def config_mode(subsystem: str) -> str:
    """Small helper for status surfaces (returns the raw configured value)."""
    key = _MODE_KEYS.get(subsystem, f"optimization_{subsystem}_mode")
    try:
        return str(config.get(key) or "off")
    except Exception:
        return "off"


def config_summary() -> dict[str, Any]:
    return {
        "emergencyDisabled": _config_bool("optimization_emergency_disabled"),
        "semanticCache": config_mode("semantic_cache"),
        "promptCache": config_mode("prompt_cache"),
        "compression": config_mode("compression"),
        "startupWarmers": _config_bool("optimization_warmers_startup_enabled"),
    }


def prompt_cache_min_prefix_tokens() -> int:
    """Minimum stable-prefix size required when the tri-state mode is wired.

    The legacy ``prompt_cache_enabled`` pilot keeps its historical no-minimum
    behavior; only the new wired mode enforces the operational threshold.
    """
    if effective_mode("prompt_cache") is not OptimizationMode.WIRED:
        return 0
    try:
        return max(0, int(config.get("optimization_warmers_min_prefix_tokens")))
    except (TypeError, ValueError):
        return 0


def prompt_cache_warming_enabled() -> bool:
    """Whether the provider may attach prompt-cache breakpoints.

    The new tri-state mode wins when explicitly set to ``shadow``/``wired``.
    With the default ``off``, the historical ``prompt_cache_enabled`` pilot
    flag still works, and the emergency switch disables both. Invalid legacy
    values propagate to the provider wrapper, which logs and disables.
    """
    if _config_bool("optimization_emergency_disabled"):
        return False
    mode = effective_mode("prompt_cache")
    if mode is OptimizationMode.WIRED:
        return True
    if mode is OptimizationMode.SHADOW:
        return False
    return bool(config.get("prompt_cache_enabled"))
