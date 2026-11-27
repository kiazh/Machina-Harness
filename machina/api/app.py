"""FastAPI application factory for the Machina HTTP API.

Exposes REST endpoints that reuse the same core services (session_manager,
context_manager, memory_store, job_store, agent_registry) and the Gateway
class that the terminal UI drives via JSON-RPC.

Structure (S4.1): routes live in per-resource APIRouter modules under
``machina.api.routes`` (sessions, memory, jobs, documents, approvals,
agents, rpc, health, plus shared models/helpers/streaming). ``create_app``
below only assembles them onto one ``FastAPI`` instance — lifespan and
middleware are preserved here.

Backwards-compatibility surface: several names are re-exported (or kept as
module attributes) because tests and callers address them as
``machina.api.app.<name>`` — ``db`` / ``Gateway`` / ``session_manager`` /
``context_manager`` (patch targets), ``StreamEvent``, ``PromptRequest`` and
the other request models, ``_serialize_event`` / ``_redact_value``,
``CLAIM_RENEW_INTERVAL_SECONDS``, and ``lifespan``.

The shared JSON-RPC gateway lives on ``app.state.gateway``; HTTP dispatch uses
the typed ``Gateway.call`` seam rather than synthesizing JSON-RPC frames.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib import import_module
from typing import Any

from fastapi import FastAPI

from machina import __version__
from machina.api.rate_limit import RateLimitMiddleware
from machina.api.routes import (
    a2a,
    agents,
    approvals,
    documents,
    health,
    jobs,
    memory,
    openapi,
    rpc,
    sessions,
)
from machina.api.routes.models import (
    CreateDocumentRequest,
    CreateJobRequest,
    CreateMemoryRequest,
    CreatePersonaMemoryRequest,
    CreateSessionRequest,
    PromptRequest,
    ResolveApprovalRequest,
    ResumeApprovalRequest,
    RpcRequest,
    UpdateSessionRequest,
)
from machina.api.routes.shared import _redact_value, _serialize_event
from machina.core.context import context_manager  # re-exported (patch target)
from machina.core.models import StreamEvent  # re-exported (test attribute)
from machina.core.session import session_manager  # re-exported (patch target)
from machina.db.connection import db
from machina.gateway.server import Gateway
from machina.observability.diagnostics import record_failure
from machina.observability.instrumentation import warm

logger = logging.getLogger(__name__)
CLAIM_RENEW_INTERVAL_SECONDS = 30.0

__all__ = [
    "CLAIM_RENEW_INTERVAL_SECONDS",
    "CreateDocumentRequest",
    "CreateJobRequest",
    "CreateMemoryRequest",
    "CreatePersonaMemoryRequest",
    "CreateSessionRequest",
    "PromptRequest",
    "ResolveApprovalRequest",
    "ResumeApprovalRequest",
    "RpcRequest",
    "StreamEvent",
    "UpdateSessionRequest",
    "_redact_value",
    "_serialize_event",
    "context_manager",
    "create_app",
    "db",
    "lifespan",
    "session_manager",
]


# ─── gateway helper ────────────────────────────────────────────────────────


def _discard_gateway_frame(frame: dict[str, Any]) -> None:
    """Discard gateway notifications on the HTTP transport.

    ``Gateway.call`` returns request results directly; streamed events use the
    SSE transport instead of gateway frames. The writer is still required by
    the Gateway constructor (shell job listeners and turn tasks may emit).
    """
    del frame


# ─── lifespan ──────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Single shared lifecycle: the runtime graph owns DB/audit/scheduler.

    Every initialized resource is cleaned up on ordinary *and* exceptional
    exits (MCH-005): the yielded lifetime is wrapped in ``try/finally`` with
    each stage independently guarded, so one failing (or cancelled) cleanup
    cannot skip the rest or replace the primary exception.
    """
    from machina.core.runtime import runtime_services
    from machina.plugins.loader import load_plugins

    load_plugins()
    runner = None
    warmers_task = None
    gateway = None
    primary_error: BaseException | None = None
    try:
        try:
            warm()
            report = await runtime_services.startup()
            if report.get("db") == "ready":
                from machina.core.scheduler import JobRunner

                runner = JobRunner()
                runner.start()
                config_module = import_module("machina.core.config")
                warmers_task = import_module(
                    "machina.core.optimization.warmers.startup"
                ).schedule_startup_warmers(
                    provider=config_module.config.get("provider"),
                    model=config_module.config.get("model"),
                )
            # One embedded gateway for all /rpc dispatches. It borrows the
            # application's runtime/DB (owns_db=False) and exposes the typed
            # Gateway.call seam; streamed events stay on the SSE transport.
            # W8: never inherit the stdio MACHINA_GATEWAY_TOKEN — the HTTP
            # boundary already enforces API-key auth.
            gateway = Gateway(
                _discard_gateway_frame,
                owns_db=False,
                inherit_env_token=False,
                local_only=False,
            )
            app.state.gateway = gateway
        except Exception as e:
            logger.warning("Runtime startup degraded: %s", e, exc_info=True)
        yield
    except BaseException as error:
        primary_error = error
        raise
    finally:
        # AH-AUDIT-024: bounded shutdown — no stage awaits cancellation-
        # resistant work indefinitely. Failures are reported, never raised
        # over the primary outcome.
        cleanup_cancelled: asyncio.CancelledError | None = None

        async def _cleanup_stage(label: str, cleanup: Any, timeout: float) -> None:
            nonlocal cleanup_cancelled
            try:
                await asyncio.wait_for(cleanup(), timeout=timeout)
            except asyncio.CancelledError as error:
                cleanup_cancelled = cleanup_cancelled or error
                logger.warning("API %s cleanup was cancelled; continuing", label)
            except Exception as error:
                logger.warning("API %s cleanup failed: %s", label, error, exc_info=True)
                record_failure(f"app.lifespan.{label}", error)

        if runner is not None:
            await _cleanup_stage("job_runner_stop", runner.stop, 10)
        if warmers_task is not None:
            warmers_task.cancel()

            async def _stop_warmers() -> None:
                try:
                    await warmers_task
                except (asyncio.CancelledError, Exception):
                    pass

            await _cleanup_stage("optimization_warmers_stop", _stop_warmers, 2)
        try:
            shadow_module = import_module("machina.core.optimization.shadow")
            await _cleanup_stage("optimization_shadow_drain", shadow_module.drain_shadow_tasks, 2)
        except Exception:
            pass
        if gateway is not None:
            await _cleanup_stage("gateway_close", gateway.close, 10)
        await _cleanup_stage("runtime_shutdown", runtime_services.shutdown, 15)
        if cleanup_cancelled is not None and primary_error is None:
            raise cleanup_cancelled


# ─── app factory ───────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Create and configure the FastAPI application (thin assembly)."""
    # INFRA-M4 (infra audit): the .env.example placeholder is a publicly
    # known value — refuse to start behind it (fail closed). An UNSET key
    # stays the per-request 503 contract; only the literal placeholder is
    # rejected, so dev/test environments without a key are unaffected.
    from machina.api.auth import configured_key

    if configured_key() == "replace-with-a-long-random-key":
        raise RuntimeError(
            "MACHINA_API_KEY is still the .env.example placeholder; set a real "
            "random key (e.g. `machina setup` or openssl rand -hex 32)."
        )
    app = FastAPI(
        title="Machina API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def _openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi

        app.openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version="3.2.1",
            routes=app.routes,
        )
        return app.openapi_schema

    app.openapi = _openapi  # type: ignore[method-assign]
    app.add_middleware(RateLimitMiddleware)
    # Mount each resource router's routes directly (instead of
    # ``include_router``): the installed FastAPI resolves included routers
    # lazily, leaving placeholder objects in ``app.routes``. The test-suite
    # and operators introspect ``app.routes`` for real ``APIRoute`` entries
    # (path/methods/endpoint), so mount the already-built routes verbatim.
    # Registration order groups by resource; no two same-method routes
    # overlap, so matching is identical to the pre-split inline order.
    for resource in (
        health,
        sessions,
        memory,
        jobs,
        documents,
        approvals,
        agents,
        rpc,
        openapi,
        a2a,
    ):
        app.router.routes.extend(resource.router.routes)
    _mark_changed = getattr(app.router, "_mark_routes_changed", None)
    if callable(_mark_changed):
        _mark_changed()
    return app
