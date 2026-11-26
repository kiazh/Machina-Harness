"""JSON-RPC gateway between the terminal UI and the agent core.

The UI (``ui/``) spawns ``python -m machina.gateway`` and exchanges one JSON object
per line over stdin/stdout (JSON-RPC 2.0). Requests get a ``result`` or
``error`` response; streamed output is pushed as notifications of the form
``{"jsonrpc": "2.0", "method": "event", "params": {"type": ..., ...}}``.

The full method and event list is in ``communications/ui-gateway-protocol.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from typing import Any, Protocol

from machina import __version__
from machina.core.config import config
from machina.core.execution_context import begin_turn_scope, end_turn_scope
from machina.core.models import Session
from machina.core.optimization.warmers.startup import schedule_startup_warmers
from machina.core.provider import PROVIDERS
from machina.core.runtime import RuntimeServices, runtime_services
from machina.core.session import session_manager
from machina.core.session_service import (
    SessionCreateError,
    SessionCreateSpec,
    session_service,
)
from machina.core.turn_engine import (
    MAX_TOOL_RESULT_CHARS as MAX_TOOL_RESULT_CHARS,
)
from machina.core.turn_lifecycle import ApprovalPolicy, TurnLifecycle
from machina.db.connection import db
from machina.gateway import features
from machina.gateway.errors import (  # noqa: F401 — re-exported for clients and tests
    DATABASE_UNAVAILABLE,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    NOT_FOUND,
    OPERATION_FAILED,
    PARSE_ERROR,
    SESSION_NOT_FOUND,
    TURN_IN_PROGRESS,
    UNAUTHORIZED,
    RpcError,
)
from machina.gateway.features._common import _bool
from machina.gateway.features.mode import sync_execution_mode_env
from machina.gateway.serializers import history_from_chunks, session_to_dict
from machina.observability.diagnostics import record_failure
from machina.observability.instrumentation import instrument, warm
from machina.observability.tracing import span

__all__ = [
    "Gateway",
    "RpcError",
    "history_from_chunks",
    "session_to_dict",
]

logger = logging.getLogger(__name__)
CLAIM_RENEW_INTERVAL_SECONDS = 30.0
CANCEL_JOIN_TIMEOUT_SECONDS = 0.1
# W5: bound session.resume history like the REST session endpoint
# (machina/api/routes/sessions.py reads context chunks with limit=200).
# RF4-W5: session.resume must not serialize an unbounded transcript, but the
# bound must not re-introduce the historical "resume truncated at 200" bug
# (pinned deliberately by test_resume_reads_beyond_two_hundred_chunks).
# 1000 keeps the payload bounded while preserving that contract; the DB-side
# fetch still full-scan (residual noted by FM1 for the context.py owner).
MAX_RESUME_HISTORY_CHUNKS = 1000
#: Harness chat turns run research-style tasks (web search, many tool calls),
#: so they get a longer bound than a delegated one-shot run.
HARNESS_CHAT_TIMEOUT_SECONDS = 600.0
# W4: initialize may carry these keys without a token — the bare handshake.
# Any other key (model/provider/mode today, future state mutations) requires
# the auth token when one is configured.
_BARE_INITIALIZE_KEYS = frozenset({"protocolVersion", "token"})


# ---------------------------------------------------------------------------
# Gateway logging — file + console
# ---------------------------------------------------------------------------
def _setup_gateway_logging() -> None:
    """Configure gateway logging to file and console."""
    home = os.environ.get("MACHINA_HOME", "").strip()
    if not home:
        try:
            home = str(config.get("machina_home") or "").strip()
        except Exception:
            home = ""
    root = os.path.expanduser(home) if home else os.path.join(os.path.expanduser("~"), ".machina")
    log_dir = os.path.join(root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "gateway.log")

    # File handler — rotating
    from logging.handlers import RotatingFileHandler

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    file_handler.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    console_handler.setLevel(logging.WARNING)

    # Root logger for machina.gateway
    gw_logger = logging.getLogger("machina.gateway")
    gw_logger.setLevel(logging.DEBUG)
    gw_logger.addHandler(file_handler)
    gw_logger.addHandler(console_handler)

    # Also capture machina.core logs
    core_logger = logging.getLogger("machina.core")
    core_logger.setLevel(logging.DEBUG)
    core_logger.addHandler(file_handler)

    # Workspace snapshots/reviews log at DEBUG to the same file.
    workspace_logger = logging.getLogger("machina.workspace")
    workspace_logger.setLevel(logging.DEBUG)
    workspace_logger.addHandler(file_handler)

    logger.info("Gateway logging initialized: %s", log_file)
    # Pay instrumentation's one-time import/regex cost now, never mid-turn.
    warm()


_setup_gateway_logging()

# PROVIDERS is single-sourced from machina.core.provider (imported above) so the
# gateway, HTTP API and UI always agree on valid provider names.
# MAX_TOOL_RESULT_CHARS is re-exported from machina.core.turn_engine (imported
# above) so the gateway and the shared turn engine share one tool-result bound.

Writer = Callable[[dict[str, Any]], None]


class StreamingAgent(Protocol):
    def run_stream(self, session_id: uuid.UUID, user_message: str, verbose: bool = ...) -> Any: ...


def _git_branch() -> str:
    """Current branch name read directly from ``.git/HEAD``.

    This runs during every gateway handshake. Spawning ``git`` for it cost
    roughly 450 ms on Windows, so read the tiny ref file instead and walk up
    to the repository root. Worktrees use a ``.git`` file containing
    ``gitdir: ...``; both layouts are handled without a subprocess.
    """
    from pathlib import Path

    try:
        directory = Path.cwd()
    except OSError:
        return ""
    for candidate in (directory, *directory.parents):
        dotgit = candidate / ".git"
        try:
            if dotgit.is_file():
                marker = dotgit.read_text(encoding="utf-8", errors="replace").strip()
                if not marker.startswith("gitdir:"):
                    continue
                git_dir = (candidate / marker.partition(":")[2].strip()).resolve()
            elif dotgit.is_dir():
                git_dir = dotgit
            else:
                continue
            head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if not head:
            return ""
        # Detached HEAD: git rev-parse --abbrev-ref HEAD prints "HEAD".
        if not head.startswith("ref:"):
            return "HEAD"
        ref = head.partition(":")[2].strip()
        prefix = "refs/heads/"
        return ref[len(prefix) :] if ref.startswith(prefix) else ref.rsplit("/", 1)[-1]
    return ""


def _default_agent_factory(model: str, provider: str) -> StreamingAgent:
    # Sentinel identity only: Gateway._run_turn branches on
    # ``self._agent_factory is _default_agent_factory`` and builds through
    # build_agent_for_session; this body is never invoked by the gateway.
    # The lazy imports below remain for any direct/legacy caller.
    from machina.core.agent import ReActAgent
    from machina.core.provider import get_provider

    llm = get_provider(provider=provider, model=model)
    agent = ReActAgent(
        provider=llm,
        agent_id=config.get("agent_id"),
    )
    agent._owns_provider = True  # type: ignore[attr-defined]
    return agent


class Gateway:
    """Dispatches JSON-RPC requests and streams agent turns as events."""

    def __init__(
        self,
        write: Writer,
        agent_factory: Callable[[str, str], StreamingAgent] = _default_agent_factory,
        owns_db: bool = True,
        *,
        runtime: RuntimeServices | None = None,
        inherit_env_token: bool = True,
    ) -> None:
        self._write = write
        self.protocol_version = 1
        self._agent_factory = agent_factory
        self._owns_db = owns_db
        # Stdio owns process cleanup; HTTP adapters borrow the app runtime.
        self.runtime = runtime if runtime is not None else runtime_services
        from machina.core.provider.aliases import canonical_provider

        self.model: str = config.get("model")
        self.provider: str = canonical_provider(config.get("provider"))
        # Round-3 review finding 10: the last session the client created or
        # resumed. Model commands without explicit session identity resolve
        # that session's pinned route before falling back to the gateway
        # defaults — a resumed session can pin another provider/model.
        self._opened_session_id: uuid.UUID | None = None
        self.closing = False
        self._db_ready = False
        # An explicit public readiness write opts an embedded gateway out of
        # following the shared pool; callers that own no lifecycle use this.
        self._db_ready_explicit = False
        self._job_runner = None
        self._warmers_task = None
        self._kiro_task = None
        self._hermes_task = None
        self._connector_autoconnect_task = None
        self._turns: dict[str, asyncio.Task[None]] = {}
        from machina.gateway.operations import Operations

        self.operations = Operations(self)
        self._steering: dict[str, Any] = {}
        from machina.core.shell_jobs import shell_jobs

        if shell_jobs.closing:
            shell_jobs.reopen()
        self._shell_listener = lambda job: self._write(
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": self.event_payload(
                    "shell.job", str(job.session_id), job.id, job=job.snapshot()
                ),
            }
        )
        shell_jobs.listeners.add(self._shell_listener)
        from machina.core import harness_jobs as harness_job_store

        self._harness_listener = lambda job: self._write(
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": self.event_payload(
                    "harness.job", str(job.session_id), job.id, job=job.snapshot()
                ),
            }
        )
        harness_job_store.listeners.add(self._harness_listener)
        self._turn_tokens: dict[str, str] = {}
        self._turn_ownership_lost: dict[str, asyncio.Event] = {}
        self._turn_progress: dict[str, dict[str, int]] = {}
        # AH-AUDIT-028: turn ids that already emitted their single terminal
        # completion (quarantine fallback vs late genuine completion races).
        self._turn_finished: set[str] = set()
        self._cancel_requested: set[str] = set()
        # AH-AUDIT-029: accumulated genuine human-approval wait per turn.
        # Compute accounting pauses during these waits; approval timeout
        # stays separately bounded by the broker handler.
        self._approval_wait_total: dict[str, float] = {}
        self._approval_wait_started: dict[str, float] = {}
        # H-07: Sanitized progress stage tracker for hang diagnosis.
        # Records the current stage for each turn without logging sensitive data.
        self._turn_stages: dict[str, dict[str, Any]] = {}
        # Pending human approvals by request_id (Phase E). The broker's
        # approval handler emits permission.required and awaits the future;
        # approvals.resolve completes it. Reconnect-safe via durable DB rows.
        # Entries are (future, turn_id, session_id): turn cleanup cancels
        # ONLY its own turn's waits, never another session's (addendum check).
        self._pending_approvals: dict[str, tuple[asyncio.Future[str], str, str]] = {}
        self._config_lock = asyncio.Lock()
        # Token-based auth for the stdio channel. Local UIs set
        # MACHINA_GATEWAY_TOKEN; None means "open" (stdio local use) with a warning.
        # W8: an embedded gateway (HTTP /api/v1/rpc, already behind API-key
        # auth) sets inherit_env_token=False so the stdio token never reaches
        # it — explicit signal instead of mutating _auth_token from outside.
        self._inherit_env_token = inherit_env_token
        # Bounded transport label for instrumentation only (never a credential).
        self._transport_label = "stdio" if inherit_env_token else "http"
        self._auth_token: str | None = (
            os.environ.get("MACHINA_GATEWAY_TOKEN") if inherit_env_token else None
        )
        if self._auth_token is None and inherit_env_token:
            logger.warning(
                "MACHINA_GATEWAY_TOKEN not set — gateway accepts unauthenticated requests"
            )
        self._methods: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {
            "initialize": self._initialize,
            "session.create": self._session_create,
            "session.list": self._session_list,
            "session.family": self._session_family,
            "session.resume": self._session_resume,
            "usage.get": self._usage_get,
            "prompt.submit": self._prompt_submit,
            "prompt.cancel": self._prompt_cancel,
            "prompt.steer": self._prompt_steer,
            "prompt.steering": self._prompt_steering,
            "question.answer": self._question_answer,
            "shell.jobs": self._shell_jobs,
            "config.set": self._config_set,
            "shutdown": self._shutdown,
        }
        features.register(self)

    def event_payload(
        self, event_type: str, session_id: str, turn_id: str, **fields: Any
    ) -> dict[str, Any]:
        from machina.protocol import gateway_event

        return gateway_event(event_type, session_id, turn_id, self.protocol_version, **fields)

    def add_method(
        self, name: str, handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    ) -> None:
        """Register an extra JSON-RPC method (used by ``machina.gateway.features``)."""
        if name in self._methods:
            logger.debug("method already registered, overwriting: %s", name)
        self._methods[name] = handler

    def turn_running(self, session_id: uuid.UUID) -> bool:
        task = self._turns.get(str(session_id))
        return (task is not None and not task.done()) or self.operations.session_running(session_id)

    def _record_stage(self, turn_id: str, stage: str, **details: Any) -> None:
        """Record a progress stage for hang diagnosis (H-07).

        Sanitized: no raw keys, prompts, tool payloads, or credentials.
        Records: stage name, timestamp, elapsed time, and safe metadata.
        """
        now = time.monotonic()
        existing = self._turn_stages.get(turn_id)
        if existing:
            existing["elapsed_ms"] = int((now - existing["started_at"]) * 1000)
        self._turn_stages[turn_id] = {
            "stage": stage,
            "started_at": now,
            "elapsed_ms": 0,
            "details": {k: v for k, v in details.items() if k in ("session_id", "turn_id")},
        }
        logger.debug("Turn %s stage: %s", turn_id, stage)

    def cancel_turn_approvals(self, turn_id: str) -> int:
        """Cancel pending approval waits for ONE turn (LP additional check).

        A turn finishing in session A never cancels session B's approval:
        entries are partitioned by turn id.
        """
        n = 0
        for _rid, _entry in list(self._pending_approvals.items()):
            _fut, _tid, _sid = _entry
            if _tid == turn_id and not _fut.done():
                _fut.cancel()
                n += 1
        return n

    # ─── dispatch ──────────────────────────────────────────────────────────
    async def handle_line(self, line: str) -> None:
        """Parse and answer one inbound JSON-RPC frame."""
        try:
            message = json.loads(line)
        except (ValueError, RecursionError) as e:
            # JSONDecodeError is only one ValueError subclass; huge integer
            # literals and deeply nested frames must be parse errors too, not
            # exceptions that unwind the gateway loop.
            logger.warning("Parse error: %s", e)
            self._send_error(None, PARSE_ERROR, f"parse error: {e}")
            return
        await self.handle_message(message)

    async def handle_message(self, message: Any) -> None:
        """Answer one already-parsed JSON-RPC frame."""
        rid = message.get("id") if isinstance(message, dict) else None
        if (
            not isinstance(message, dict)
            or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str)
        ):
            logger.warning("Invalid gateway request")
            self._send_error(rid, INVALID_REQUEST, "invalid request")
            return

        params = message.get("params") or {}
        if not isinstance(params, dict):
            logger.warning("Invalid params (not an object) for method %s", message.get("method"))
            self._send_error(rid, INVALID_PARAMS, "params must be an object")
            return

        method = message["method"]
        logger.debug("→ %s (id=%s)", method, rid)
        try:
            result = await self._dispatch(method, params)
        except RpcError as e:
            logger.warning("← %s error %d (id=%s)", method, e.code, rid)
            self._send_error(rid, e.code, e.message)
            return
        except Exception:  # defensive: _dispatch converts handler failures
            logger.exception("Gateway method %s failed", method)
            self._send_error(rid, INTERNAL_ERROR, "internal error")
            return

        logger.debug("← %s ok (id=%s)", method, rid)
        if rid is not None:
            self._write({"jsonrpc": "2.0", "id": rid, "result": result})

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Dispatch one method and return its real result.

        This is the typed seam for in-process transports (the HTTP API) that
        need the handler's result and typed ``RpcError`` failures without
        synthesizing a JSON-RPC frame or capturing a response write.

        The stdio transport keeps using :meth:`handle_message`; both paths
        share :meth:`_dispatch` so auth, method lookup, instrumentation, and
        error conversion cannot drift.
        """
        if not isinstance(method, str) or not method:
            raise RpcError(INVALID_REQUEST, "invalid request")
        call_params = {} if params is None else params
        if not isinstance(call_params, dict):
            raise RpcError(INVALID_PARAMS, "params must be an object")
        # An embedded gateway borrows the application's pool; refresh from the
        # live connection instead of trusting a construction-time snapshot.
        self.refresh_db_readiness()
        return await self._dispatch(method, call_params)

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Run one method, raising typed ``RpcError`` failures."""
        # Token-based authentication: reject all methods except initialize
        # if the gateway has a token set and the caller hasn't authenticated.
        if self._auth_token is not None and method != "initialize":
            if not self._token_ok(params.get("token")):
                logger.warning("Unauthorized attempt: method=%s", method)
                raise RpcError(UNAUTHORIZED, "unauthorized")

        handler = self._methods.get(method)
        if handler is None:
            logger.warning("Unknown method: %s", method)
            raise RpcError(METHOD_NOT_FOUND, f"unknown method: {method}")

        raw_session_id = params.get("session_id") or params.get("sessionId")
        session_id = raw_session_id if isinstance(raw_session_id, str) else None
        try:
            with instrument(
                "gateway",
                "rpc",
                method=method,
                transport=self._transport_label,
                session_id=session_id,
            ):
                return await handler(params)
        except RpcError:
            raise
        except Exception as exc:  # never let a handler crash the gateway
            logger.exception("Gateway method %s failed", method)
            # Sanitized client text: handler payloads stay in the server log.
            raise RpcError(INTERNAL_ERROR, "internal error") from exc

    async def close(self, timeout: float = 10.0) -> None:
        """Cancel running turns, stop the job runner, release the DB pool.

        AH-AUDIT-024: bounded joining with a hard outer deadline.
        Requested cancellation is distinguished from confirmed termination:
        turns that suppress cancellation are quarantined (logged, left for
        process shutdown) instead of holding close() forever. Ownership
        safety is preserved — quarantined tasks keep their tokens until
        expiry rather than being force-released to a newer owner.
        """
        from machina.core.runtime import _bounded_join

        await self.operations.close(timeout=timeout)
        if self._warmers_task is not None:
            self._warmers_task.cancel()
            try:
                await self._warmers_task
            except (asyncio.CancelledError, Exception):
                pass
            self._warmers_task = None
        if self._kiro_task is not None:
            self._kiro_task.cancel()
            try:
                await self._kiro_task
            except (asyncio.CancelledError, Exception):
                pass
            self._kiro_task = None
        if self._hermes_task is not None:
            self._hermes_task.cancel()
            try:
                await self._hermes_task
            except (asyncio.CancelledError, Exception):
                pass
            self._hermes_task = None
        if self._connector_autoconnect_task is not None:
            self._connector_autoconnect_task.cancel()
            try:
                await self._connector_autoconnect_task
            except (asyncio.CancelledError, Exception):
                pass
            self._connector_autoconnect_task = None
        from machina.core.optimization.shadow import drain_shadow_tasks

        await drain_shadow_tasks()
        if self._job_runner is not None:
            try:
                await asyncio.wait_for(self._job_runner.stop(), timeout=timeout)
            except asyncio.CancelledError:
                # Shutdown cancellation of close() itself must propagate; only
                # the job runner's own bounded failures are absorbed below.
                raise
            except (TimeoutError, Exception):
                logger.warning("gateway job runner stop exceeded its bound; continuing")
            self._job_runner = None
        from machina.core.shell_jobs import shell_jobs

        shell_jobs.listeners.discard(self._shell_listener)
        from machina.core import harness_jobs as harness_job_store

        harness_job_store.listeners.discard(self._harness_listener)
        shell_jobs.closing = True
        tasks = [t for t in self._turns.values() if not t.done()]
        if tasks:
            leftovers = await _bounded_join(tasks, timeout=timeout, label="gateway turns")
            for t in leftovers:
                logger.warning("gateway turn task quarantined on close: %s", t.get_name())
        await shell_jobs.close()
        # Retired workspace LSP managers keep stopping in the background after
        # a workspace reopen; join them bounded (never cancelled) before the
        # loop closes so their process teardown is observed.
        try:
            from machina.gateway.features.workspace import (
                drain_retired_lsp_managers,
                retire_active_lsp_manager,
            )

            await retire_active_lsp_manager(self)
            await drain_retired_lsp_managers(self, timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as _boundary_error:
            record_failure("server.close", _boundary_error)
        # An in-flight ChatGPT login owns a loopback listener and a background
        # task; cancel and join it here instead of leaving it for process
        # shutdown to kill without observing the socket teardown.
        try:
            from machina.gateway.features.auth_chatgpt import retire_login_flows

            await retire_login_flows(timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as _boundary_error:
            record_failure("server.close", _boundary_error)
        if self._owns_db:
            try:
                await self.runtime.shutdown_connectors(timeout=timeout)
            except asyncio.CancelledError:
                # Shutdown cancellation of close() itself must propagate.
                raise
            except Exception as _boundary_error:
                record_failure("server.close", _boundary_error)
        if self._db_ready and self._owns_db:
            # Agent background work (consolidation/learning reviews) must
            # finish before the pool closes; bounded, never raises.
            try:
                from machina.core.background_tasks import drain_background_tasks

                await drain_background_tasks()
            except asyncio.CancelledError:
                raise
            except (TimeoutError, Exception) as _boundary_error:
                record_failure("server.close", _boundary_error)
            from machina.observability.audit import audit_persistence

            try:
                await asyncio.wait_for(audit_persistence.stop(), timeout=5)
            except asyncio.CancelledError:
                raise
            except (TimeoutError, Exception) as _boundary_error:
                # Optional fallback preserves the primary outcome; report no payload.
                record_failure("server.close", _boundary_error)
            try:
                await asyncio.wait_for(db.close(), timeout=5)
            except asyncio.CancelledError:
                raise
            except (TimeoutError, Exception) as _boundary_error:
                # Optional fallback preserves the primary outcome; report no payload.
                record_failure("server.close", _boundary_error)
        self._db_ready = False
        self._db_ready_explicit = False

    def _start_job_runner(self) -> None:
        """Run scheduled jobs in the background while the gateway is up.

        Disabled with MACHINA_GATEWAY_NO_SCHEDULER=1 (tests drive the runner directly).
        """
        if os.environ.get("MACHINA_GATEWAY_NO_SCHEDULER") or self._job_runner is not None:
            return
        from machina.core.scheduler import JobRunner

        self._job_runner = JobRunner()
        self._job_runner.start()

    def _send_error(self, rid: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

    def _token_ok(self, token: Any) -> bool:
        """Constant-time token check; malformed tokens fail closed.

        RF4-W3: compare_digest raises TypeError on non-ASCII str operands; a
        malformed token must never crash the stdio loop. True when no token is
        configured (open local mode).
        """
        if self._auth_token is None:
            return True
        try:
            return isinstance(token, str) and secrets.compare_digest(token, self._auth_token)
        except TypeError:
            return False

    # ─── helpers ─────────────────────────────────────────────────────────────
    @property
    def db_ready(self) -> bool:
        """Whether database-backed methods may run (public readiness accessor)."""
        return self._db_ready

    @db_ready.setter
    def db_ready(self, ready: bool) -> None:
        self._db_ready = bool(ready)
        self._db_ready_explicit = True

    def refresh_db_readiness(self) -> bool:
        """Refresh borrowed-DB readiness from the shared connection pool.

        A gateway constructed with ``owns_db=False`` borrows the application's
        pool. Its readiness must follow the live connection rather than a
        construction-time snapshot. Owned gateways become ready through
        ``initialize`` only, and an explicit public readiness write is left
        untouched.
        """
        if not self._owns_db and not self._db_ready_explicit:
            self._db_ready = db.connected
        return self._db_ready

    def require_db(self) -> None:
        if not self.db_ready:
            raise RpcError(DATABASE_UNAVAILABLE, "database not connected; call initialize first")

    async def get_session(self, params: dict[str, Any]) -> Session:
        sid = _parse_session_id(params)
        session = await session_manager.get(sid)
        if session is None:
            raise RpcError(SESSION_NOT_FOUND, f"session {sid} not found")
        # Older sessions may pin the user-added hermes-proxy alias. Present
        # them as the built-in route so the next turn and /models agree.
        from machina.core.provider.aliases import canonical_provider

        session.provider = canonical_provider(session.provider)
        return session

    def opened_session_id(self) -> uuid.UUID | None:
        """Session whose pinned route model commands default to (round-3 F10).

        Set by ``session.create`` / ``session.resume``; when the session no
        longer resolves the callers fall back to the gateway defaults.
        """
        return self._opened_session_id

    def _set_provider(self, value: Any) -> None:
        if value not in PROVIDERS:
            raise RpcError(INVALID_PARAMS, f"provider must be one of: {', '.join(PROVIDERS)}")
        self.provider = value

    def _set_model(self, value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            raise RpcError(INVALID_PARAMS, "model must be a non-empty string")
        self.model = value.strip()

    # ─── methods ────────────────────────────────────────────────────────────
    async def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        version = params.get("protocolVersion", 1)
        if type(version) is not int or version not in (1, 2):
            raise RpcError(INVALID_PARAMS, "protocolVersion must be 1|2")
        # Pick up a token set after construction (e.g. tests / late env). An
        # embedded gateway opted out of env inheritance (W8) and never does.
        if self._auth_token is None and self._inherit_env_token:
            env_token = os.environ.get("MACHINA_GATEWAY_TOKEN")
            if env_token:
                self._auth_token = env_token
        # W4: the bare handshake stays token-free so a fresh client can
        # authenticate, but ANY state-changing parameter (model/provider/mode
        # today, future keys) requires the token once one is configured. The
        # check runs before protocol_version/plugins/config/DB are touched.
        if set(params) - _BARE_INITIALIZE_KEYS and not self._token_ok(params.get("token")):
            logger.warning("Unauthorized state-changing initialize rejected")
            raise RpcError(UNAUTHORIZED, "unauthorized")
        self.protocol_version = version
        logger.info("Gateway initialize: model=%s provider=%s", self.model, self.provider)
        from machina.plugins.loader import load_plugins

        load_plugins()
        async with self._config_lock:
            if params.get("model"):
                self._set_model(params["model"])
            if params.get("provider"):
                self._set_provider(params["provider"])
            # LP-13: CLI --mode reaches the gateway (previously dropped at the
            # Node boundary). Applied to this gateway process only
            # (non-persistent); new sessions auto-grant under a full default.
            if params.get("mode"):
                mode = str(params["mode"]).strip().lower()
                if mode not in ("ask", "workspace", "sandbox", "full"):
                    raise RpcError(INVALID_PARAMS, "mode must be ask|workspace|sandbox|full")
                config.set("execution_mode", mode)
                # F1: Config.get prefers MACHINA_EXECUTION_MODE, so without
                # the process-env sync a launcher --mode was silently ignored
                # under a pre-existing .env shadow. Process-scoped only here
                # (never persisted): --mode applies to this gateway process.
                sync_execution_mode_env(mode, persist=False)
        if not self._db_ready:
            # Own only the pool this call opened: an embedded gateway borrows
            # the app's pool and must never close it on a failed handshake.
            opened_here = not db.connected
            try:
                await db.connect()
            except Exception as e:
                reference = uuid.uuid4().hex[:12]
                logger.error(
                    "Database connection failed (reference=%s): %s",
                    reference,
                    e,
                    exc_info=True,
                )
                await self._release_failed_db(opened_here)
                # Sanitized client text: driver/DNS/user details stay in the
                # server log; the reference lets an operator correlate them.
                raise RpcError(
                    DATABASE_UNAVAILABLE, f"database unavailable (reference: {reference})"
                ) from e
            self._db_ready = True
            try:
                await db.initialize_runtime_schema()
            except Exception as error:
                self._db_ready = False
                reference = uuid.uuid4().hex[:12]
                logger.error(
                    "Runtime schema upgrade failed (reference=%s): %s",
                    reference,
                    error,
                    exc_info=True,
                )
                await self._release_failed_db(opened_here)
                raise RpcError(
                    DATABASE_UNAVAILABLE,
                    f"runtime schema upgrade failed (reference: {reference})",
                ) from error
            from machina.core.usage import usage_store

            # W7: startup cleanup must not reap another worker's in-flight
            # reservations. Pass the documented 10-minute grace
            # (UsageStore.cleanup_orphaned_reservations docstring): only rows
            # older than an entire turn can be orphans.
            await usage_store.cleanup_orphaned_reservations(older_than_minutes=10)
            from machina.observability.audit import audit_persistence

            audit_persistence.start()
            self._start_job_runner()
            # Free local warmers run once, off the request path. The helper
            # skips itself when disabled or when tests disable the scheduler;
            # it never makes a synthetic provider call.
            self._warmers_task = schedule_startup_warmers(provider=self.provider, model=self.model)
            # Optional Kiro local-proxy startup. This only creates a task: the
            # TCP probe, login detection, and child spawn all run in a worker
            # thread and never delay initialize/`/models`.
            from machina.core.provider.kiro_gateway import schedule_autostart

            self._kiro_task = schedule_autostart()
            # Detect the installed Hermes CLI and start its OpenAI proxy when
            # an upstream OAuth login exists. Same contract: a background
            # task, never on the initialize/`/models` path, and only a proxy
            # Machina started is stopped.
            from machina.core import hermes_proxy

            self._hermes_task = hermes_proxy.schedule_autostart()
            # Reconnect already-authorized HTTP MCP servers without blocking
            # initialize or the first turn. This is best-effort: missing or
            # expired authorization stays on the explicit /mcp path.
            if self._connector_autoconnect_task is None or self._connector_autoconnect_task.done():
                self._connector_autoconnect_task = asyncio.create_task(
                    self._auto_connect_connectors(), name="gateway-connector-autoconnect"
                )
        payload = {
            "version": __version__,
            "protocolVersion": self.protocol_version,
            "model": self.model,
            "provider": self.provider,
            # Redacted to the basename: the full working-directory path can
            # leak usernames / machine layout to unauthenticated callers.
            "cwd": os.path.basename(os.getcwd()),
            "branch": _git_branch(),
        }
        # The absolute launch directory is only disclosed to a caller that
        # presented the valid auth token (the local UI always does). The
        # workspace feature needs a real absolute root; unauthenticated or
        # token-less handshakes keep the redacted basename only.
        if self._auth_token is not None and self._token_ok(params.get("token")):
            payload["cwdFull"] = os.getcwd()
        return payload

    async def _release_failed_db(self, opened_here: bool) -> None:
        """Close a pool this initialize attempt opened; never a borrowed one.

        A failed connect may leave a partially-created asyncpg pool, and a
        failed schema upgrade leaves a fully-open pool. Both would otherwise
        persist until process exit while ``_db_ready`` is false.
        """
        if not opened_here:
            return
        try:
            if db.connected:
                await db.close()
        except Exception as _boundary_error:
            record_failure("server.initialize.db_cleanup", _boundary_error)

    async def _auto_connect_connectors(self) -> None:
        """Best-effort startup reconnect for configured HTTP MCP servers.

        Runs after initialize has returned (created as a task) and never opens
        a browser or requests approval. Failures stay silent here; /mcp shows
        the honest per-server state and offers authorization.
        """
        try:
            await self.runtime.get_connector_service().auto_connect_authorized()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            record_failure("server.connector_autoconnect", error)

    async def _session_create(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create and select a session using the agent definition and gateway routing defaults."""
        self.require_db()
        title = str(params.get("title") or "").strip()[:80] or "New session"
        agent_id = str(params.get("agent") or "harness").strip()[:100] or "harness"
        async with self._config_lock:
            model, provider = self.model, self.provider
        try:
            session = await session_service.create(
                SessionCreateSpec(
                    title=title,
                    agent_id=agent_id,
                    desktop_workspace_root=getattr(self, "_desktop_workspace_root", None),
                    default_provider=provider,
                    default_model=model,
                )
            )
        except SessionCreateError as error:
            raise RpcError(INVALID_PARAMS, error.message) from error
        self._opened_session_id = session.id
        try:
            from machina.gateway.features.mode import grant_full_default

            await grant_full_default(session)
        except Exception as _boundary_error:
            # Optional fallback preserves the primary outcome; report no payload.
            record_failure("server._session_create", _boundary_error)
        return {"session": session_to_dict(session)}

    async def _session_list(self, params: dict[str, Any]) -> dict[str, Any]:
        self.require_db()
        limit = params.get("limit", 20)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise RpcError(INVALID_PARAMS, "limit must be an integer between 1 and 200")
        cursor = params.get("cursor")
        if cursor is None:
            offset = 0
        elif isinstance(cursor, str) and cursor.isascii() and cursor.isdigit():
            offset = int(cursor)
        else:
            raise RpcError(INVALID_PARAMS, "cursor must be a non-negative integer string")
        if offset > 100_000:
            raise RpcError(INVALID_PARAMS, "cursor offset too large (max 100000)")
        sessions = await session_manager.list_sessions(limit=limit + 1, offset=offset)
        result: dict[str, Any] = {
            "sessions": [session_to_dict(s) for s in sessions[:limit]],
        }
        if len(sessions) > limit:
            result["nextCursor"] = str(offset + limit)
        return result

    async def _session_family(self, params: dict[str, Any]) -> dict[str, Any]:
        """List one delegated family without paging through unrelated chats."""
        self.require_db()
        session = await self.get_session(params)
        root_id = (
            session.parent_session_id
            if not session.is_fork and session.parent_session_id
            else session.id
        )
        family = await session_manager.list_family(root_id)
        return {"sessions": [session_to_dict(member) for member in family]}

    async def _session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        self.require_db()
        session = await self.get_session(params)
        self._opened_session_id = session.id
        from machina.core.context import context_manager

        with instrument("session", "resume", session_id=str(session.id)) as span:
            chunks = await context_manager.get_transcript_chunks(
                session.id,
                limit=MAX_RESUME_HISTORY_CHUNKS,
                include_embedding=False,
            )
            # Keep the defensive slice for test doubles and older context
            # implementations; the real query is already bounded.
            history = history_from_chunks(chunks[:MAX_RESUME_HISTORY_CHUNKS])
            span.note(history_chunks=len(history))
        return {
            "session": session_to_dict(session),
            "history": history,
        }

    async def _usage_get(self, params: dict[str, Any]) -> dict[str, Any]:
        self.require_db()
        session = await self.get_session(params)
        agent = params.get("agent") or session.agent_id
        if not isinstance(agent, str) or not 1 <= len(agent) <= 100:
            raise RpcError(INVALID_PARAMS, "agent must be a non-empty string up to 100 characters")
        from machina.core.usage import usage_store

        return await usage_store.summary(session.id, agent)

    async def _prompt_submit(self, params: dict[str, Any]) -> dict[str, Any]:
        self.require_db()
        session = await self.get_session(params)
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RpcError(INVALID_PARAMS, "text must be a non-empty string")
        text = text.strip()
        if len(text) > 20_000:
            raise RpcError(INVALID_PARAMS, "text is too long (max 20000 characters)")
        from machina.core.images import validate_images

        try:
            images = validate_images(params.get("images", []))
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from exc
        for index in range(1, len(images) + 1):
            marker = f"[image {index}]"
            if marker not in text:
                text += "\n" + marker
        # A harness-backed child chat forwards replies to the same external
        # platform instead of running a native agent turn.
        harness_platform = ""
        if isinstance(getattr(session, "state", None), dict):
            harness_platform = str(session.state.get("harness_platform") or "").strip()
        if harness_platform:
            key = str(session.id)
            turn_id = uuid.uuid4().hex[:12]
            token = await TurnLifecycle.acquire_claim(session.id, turn_id=turn_id)
            if not token:
                raise RpcError(TURN_IN_PROGRESS, "a turn is already running for this session")
            running = self._turns.get(key)
            if running is not None and not running.done():
                from machina.core.turns import end_turn as _end

                await _end(session.id, token)
                raise RpcError(TURN_IN_PROGRESS, "a turn is already running for this session")
            try:
                from machina.core.context import context_manager

                await context_manager.add_chunk(
                    session.id,
                    session.agent_id,
                    "user_message",
                    {"content": text},
                    token_count=max(1, len(text) // 4),
                )
            except Exception:
                logger.exception("harness chat could not persist the user message")
            logger.info("Harness turn %s started for session %s", turn_id, session.id)
            self._turn_tokens[key] = token
            task = asyncio.create_task(
                self._run_harness_chat_turn(session, turn_id, text, harness_platform, token)
            )
            self._turns[key] = task
            return {"turnId": turn_id}
        key = str(session.id)
        # Shared coordinator claim (cross-transport owner token). The DB claim
        # fences same-process and REST/scheduler workers alike (N3, final pass:
        # the former `_turn_locks` in-process lock was declared but never
        # acquired - removed). Fails closed on claim errors.
        turn_id = uuid.uuid4().hex[:12]
        token = await TurnLifecycle.acquire_claim(session.id, turn_id=turn_id)
        if not token:
            raise RpcError(TURN_IN_PROGRESS, "a turn is already running for this session")
        running = self._turns.get(key)
        if running is not None and not running.done():
            from machina.core.turns import end_turn as _end

            await _end(session.id, token)
            raise RpcError(TURN_IN_PROGRESS, "a turn is already running for this session")
        steering_id = params.get("steeringId")
        if steering_id is not None:
            inbox = self._steering.get(key)
            item = (
                next((item for item in inbox.items if item.id == steering_id), None)
                if inbox
                else None
            )
            if item is None or item.text != text:
                from machina.core.turns import end_turn as _end

                await _end(session.id, token)
                raise RpcError(
                    INVALID_PARAMS, "queued steering changed; refresh activity before retrying"
                )
            inbox.remove(item.id)
        logger.info("Turn %s started for session %s", turn_id, session.id)
        self._turn_tokens[key] = token
        from machina.core.steering import SteeringInbox

        self._steering.setdefault(key, SteeringInbox(session.id))
        image_kwargs = {"images": images} if images else {}
        self._turns[key] = asyncio.create_task(
            self._run_turn_with_timeout(session.id, turn_id, text, **image_kwargs)
        )
        return {"turnId": turn_id}

    async def _run_harness_chat_turn(
        self,
        session: Session,
        turn_id: str,
        text: str,
        platform: str,
        token: str,
    ) -> None:
        """Forward one child-chat reply to the harness and stream its output."""
        key = str(session.id)

        def emit(event_type: str, **fields: Any) -> None:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": self.event_payload(event_type, key, turn_id, **fields),
                }
            )

        emit("message.start")
        body = ""
        streamed: list[str] = []
        status_note = ""

        def on_progress(chunk: str) -> None:
            # The live log is the transcript: keep it visible instead of
            # replacing it with the short final answer when the job ends.
            streamed.append(chunk)
            emit("message.delta", text=chunk)

        try:
            from machina.core import harness_jobs
            from machina.gateway.features.interop import _workspace_cwd

            try:
                cwd = _workspace_cwd(session)
            except Exception:
                cwd = None
            resume = ""
            if isinstance(getattr(session, "state", None), dict):
                resume = str(session.state.get("harness_session_id") or "").strip()
            job = await harness_jobs.start(
                session_id=key,
                platform=platform,
                task=text,
                timeout=HARNESS_CHAT_TIMEOUT_SECONDS,
                cwd=cwd,
                reuse_session=True,
                record_user=False,
                on_progress=on_progress,
                resume_session=resume or None,
            )
            final = await harness_jobs.wait(job["id"], timeout=HARNESS_CHAT_TIMEOUT_SECONDS + 30.0)
            if final is None:
                body = "Harness job disappeared before producing a result."
            else:
                body = (
                    final.get("stdout")
                    or final.get("stderr")
                    or final.get("error")
                    or "(no output)"
                )
                if final.get("status") != "complete":
                    status_note = f"Harness {platform} {final.get('status')}"
        except asyncio.CancelledError:
            status_note = "Harness turn cancelled."
            raise
        except Exception as exc:
            status_note = f"Harness {platform} failed: {type(exc).__name__}: {exc}"
        finally:
            logged = "".join(streamed)
            answer = body.strip()
            if logged.strip():
                body = logged.rstrip()
                # Keep the live "what it is doing" log and append the final
                # answer only when the stream did not already contain it.
                # Compare normalized text: token events split the answer
                # across newlines, so a raw substring test would duplicate it.
                flat_body = " ".join(body.split())
                flat_answer = " ".join(answer.split())
                if flat_answer and flat_answer not in flat_body:
                    body = f"{body}\n\n{answer}"
                if status_note and status_note not in body:
                    body = f"{body}\n\n{status_note}"
            elif status_note:
                body = status_note if not answer else f"{status_note}: {answer}"
            try:
                emit(
                    "message.complete",
                    text=body,
                    tokens=0,
                    iterations=1,
                    toolCalls=0,
                    cancelled=False,
                )
            finally:
                from machina.core.turns import end_turn as _end

                try:
                    await _end(session.id, token)
                except Exception:
                    logger.exception("harness chat could not release its turn claim")
                self._turn_tokens.pop(key, None)
                current = self._turns.get(key)
                if current is not None and current is asyncio.current_task():
                    self._turns.pop(key, None)

    async def _shell_jobs(self, params: dict[str, Any]) -> dict[str, Any]:
        self.require_db()
        from machina.core.shell_jobs import shell_jobs

        session = await self.get_session(params)
        job_id = params.get("jobId")
        if not job_id:
            return {"jobs": shell_jobs.list(session.id)}
        try:
            if params.get("background"):
                return {
                    "job": shell_jobs.detach(session.id, str(job_id)).snapshot(include_output=True)
                }
            job = (
                (await shell_jobs.stop(session.id, str(job_id)))
                if params.get("stop")
                else shell_jobs.get(session.id, str(job_id))
            )
            return {"job": job.snapshot(include_output=True)}
        except KeyError as error:
            raise RpcError(
                INVALID_PARAMS, "background command not found in this session"
            ) from error

    async def _prompt_steer(self, params: dict[str, Any]) -> dict[str, Any]:
        self.require_db()
        session = await self.get_session(params)
        key = str(session.id)
        task = self._turns.get(key)
        inbox = self._steering.get(key)
        if not task or task.done() or inbox is None:
            return {"queued": False}
        text = params.get("text")
        if not isinstance(text, str):
            raise RpcError(INVALID_PARAMS, "text must be a string")
        try:
            item = inbox.add(text)
        except ValueError as error:
            raise RpcError(INVALID_PARAMS, str(error)) from error
        return {"queued": True, "id": item.id, "items": inbox.snapshot()}

    async def _prompt_steering(self, params: dict[str, Any]) -> dict[str, Any]:
        self.require_db()
        session = await self.get_session(params)
        key = str(session.id)
        inbox = self._steering.get(key)
        if inbox is None:
            return {"items": []}
        remove = params.get("remove")
        if remove is not None:
            inbox.remove(str(remove))
        if params.get("replace"):
            text = params.get("text")
            if not isinstance(text, str):
                raise RpcError(INVALID_PARAMS, "text must be a string")
            try:
                if not inbox.replace(str(params["replace"]), text):
                    raise RpcError(
                        INVALID_PARAMS, "this steering message has already been delivered"
                    )
            except ValueError as error:
                raise RpcError(INVALID_PARAMS, str(error)) from error
        return {"items": inbox.snapshot()}

    async def _question_answer(self, params: dict[str, Any]) -> dict[str, Any]:
        """Answer the structured question currently waiting in one turn."""
        self.require_db()
        session = await self.get_session(params)
        request_id = params.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            raise RpcError(INVALID_PARAMS, "requestId is required")
        cancelled = bool(params.get("cancelled"))
        answer = params.get("answer")
        if not cancelled and (not isinstance(answer, str) or not answer.strip()):
            raise RpcError(INVALID_PARAMS, "answer must be a non-empty string or cancelled=true")
        from machina.core.questions import question_broker

        if not question_broker.answer(
            session.id,
            request_id,
            "User cancelled the question. Continue with your best judgment."
            if cancelled
            else str(answer),
        ):
            raise RpcError(INVALID_PARAMS, "this question is no longer pending")
        return {"answered": True}

    async def _prompt_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        key = str(_parse_session_id(params))
        task = self._turns.get(key)
        if task is None or task.done():
            return {"cancelled": False}
        task.cancel()
        await asyncio.sleep(0)  # let the cancellation deliver before reporting
        try:
            # Best-effort join so the turn's cleanup runs before we answer.
            # shield() keeps a cancelled turn from cancelling this handler.
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except (asyncio.CancelledError, TimeoutError):
            pass
        # Entry removal is left to _run_turn's finally block, which deletes
        # only if the stored task is still the current one (identity check).
        return {"cancelled": True}

    async def _config_set(self, params: dict[str, Any]) -> dict[str, Any]:
        """Set a setting. ``model``/``provider`` apply to this gateway; any other
        non-secret key updates the config (and ``~/.machina/config.yaml``
        when ``persist`` is true). Secrets are only ever read from the environment."""
        from machina.core.config import DEFAULTS, SECRET_KEYS

        key, value = params.get("key"), params.get("value")
        persist = _bool(params, "persist")
        async with self._config_lock:
            previous_route = getattr(self, key) if key in ("model", "provider") else None
            if key == "model":
                self._set_model(value)
                value = self.model
            elif key == "provider":
                self._set_provider(value)
                value = self.provider
            elif key in SECRET_KEYS:
                raise RpcError(INVALID_PARAMS, f"{key} is a secret; use /keys or .env instead")
            elif isinstance(key, str) and key in DEFAULTS:
                # Coerce first so invalid values raise; persist the coerced
                # value. reasoning_effort goes through the same single-sourced
                # vocabulary as config.set (aliases normalize to "" there), so
                # this surface and models.setEffort can never disagree.
                value = features.coerce_config_value(key, value)
                config.set(key, value, persist=persist)
            else:
                raise RpcError(INVALID_PARAMS, f"unknown setting: {key}")
            if persist and key in ("model", "provider"):
                try:
                    config.set(key, value, persist=True)
                except Exception:
                    setattr(self, key, previous_route)
                    raise
                # MACHINA_MODEL/PROVIDER from .env shadows config.yaml on
                # restart (Config.get checks env first). Sync both so a saved
                # default survives restarts and live config.get reads.
                env_key = f"MACHINA_{key.upper()}"
                try:
                    from machina.security.env_file import sync_env_shadow

                    sync_env_shadow(env_key, value)
                except (OSError, ValueError) as e:
                    logger.warning(
                        "config.set: .env sync failed for %s (%s)",
                        env_key,
                        type(e).__name__,
                    )
            elif key == "execution_mode":
                # F1: Config.get reads MACHINA_EXECUTION_MODE before the
                # in-memory value, so a global mode write is shadowed by the
                # .env line `machina mode X` leaves behind unless the shadow
                # is updated too. `value` is the coerced new value, never
                # config.get (which still prefers the stale shadow). Process
                # env always; .env in the durable path.
                sync_execution_mode_env(str(value), persist=persist)
        result: dict[str, Any] = {"model": self.model, "provider": self.provider, "key": key}
        result["value"] = getattr(self, key) if key in ("model", "provider") else config.get(key)
        return result

    async def _shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        logger.info("Gateway shutdown requested")
        self.closing = True
        return {}

    # ─── turns ─────────────────────────────────────────────────────────────
    async def _run_turn(
        self,
        session_id: uuid.UUID,
        turn_id: str,
        text: str,
        *,
        images: list[dict[str, str]] | None = None,
    ) -> None:
        """Run one agent turn through the shared transport-neutral lifecycle."""
        sid = str(session_id)
        from machina.core.questions import question_broker

        owned_token = self._turn_tokens.get(sid)
        registered_turn = self._turns.get(sid)
        self._record_stage(turn_id, "turn_started", session_id=sid)
        # S2 observe-only hook: open the turn span (never gates execution).
        try:
            from machina.core.intent import classify_task as _trace_classify
            from machina.observability.traces import start_turn as _trace_start_turn

            try:
                _trace_task_class = _trace_classify(text)
            except Exception:
                _trace_task_class = ""
            _trace_start_turn(turn_id, session_id=sid, task_class=_trace_task_class)
        except Exception:
            pass

        def emit(event_type: str, **fields: Any) -> None:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": self.event_payload(event_type, sid, turn_id, **fields),
                }
            )

        _owned_task = asyncio.current_task()

        class _TurnHooks:
            def on_stage(_self, stage: str) -> None:
                self._record_stage(turn_id, stage, session_id=sid)

            def on_ownership_lost(_self, reason: str) -> None:
                # Stop owned effects FIRST: the cancel is the critical effect
                # and must not be skippable by an emit failure. The frame
                # (correct protocol name: turn.ownership_lost, underscore)
                # follows.
                if _owned_task is not None:
                    _owned_task.cancel()
                emit("turn.ownership_lost", reason=reason)

        # Phase E: human approval handler for this turn. Emits
        # permission.required and awaits the UI/HTTP resolve without holding
        # DB transactions — logical turn ownership is retained.
        async def _turn_approver(card: dict) -> str:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[str] = loop.create_future()
            self._pending_approvals[card["request_id"]] = (fut, turn_id, sid)
            # Concurrent approval cards share one paused wall-clock interval.
            self._approval_wait_started.setdefault(turn_id, loop.time())
            # AH-AUDIT-003/004: the card carries the exact executable and
            # arguments (argv list), cwd, backend, timeout, network, and
            # file-write content identity + diff. Never a bare operation
            # name with an empty target.
            from machina.protocol import approval_fields

            emit("needs_approval", **approval_fields(card))
            # AH-AUDIT-029: genuine human waits pause compute accounting
            # (tracked separately); approval timeout stays broker-bounded.
            # Renew logical ownership while waiting so a long human pause
            # does not expire the live claim mid-turn.
            try:
                from machina.core.turns import renew_turn as _renew_claim

                _renew_token = owned_token
            except Exception as exc:
                record_failure("server.approval_renew", exc)
                _renew_token = None
            try:
                return await fut
            finally:
                self._pending_approvals.pop(card["request_id"], None)
                try:
                    if not any(
                        owner == turn_id for _, owner, _ in self._pending_approvals.values()
                    ):
                        started = self._approval_wait_started.pop(turn_id, None)
                        if started is not None:
                            waited = asyncio.get_running_loop().time() - started
                            self._approval_wait_total[turn_id] = self._approval_wait_total.get(
                                turn_id, 0.0
                            ) + max(0.0, waited)
                    if _renew_token:
                        renewed = await _renew_claim(session_id, _renew_token)
                        if not renewed:
                            from machina.core.turns import owns_claim as _owns_claim

                            if not await _owns_claim(session_id, _renew_token):
                                # Ownership moved on during the human wait:
                                # stop owned effects instead of interleaving
                                # with the new owner.
                                _current = asyncio.current_task()
                                if _current is not None:
                                    _current.cancel()
                                raise RuntimeError("turn ownership lost during approval wait")
                except RuntimeError:
                    raise
                except Exception as _boundary_error:
                    # Optional fallback preserves the primary outcome; report no payload.
                    record_failure("server._run_turn", _boundary_error)

        def _approval_resolved(card: dict) -> None:
            # R4-065: the broker expired the deadline itself (no human
            # decision). Retire the card on the same channel as decisions.
            emit(
                "approval.resolved",
                requestId=str(card.get("request_id", "")),
                status=str(card.get("status") or "expired"),
            )

        # The lifecycle owns approval registration, row construction, renewal,
        # stream relay and the bounded cleanup/release tail. The gateway keeps
        # only JSON-RPC serialization, the interactive approval card body, the
        # loss frame, and terminal frame mapping.
        lifecycle = TurnLifecycle(
            session_id=session_id,
            turn_id=turn_id,
            token=owned_token,
            text=text,
            images=images,
            boundary="server._run_turn",
            renew_interval_s=CLAIM_RENEW_INTERVAL_SECONDS,
            hooks=_TurnHooks(),
            approval=ApprovalPolicy(
                mode="interactive",
                handler=_turn_approver,
                principal="tui",
                on_resolved=_approval_resolved,
            ),
        )
        engine = lifecycle.engine

        def complete(cancelled: bool = False) -> None:
            cancelled = cancelled or turn_id in self._cancel_requested
            # AH-AUDIT-027: message.complete.text is the canonical complete
            # answer — the fully-redacted final content exactly once (the
            # engine owns the exactly-once withheld-tail flush and the token
            # accounting; see TurnEngine.build_terminal).
            # Exactly-once guard: quarantined timeout paths may emit a
            # fallback completion; a late genuine completion is then a
            # duplicate and is suppressed (first terminal event wins).
            if turn_id in self._turn_finished:
                return
            self._turn_finished.add(turn_id)
            final = lifecycle.final
            # S2 observe-only hook: close the turn span (never gates execution).
            try:
                from machina.observability.traces import end_turn as _trace_end_turn

                if cancelled:
                    _trace_end_turn(turn_id, status="cancelled", stop_reason="cancelled")
                elif final is None:
                    _trace_end_turn(turn_id, status="error", stop_reason="error")
                else:
                    _trace_end_turn(
                        turn_id,
                        status="ok",
                        stop_reason=str(getattr(final, "outcome", "") or "completed"),
                    )
            except Exception:
                pass
            emit("message.complete", **lifecycle.build_terminal(cancelled=cancelled))

        emit("message.start")
        # Structured questions are delivered to the active client for this
        # session while the tool call waits. The broker is scoped to this turn
        # and always unbound in the cleanup below.
        question_broker.bind(sid, lambda payload: emit("question.asked", **payload))
        _cancelled = False
        _release_failed = False
        # Growth indicator: background training (consolidation wins, staged
        # reviews) surfaces as growth.status events on this turn's stream.
        from machina.core.agent import add_growth_listener, remove_growth_listener

        def _growth_emit(notify_session: str, stage: str, detail: str) -> None:
            if notify_session != sid:
                return
            try:
                emit("growth.status", stage=stage, detail=detail)
            except Exception as _boundary_error:
                record_failure("server.growth_emit", _boundary_error)

        add_growth_listener(_growth_emit)
        from machina.core.orchestrator import child_observer

        child_observer_token = child_observer.set(
            lambda child_id, phase, value: emit(
                "agent.child", childSessionId=str(child_id), phase=phase, value=value
            )
        )
        from machina.core.steering import SteeringInbox, steering_inbox

        inbox = self._steering.setdefault(sid, SteeringInbox(session_id))
        inbox.changed = lambda items, delivered: emit(
            "steering.update", items=items, delivered=delivered
        )
        if inbox.items:
            inbox.notify()
        steering_token = steering_inbox.set(inbox)
        # AH-AUDIT-013: managed claim renewal for the whole turn (compute +
        # approval waits). A live renewing owner blocks takeovers; repeated
        # renewal failure means the claim lapsed — owned effects stop via
        # the loss signal. Crash expiry recovery is preserved (a dead
        # worker renews nothing and its claim becomes reclaimable).
        _ownership_lost = self._turn_ownership_lost.setdefault(turn_id, asyncio.Event())
        # W2: the engine owns the renewal loop (shared W3 primitive) and adopts
        # this transport's loss event so readers here and the renewal primitive
        # share ONE signal; on_lost cancels owned work via _TurnHooks.
        lifecycle.start(ownership_lost=_ownership_lost)
        try:
            turn_scope_token = None
            try:
                # AH-021/AH-022: resolve the session's stored model/provider and
                # agent definition (allowed_tools/persona). Resumed sessions
                # execute with their own settings, not the gateway globals;
                # globals only apply to newly created sessions. Direct execution
                # of a specialist child can no longer bypass its restrictions.
                # AH-AUDIT-019: execution-critical read bypasses the display
                # cache so resumed sessions run with current durable settings.
                session = await lifecycle.load_session(session_id)
                if session is None:
                    # W1: no terminal here — the single outer finally below runs
                    # cleanup and emits message.complete after release.
                    emit("error", message="session not found")
                    return
                # S2 observe-only hook: attribute the agent id (never gates execution).
                try:
                    from machina.observability.traces import set_turn_attributes as _trace_set_attrs

                    _trace_set_attrs(turn_id, agent_id=getattr(session, "agent_id", ""))
                except Exception:
                    pass
                try:
                    from machina.core.agent_factory import build_agent_for_session
                except Exception:
                    # Fail closed (Pattern C/5.4): no silent fallback to a weaker
                    # global factory. Surface the error; the turn cannot run.
                    logger.exception("Agent factory unavailable")
                    # W1: terminal is emitted once by the outer finally, after
                    # renewal join + claim release.
                    emit("error", message="agent factory unavailable; turn aborted")
                    return
                self._record_stage(turn_id, "building_agent", session_id=sid)
                if self._agent_factory is _default_agent_factory:
                    agent = await lifecycle.prepare(session, builder=build_agent_for_session)
                else:
                    # Explicit in-process dependency injection is trusted code,
                    # never a fallback after a default factory failure. Resolve
                    # stored session settings on this path too.
                    async def _build_injected(_session: Any) -> Any:
                        return self._agent_factory(
                            session.model or self.model, session.provider or self.provider
                        )

                    agent = await lifecycle.prepare(session, builder=_build_injected)
                self._record_stage(turn_id, "waiting_for_provider", session_id=sid)
                # R4-010: driver-owned turn scope — the context must be set
                # before per-step tasks are created (see TurnEngine.stream).
                turn_scope_token = begin_turn_scope()
                # R4-064: driver-owned OTel stream span — this transport
                # drives the relay inline in one task, so the span is
                # entered and detached in the same context.
                with span(
                    "agent.run_stream",
                    agent_id=str(getattr(agent, "agent_id", "")),
                    session_id=str(session_id),
                ):
                    # W2: the shared engine owns boundary-safe streaming redaction,
                    # tool-call pairing/truncation, token accounting and final
                    # capture; this loop only serializes semantic events.
                    async with aclosing(lifecycle.stream()) as events:
                        async for turn_event in events:
                            if turn_event.kind == "delta":
                                emit("message.delta", text=turn_event.payload["text"])
                            elif turn_event.kind == "tool_start":
                                self._record_stage(turn_id, "running_tool", session_id=sid)
                                self._turn_progress[turn_id] = {
                                    "tokens": engine.tokens,
                                    "iterations": 0,
                                    "toolCalls": engine.tool_count,
                                }
                                emit(
                                    "tool.start",
                                    id=turn_event.payload["id"],
                                    name=turn_event.payload["name"],
                                    args=turn_event.payload["args"],
                                )
                            elif turn_event.kind == "tool_complete":
                                payload = turn_event.payload
                                emit(
                                    "tool.complete",
                                    id=payload["id"],
                                    name=payload["name"],
                                    result=payload["result"],
                                    truncated=payload["truncated"],
                                    isError=payload["isError"],
                                    **(
                                        {"outcome": payload["outcome"]}
                                        if "outcome" in payload
                                        else {}
                                    ),
                                )
                            elif turn_event.kind == "needs_approval":
                                # The lifecycle's interactive policy suppresses
                                # these markers; the broker handler emits the card.
                                continue
                            elif turn_event.kind == "usage":
                                self._turn_progress[turn_id] = {
                                    "tokens": engine.tokens,
                                    "iterations": 0,
                                    "toolCalls": engine.tool_count,
                                }
                                usage_payload = {"tokens": turn_event.payload["tokens"]}
                                context_tokens = turn_event.payload.get("contextTokens")
                                if isinstance(context_tokens, int) and context_tokens > 0:
                                    usage_payload["contextTokens"] = context_tokens
                                context_percent = turn_event.payload.get("contextPercent")
                                if isinstance(context_percent, (int, float)):
                                    usage_payload["contextPercent"] = context_percent
                                credits_used = turn_event.payload.get("creditsUsed")
                                if isinstance(credits_used, (int, float)):
                                    usage_payload["creditsUsed"] = credits_used
                                emit("usage", **usage_payload)
                            elif turn_event.kind == "done":
                                final_response = lifecycle.final
                                self._turn_progress[turn_id] = {
                                    "tokens": (
                                        getattr(final_response, "tokens_used", 0) or engine.tokens
                                    ),
                                    "iterations": (getattr(final_response, "iterations", 0) or 0),
                                    "toolCalls": len(
                                        getattr(final_response, "tool_calls", None) or []
                                    ),
                                }
                            else:
                                logger.warning("Unknown turn event kind: %s", turn_event.kind)
            except asyncio.CancelledError:
                # AH-AUDIT-028: do NOT emit terminal completion here. Emit a
                # stopping state (UI stays blocked), run cleanup + ownership
                # release in finally, then emit the single terminal completion
                # below — completion means the turn is no longer owned.
                _cancelled = True
                emit("message.stopping", reason="cancelled")
            except Exception as exc:
                _cancelled = _ownership_lost.is_set()
                logger.exception("Turn %s failed", turn_id)
                from machina.core.exceptions import ProviderError, ValidationError
                from machina.memory.redaction import redact_diagnostic

                # Only typed provider/validation failures may cross the wire;
                # arbitrary runtime exceptions stay generic so internal
                # implementation details are not exposed to the UI.
                if isinstance(exc, (ProviderError, ValidationError)):
                    detail = redact_diagnostic(exc).strip()[:500]
                    suffix = f": {detail}" if detail else ""
                else:
                    # Type-only context helps diagnose an untied failure without
                    # exposing an arbitrary exception's potentially sensitive text.
                    # Point at the supported traceback surface so the user is not
                    # left with a bare class name.
                    suffix = f" ({type(exc).__name__}); run `machina debug logs` for the traceback"
                emit("error", message="agent turn failed" + suffix)
            finally:
                if turn_scope_token is not None:
                    end_turn_scope(turn_scope_token)
                # H-01/H-02: the bounded cleanup contract is owned by the
                # lifecycle/engine: clear the approval handler (and cancel any
                # still-pending approval waits), join optional work -> provider
                # close -> stop renewal -> shielded bounded claim release ->
                # join renewal. Ordinary failures are reported inside close().
                try:
                    close = await lifecycle.close(
                        cancelled=_cancelled,
                        on_closing=lambda: self.cancel_turn_approvals(turn_id),
                    )
                    _release_failed = close.release_failed
                finally:
                    # Owner-token bookkeeping stays adapter-side: clear it only
                    # when ownership was released, otherwise preserve it for DB
                    # reconciliation / TTL expiry.
                    if not _release_failed and self._turn_tokens.get(sid) == owned_token:
                        self._turn_tokens.pop(sid, None)
                    if registered_turn is not None and self._turns.get(sid) is registered_turn:
                        del self._turns[sid]
        finally:
            # W1: ONE always-run cleanup after add_growth_listener. The two
            # early return paths (session not found / agent factory
            # unavailable) used to call complete() BEFORE cleanup and then
            # skip this block entirely: the growth listener and the
            # _turn_ownership_lost entry leaked, the renewal loop was never
            # joined (a delayed spurious turn.ownership_lost could land after
            # message.complete) and _cancel_requested kept the turn id.
            # AH-AUDIT-013: engine.close() above stopped/joined renewal; the
            # shared loss event is retired here so it cannot leak.
            self._turn_ownership_lost.pop(turn_id, None)
            try:
                if _release_failed:
                    # No terminal: the claim may belong to a successor, so
                    # completion cannot claim the turn is unowned. Recovery
                    # stays via DB reconciliation / TTL expiry.
                    emit("turn.cleanup_pending", reason="ownership release unavailable")
                else:
                    # AH-AUDIT-027: emit the safe flushed tail as a delta
                    # exactly once before terminal completion, so concatenated
                    # displayed text equals the canonical redacted answer
                    # exactly once (also when prior deltas were emitted and
                    # when the whole answer was withheld).
                    try:
                        _tail = lifecycle.flush_tail()
                        if _tail:
                            emit("message.delta", text=_tail)
                    except Exception as _boundary_error:
                        # Optional fallback preserves the primary outcome; report no payload.
                        record_failure("server._run_turn", _boundary_error)
                    # AH-AUDIT-028: this is the single terminal completion for
                    # every path (success/error/cancel/early-abort) — emitted
                    # only after cleanup and ownership release succeeded.
                    complete(cancelled=_cancelled)
            finally:
                child_observer.reset(child_observer_token)
                steering_inbox.reset(steering_token)
                inbox.changed = None
                question_broker.unbind(sid)
                # Growth notices may land with late background consolidation;
                # keep the listener until the terminal event, then always drop it.
                remove_growth_listener(_growth_emit)
                self._cancel_requested.discard(turn_id)
                self._record_stage(turn_id, "answer_complete", session_id=sid)
        if _release_failed:
            # Claim still owned by this turn's token (release unavailable):
            # do not schedule cross-owner maintenance.
            return
        # Phase C: threshold-triggered auto-compaction at the safe turn
        # boundary (ownership released above). AH-AUDIT-025: registered
        # with the shared runtime so shutdown cancels/joins it before DB
        # closure instead of leaking a use-after-close call.
        self._record_stage(turn_id, "compaction_maintenance", session_id=sid)
        lifecycle.schedule_auto_compact()

    async def _run_turn_with_timeout(
        self,
        session_id: uuid.UUID,
        turn_id: str,
        text: str,
        *,
        images: list[dict[str, str]] | None = None,
    ) -> None:
        """Observe compute time separately from approvals; retirement owns completion.

        Timeout and cancel request termination and observe a bounded grace. A
        resistant inner task retains its claim and strong registration; it alone
        emits terminal completion after its actual cleanup. A late result cannot
        turn a requested cancellation into success.
        """
        timeout = float(config.get("turn_timeout"))
        sid = str(session_id)
        current = asyncio.current_task()
        turn_kwargs = {"images": images} if images else {}
        inner = asyncio.create_task(self._run_turn(session_id, turn_id, text, **turn_kwargs))
        start = asyncio.get_running_loop().time()

        def retired(task) -> None:
            if not task.cancelled():
                task.exception()
            if self._turns.get(sid) is task:
                self._turns.pop(sid, None)
            self._turn_progress.pop(turn_id, None)
            self._turn_stages.pop(turn_id, None)  # W2: stage tracker no longer grows forever
            self._approval_wait_total.pop(turn_id, None)
            self._approval_wait_started.pop(turn_id, None)
            self._cancel_requested.discard(turn_id)

        inner.add_done_callback(retired)

        async def request_stop(reason: str) -> None:
            self._cancel_requested.add(turn_id)
            inner.cancel()
            done, _ = await asyncio.wait({inner}, timeout=CANCEL_JOIN_TIMEOUT_SECONDS)
            if inner not in done:
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": self.event_payload(
                            "turn.cleanup_pending", sid, turn_id, reason=reason
                        ),
                    }
                )

        try:
            while not inner.done():
                now = asyncio.get_running_loop().time()
                waited = self._approval_wait_total.get(turn_id, 0.0)
                approval_started = self._approval_wait_started.get(turn_id)
                if approval_started is not None:
                    waited += max(0.0, now - approval_started)
                if timeout > 0 and now - start - waited >= timeout:
                    logger.warning("Turn %s exceeded compute timeout", turn_id)
                    self._write(
                        {
                            "jsonrpc": "2.0",
                            "method": "event",
                            "params": self.event_payload(
                                "error", sid, turn_id, message="turn timed out"
                            ),
                        }
                    )
                    await request_stop("timeout")
                    return
                await asyncio.wait({inner}, timeout=0.2)
            if not inner.cancelled():
                inner.result()
        except asyncio.CancelledError:
            await request_stop("cancelled")
            raise
        finally:
            if self._turns.get(sid) is current:
                if inner.done():
                    self._turns.pop(sid, None)
                else:
                    self._turns[sid] = inner
                    from machina.core.runtime import runtime_services

                    runtime_services.track(inner)
            if len(self._turn_finished) > 1000:
                self._turn_finished.clear()


def _parse_session_id(params: dict[str, Any]) -> uuid.UUID:
    """Parse sessionId, accepting canonical, braced, and URN UUID forms."""
    try:
        return uuid.UUID(str(params.get("sessionId")).strip())
    except (ValueError, TypeError):
        raise RpcError(INVALID_PARAMS, "sessionId must be a UUID") from None
