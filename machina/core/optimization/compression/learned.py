"""Optional learned prompt compression, disabled by default.

The deterministic reducers remain the default. This adapter only replaces
eligible old assistant/tool text after the semantic cache misses; it never
touches system/instructions, the current turn, tool schemas, or cached
prefixes. The optional LLMLingua-2 backend is loaded lazily and any import,
load, timeout, anchor, or similarity failure reverts to the original text.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import importlib.util
import json
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from machina.core.assembler import get_token_count
from machina.core.config import config
from machina.core.context_windows import context_window_for
from machina.core.optimization.compression.quality_guard import evaluate_compression
from machina.core.optimization.compression.span_protector import (
    CompressionSpan,
    compressible_spans,
)
from machina.core.optimization.policy import (
    OptimizationMode,
    breakers,
    effective_mode,
)
from machina.core.optimization.policy import (
    evaluate_compression as evaluate_trigger,
)
from machina.core.optimization.shadow import ShadowEvent, schedule_shadow
from machina.memory.embeddings import embed_query

__all__ = [
    "CompressionResult",
    "Compressor",
    "LlMLinguaCompressor",
    "NullCompressor",
    "build_compressor",
    "compress_messages",
    "maybe_compress_request",
]

_DEFAULT_TARGET_RATIO = 0.6
_DEFAULT_MIN_RATIO = 0.4
_DEFAULT_MIN_SIMILARITY = 0.88
_COMPRESSOR_CACHE: dict[str, Compressor] = {}
_COMPRESSOR_LOCK = threading.Lock()

#: Dedicated single-worker pool for the (CPU-heavy, model-loading) LLMLingua
#: call. A ``compress()`` that outlives the caller's timeout keeps its thread
#: running — threads cannot be cancelled. On the shared default executor those
#: abandoned calls pile up and starve latency-sensitive ``asyncio.to_thread``
#: users (file tools, the RAG embedder). One worker bounds the abandonment to
#: a single thread, and the compressor's own lock serializes access anyway.
_COMPRESSOR_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="machina-compress"
)


class Compressor(Protocol):
    revision: str
    available: bool

    def compress(self, text: str, *, ratio: float) -> str | None: ...


class NullCompressor:
    revision = "none"
    available = False

    def compress(self, text: str, *, ratio: float) -> str | None:
        return None


class LlMLinguaCompressor:
    """Lazy optional adapter; no import or model load happens when unused."""

    revision = "llmlingua2:default"

    def __init__(self, model_name: str = "") -> None:
        self.model_name = model_name or (
            "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"
        )
        self.revision = f"llmlingua2:{self.model_name}"
        self._compressor: Any | None = None
        self._error: Exception | None = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return importlib.util.find_spec("llmlingua") is not None

    def _load(self) -> Any | None:
        if self._compressor is not None or self._error is not None:
            return self._compressor
        try:
            module = importlib.import_module("llmlingua")
            prompt_compressor = getattr(module, "PromptCompressor")

            self._compressor = prompt_compressor(
                model_name=self.model_name,
                use_llmlingua2=True,
                device_map="cpu",
            )
        except Exception as exc:
            self._error = exc
            return None
        return self._compressor

    def compress(self, text: str, *, ratio: float) -> str | None:
        with self._lock:
            compressor = self._load()
            if compressor is None:
                return None
            try:
                result = compressor.compress_prompt(
                    [text],
                    rate=max(0.1, min(0.9, float(ratio))),
                    force_tokens=["\n", ".", "?", "!"],
                )
                if isinstance(result, dict):
                    compressed = result.get("compressed_prompt")
                    if isinstance(compressed, str):
                        return compressed
                if isinstance(result, str):
                    return result
            except Exception:
                return None
            return None

    async def compress_async(self, text: str, *, ratio: float) -> str | None:
        """``compress`` on the dedicated worker pool (see the executor note)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _COMPRESSOR_EXECUTOR, functools.partial(self.compress, text, ratio=ratio)
        )


def build_compressor(backend: str | None = None) -> Compressor:
    selected = (backend or str(config.get("optimization_compression_backend") or "")).lower()
    if selected in ("", "none"):
        return NullCompressor()
    if selected == "llmlingua2":
        with _COMPRESSOR_LOCK:
            compressor = _COMPRESSOR_CACHE.get(selected)
            if compressor is None:
                compressor = LlMLinguaCompressor()
                _COMPRESSOR_CACHE[selected] = compressor
            return compressor
    return NullCompressor()


@dataclass(frozen=True)
class CompressionResult:
    messages: list[dict[str, Any]]
    applied: bool
    tokens_before: int
    tokens_after: int
    reason: str


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    payload = json.dumps(messages, default=str, ensure_ascii=False)
    try:
        return max(1, int(get_token_count(payload)))
    except Exception:
        return max(1, len(payload) // 4)


def _empty_result(messages: list[dict[str, Any]], reason: str) -> CompressionResult:
    tokens = _estimate_tokens(messages)
    return CompressionResult(
        messages=messages,
        applied=False,
        tokens_before=tokens,
        tokens_after=tokens,
        reason=reason,
    )


def _replace_content(message: dict[str, Any], text: str) -> dict[str, Any]:
    return {**message, "content": text}


async def compress_messages(
    messages: list[dict[str, Any]],
    *,
    compressor: Compressor,
    target_ratio: float = _DEFAULT_TARGET_RATIO,
    min_ratio: float = _DEFAULT_MIN_RATIO,
    embed: Callable[[str], Awaitable[list[float] | None]] | None = None,
    min_similarity: float = _DEFAULT_MIN_SIMILARITY,
    shadow: bool = False,
) -> CompressionResult:
    """Compress eligible spans; return both candidate and original."""
    spans: list[CompressionSpan] = compressible_spans(messages)
    if not spans or not compressor.available:
        return _empty_result(messages, "no_compressor" if not compressor.available else "nothing")

    candidate = list(messages)
    changed = False
    for span in spans:
        try:
            if isinstance(compressor, LlMLinguaCompressor):
                # Dedicated worker: a timeout-abandoned call cannot occupy the
                # default executor that the rest of the app shares.
                compressed = await compressor.compress_async(span.text, ratio=target_ratio)
            else:
                compressed = await asyncio.to_thread(
                    compressor.compress, span.text, ratio=target_ratio
                )
        except Exception:
            continue
        if not isinstance(compressed, str) or not compressed.strip():
            continue
        if len(compressed) >= len(span.text):
            continue
        quality = await evaluate_compression(
            span.text,
            compressed,
            embed=embed if not shadow else None,
            min_similarity=min_similarity,
        )
        if not quality.allowed:
            continue
        candidate[span.message_index] = _replace_content(candidate[span.message_index], compressed)
        changed = True

    before = _estimate_tokens(messages)
    after = _estimate_tokens(candidate)
    if not changed or after >= before:
        return _empty_result(messages, "no_savings")
    ratio = after / before if before else 1.0
    if ratio < min_ratio:
        # Too aggressive: the guard missed a structure the caller cares about.
        return _empty_result(messages, "below_min_ratio")
    if ratio > 0.95:
        return _empty_result(messages, "insufficient_savings")
    return CompressionResult(
        messages=messages if shadow else candidate,
        applied=not shadow,
        tokens_before=before,
        tokens_after=after,
        reason="shadow" if shadow else "compressed",
    )


def _float(name: str, default: float) -> float:
    try:
        return float(config.get(name))
    except (TypeError, ValueError):
        return default


def compressor_revision() -> str:
    """Stable key component, including whether compression was active."""
    mode = effective_mode("compression")
    if mode is OptimizationMode.OFF:
        return "off"
    backend = str(config.get("optimization_compression_backend") or "none").lower()
    if backend == "none":
        return f"{mode}:none"
    return (
        f"{mode}:{backend}:{_float('optimization_compression_target_ratio', _DEFAULT_TARGET_RATIO)}"
    )


async def maybe_compress_request(
    messages: list[dict[str, Any]],
    tools: list[Any] | None,
    *,
    provider: Any,
    model: str | None,
    session_id: Any = None,
    agent_id: str = "",
    request_hash: str = "",
    task_class: str = "",
) -> CompressionResult:
    """Apply compression according to the configured mode.

    The returned ``messages`` are the ones the caller must send. In shadow
    mode they are always the originals; the candidate is recorded only.
    """
    mode = effective_mode("compression")
    if mode is OptimizationMode.OFF:
        return _empty_result(messages, "mode_off")
    if not breakers().allow("compression"):
        return _empty_result(messages, "circuit_open")

    before = _estimate_tokens(messages)
    context_window = context_window_for(model or getattr(provider, "model", None), provider)
    decision = evaluate_trigger(
        mode=mode,
        tokens_before=before,
        context_window=context_window,
        messages_present=bool(messages),
    )
    if not decision.allowed:
        return _empty_result(
            messages, str(decision.reasons[0]) if decision.reasons else "ineligible"
        )

    compressor = build_compressor()
    if not compressor.available:
        schedule_shadow(
            ShadowEvent(
                subsystem="compression",
                decision="skipped",
                reason_codes=("no_compressor",),
                mode=str(mode),
                session_id=session_id,
                agent_id=agent_id,
                provider=str(
                    getattr(provider, "spec", None).name if getattr(provider, "spec", None) else ""
                ),
                model=str(model or getattr(provider, "model", "")),
                task_class=task_class,
                request_hash=request_hash,
                tokens_before=before,
                tokens_after=before,
            )
        )
        return _empty_result(messages, "no_compressor")

    embed = None
    if mode is OptimizationMode.WIRED:
        embed = embed_query

    try:
        result = await asyncio.wait_for(
            compress_messages(
                messages,
                compressor=compressor,
                target_ratio=_float("optimization_compression_target_ratio", _DEFAULT_TARGET_RATIO),
                min_ratio=_float("optimization_compression_min_ratio", _DEFAULT_MIN_RATIO),
                embed=embed,
                min_similarity=_float(
                    "optimization_compression_min_embedding_similarity", _DEFAULT_MIN_SIMILARITY
                ),
                shadow=mode is OptimizationMode.SHADOW,
            ),
            timeout=max(
                0.01,
                _float("optimization_compression_max_latency_ms", 75.0) / 1000.0,
            ),
        )
    except TimeoutError:
        breakers().record_failure("compression")
        return _empty_result(messages, "timeout")
    except Exception:
        breakers().record_failure("compression")
        return _empty_result(messages, "compression_failed")

    if result.reason in {"compressed", "shadow"}:
        breakers().record_success("compression")
    schedule_shadow(
        ShadowEvent(
            subsystem="compression",
            decision="applied" if result.applied else "shadow",
            reason_codes=(result.reason,),
            mode=str(mode),
            session_id=session_id,
            agent_id=agent_id,
            provider=str(
                getattr(provider, "spec", None).name if getattr(provider, "spec", None) else ""
            ),
            model=str(model or getattr(provider, "model", "")),
            task_class=task_class,
            request_hash=request_hash,
            tokens_before=result.tokens_before,
            tokens_after=result.tokens_after,
        )
    )
    return result
