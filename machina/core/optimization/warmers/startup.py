"""Local, free startup warmers.

Warmers are one-shot, bounded, failure-isolated, and never make synthetic
provider calls. Paid prefix warming lives in :mod:`prefix` and stays off by
default.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from machina.core.assembler import get_token_count
from machina.core.config import config
from machina.core.metrics import metrics
from machina.core.optimization.canonicalizer import tool_schema_hash
from machina.core.optimization.capabilities import resolve_capabilities
from machina.memory.embeddings import embed_query

__all__ = ["WarmerReport", "run_startup_warmers", "schedule_startup_warmers"]

logger = logging.getLogger(__name__)


@dataclass
class WarmerReport:
    ran: bool = False
    skipped: str = ""
    durations_ms: dict[str, float] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "skipped": self.skipped,
            "durationsMs": dict(self.durations_ms),
            "failures": dict(self.failures),
        }


async def _warm_tokenizer() -> None:
    await asyncio.to_thread(_warm_tokenizer_sync)


def _warm_tokenizer_sync() -> None:
    get_token_count("machina warmup")


async def _warm_capabilities(provider: Any, model: str | None) -> None:
    resolve_capabilities(provider, model)


async def _warm_embedding() -> None:
    # Never warm a paid API backend from a startup hook. Only local ONNX and
    # model2vec runtimes are eligible; an explicit/local-server backend is
    # left lazy because its cost/ownership is not knowable here.
    settings = importlib.import_module("machina.rag.embedder").resolve_embedding_settings()
    if settings.kind not in ("local", "onnx"):
        return
    await embed_query("machina warmup")


async def _warm_prompt_hash() -> None:
    tool_schema_hash([])


_WARMERS: tuple[tuple[str, Callable[[], Awaitable[None]]], ...] = (
    ("tokenizer", _warm_tokenizer),
    ("prompt_hash", _warm_prompt_hash),
    ("embedding", _warm_embedding),
)


def _enabled() -> bool:
    try:
        if bool(config.get("optimization_emergency_disabled")):
            return False
        return bool(config.get("optimization_warmers_startup_enabled"))
    except Exception:
        return False


async def run_startup_warmers(
    *,
    provider: Any | None = None,
    model: str | None = None,
    timeout_seconds: float = 5.0,
) -> WarmerReport:
    """Run the free warmers once. Never raises for a warmer failure."""
    report = WarmerReport()
    if not _enabled():
        report.skipped = "disabled"
        return report
    if os.environ.get("MACHINA_GATEWAY_NO_SCHEDULER"):
        # The test harness and one-shot callers set this flag. Startup work
        # must not add latency or background tasks to those paths.
        report.skipped = "no_background_scheduler"
        return report

    report.ran = True
    jobs: list[tuple[str, Callable[[], Awaitable[None]]]] = list(_WARMERS)
    if provider is not None:
        jobs.append(
            (
                "capabilities",
                lambda: _warm_capabilities(provider, model),
            )
        )
    for name, job in jobs:
        started = time.monotonic()
        try:
            await asyncio.wait_for(job(), timeout=max(0.1, timeout_seconds))
        except Exception as exc:
            report.failures[name] = type(exc).__name__
            logger.debug("startup warmer %s failed (%s)", name, type(exc).__name__)
        finally:
            report.durations_ms[name] = round((time.monotonic() - started) * 1000, 1)
    return report


def schedule_startup_warmers(
    *,
    provider: Any | None = None,
    model: str | None = None,
) -> asyncio.Task[WarmerReport] | None:
    """Schedule warmers on the running loop; return None when skipped."""
    if not _enabled() or os.environ.get("MACHINA_GATEWAY_NO_SCHEDULER"):
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(run_startup_warmers(provider=provider, model=model))
    task.add_done_callback(_log_warmer_report)
    return task


def _log_warmer_report(task: asyncio.Task[WarmerReport]) -> None:
    """Surface the result instead of discarding a completed warmer task."""
    try:
        report = task.result()
    except (asyncio.CancelledError, Exception):
        return
    try:
        metrics.increment_counter("optimization.warmers.ran")
        for name in report.failures:
            metrics.increment_counter("optimization.warmers.failure")
            metrics.increment_counter(f"optimization.warmers.failure.{name}")
    except Exception:
        pass
    if report.failures:
        logger.warning("optimization warmers completed with failures: %s", report.to_dict())
    else:
        logger.info("optimization warmers completed: %s", report.to_dict())
