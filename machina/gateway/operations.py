"""Gateway-owned long-running work, independent of an individual RPC request.

The RPC dispatcher stays serial. Only the two explicitly permitted runners
execute in owned background tasks, with one operation active per gateway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from machina.gateway.errors import (
    INVALID_PARAMS,
    NOT_FOUND,
    OPERATION_FAILED,
    TURN_IN_PROGRESS,
    UNAUTHORIZED,
    RpcError,
)
from machina.gateway.features._common import _bool, _str, _uuid

if TYPE_CHECKING:
    from machina.gateway.server import Gateway

logger = logging.getLogger(__name__)

MAX_RETAINED_OPERATIONS = 32
MAX_RESULT_BYTES = 256 * 1024
MAX_PARAMS_BYTES = 256 * 1024
MAX_ERROR_BYTES = 4096
MAX_APPROVAL_BYTES = 64 * 1024
MAX_PENDING_APPROVAL_BYTES = 512 * 1024
MAX_PENDING_APPROVALS = 64
CLAIM_RENEW_INTERVAL_SECONDS = 30.0
_RUN_METHODS = frozenset({"agents.run", "interop.run"})


def _diagnostic(error: object) -> str:
    """Redact before bounding, and fail closed if the redactor fails."""
    try:
        from machina.memory.redaction import redact_diagnostic

        text = redact_diagnostic(error)
    except Exception:
        text = "[redaction unavailable]"
    return text.encode("utf-8")[:MAX_ERROR_BYTES].decode("utf-8", errors="ignore")


@dataclass
class _Operation:
    snapshot: dict[str, Any]
    session_id: uuid.UUID | None
    token: str | None
    task: asyncio.Task[None] | None = None
    entered: bool = False
    cancel_requested: bool = False
    ownership_error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()


class Operations:
    """Retain bounded results and own every runner through actual settlement."""

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway
        self._items: dict[str, _Operation] = {}
        self._start_lock = asyncio.Lock()
        self._closed = False

    def session_running(self, session_id: uuid.UUID) -> bool:
        return any(item.session_id == session_id and item.active for item in self._items.values())

    async def _validate(
        self, params: dict[str, Any]
    ) -> tuple[str, dict[str, Any], uuid.UUID | None]:
        method = _str(params, "method", max_len=32)
        if method not in _RUN_METHODS:
            raise RpcError(INVALID_PARAMS, "operations can only run agents.run or interop.run")
        payload = params.get("params")
        if not isinstance(payload, dict):
            raise RpcError(INVALID_PARAMS, "params must be an object")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > MAX_PARAMS_BYTES:
                raise RpcError(INVALID_PARAMS, "operation params are too large")
            payload = json.loads(encoded)
        except (TypeError, ValueError, RecursionError):
            raise RpcError(INVALID_PARAMS, "operation params must be finite JSON values") from None
        if method == "agents.run":
            from machina.gateway.features.agents import _steps

            steps = _steps(payload)
            for agent, task in steps:
                _str({"agent": agent}, "agent", max_len=100)
                _str({"task": task}, "task", max_len=20_000)
            if payload.get("mode", "sequential") not in ("sequential", "parallel"):
                raise RpcError(INVALID_PARAMS, "mode must be 'sequential' or 'parallel'")
            if not payload.get("sessionId"):
                raise RpcError(INVALID_PARAMS, "sessionId is required for agents.run operations")
        else:
            from machina.gateway.features.interop import (
                MAX_RUN_PROMPT_CHARS,
                _platform,
                _run_timeout,
            )

            _platform(_str(payload, "platform", max_len=32))
            _str(payload, "prompt", max_len=MAX_RUN_PROMPT_CHARS)
            _run_timeout(payload)
        session_id = None
        if payload.get("sessionId") is not None:
            session_id = _uuid(payload, "sessionId")
            payload["sessionId"] = str(session_id)
            self._gateway.require_db()
            session = await self._gateway.get_session(payload)
            if method == "interop.run":
                from machina.gateway.features.interop import _workspace_cwd

                _workspace_cwd(session)
        return method, payload, session_id

    async def start(self, params: dict[str, Any]) -> dict[str, Any]:
        # The lock also covers DB-backed session validation/claim acquisition:
        # concurrent transport callers cannot both observe the free slot.
        async with self._start_lock:
            if self._closed or self._gateway.closing:
                raise RpcError(OPERATION_FAILED, "gateway is shutting down")
            method, payload, session_id = await self._validate(params)
            if self._closed or self._gateway.closing:
                raise RpcError(OPERATION_FAILED, "gateway is shutting down")
            if any(item.active for item in self._items.values()):
                raise RpcError(TURN_IN_PROGRESS, "a background operation is already running")
            if session_id is not None and self._gateway.turn_running(session_id):
                raise RpcError(TURN_IN_PROGRESS, "a turn is already running for this session")
            operation_id = str(_uuid(params, "id")) if "id" in params else uuid.uuid4().hex
            if operation_id in self._items:
                raise RpcError(INVALID_PARAMS, "operation id has already been used")
            token = None
            if session_id is not None:
                from machina.core.turns import try_begin_turn

                token = await try_begin_turn(session_id, turn_id=f"operation-{operation_id}")
                if token is None:
                    raise RpcError(TURN_IN_PROGRESS, "a turn is already running for this session")
                if self._closed or self._gateway.closing:
                    from machina.core.turns import end_turn

                    await end_turn(session_id, token)
                    raise RpcError(OPERATION_FAILED, "gateway is shutting down")
            snapshot = {
                "id": operation_id,
                "method": method,
                "status": "running",
                "cancellable": True,
                "startedAt": datetime.now(UTC).isoformat(),
            }
            if session_id is not None:
                snapshot["sessionId"] = str(session_id)
            operation = _Operation(snapshot, session_id, token)
            while len(self._items) >= MAX_RETAINED_OPERATIONS:
                oldest = next(key for key, item in self._items.items() if not item.active)
                self._items.pop(oldest)
            self._items[operation_id] = operation
            operation.task = asyncio.create_task(
                self._run(operation, payload), name=f"gateway-operation-{operation_id}"
            )
            return self._snapshot(operation)

    def _find(self, params: dict[str, Any]) -> _Operation:
        operation_id = _str(params, "id", max_len=64)
        operation = self._items.get(operation_id)
        if operation is None:
            raise RpcError(NOT_FOUND, "operation not found")
        if params.get("sessionId") is not None:
            if _uuid(params, "sessionId") != operation.session_id:
                raise RpcError(UNAUTHORIZED, "operation belongs to another session")
        return operation

    @staticmethod
    def _snapshot(operation: _Operation) -> dict[str, Any]:
        # Do not expose the retained mutable result to transport adapters.
        return json.loads(json.dumps(operation.snapshot, ensure_ascii=False))

    def get(self, params: dict[str, Any]) -> dict[str, Any] | None:
        missing_ok = _bool(params, "missingOk")
        try:
            operation = self._find(params)
        except RpcError as error:
            if missing_ok and error.code == NOT_FOUND:
                return None
            raise
        return self._snapshot(operation)

    def _request_cancel(self, operation: _Operation) -> None:
        if (
            not operation.active
            or not operation.snapshot["cancellable"]
            or operation.cancel_requested
        ):
            return
        operation.cancel_requested = True
        operation.cancel_event.set()
        operation.snapshot["status"] = "cancelling"
        # A Task cancelled before its coroutine enters never runs finally.
        # In that case _run observes the flag and retires the claim itself.
        if operation.entered:
            assert operation.task is not None
            operation.task.cancel()

    def cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        operation = self._find(params)
        self._request_cancel(operation)
        return self._snapshot(operation)

    async def _renew(self, operation: _Operation) -> None:
        from machina.core.turns import renew_turn

        while True:
            await asyncio.sleep(CLAIM_RENEW_INTERVAL_SECONDS)
            try:
                owned = await renew_turn(operation.session_id, operation.token)
            except Exception:
                owned = False
            if not owned:
                operation.ownership_error = "operation lost ownership of its parent session"
                self._request_cancel(operation)
                return

    async def _check_approval_owner(self, operation: _Operation) -> None:
        from machina.core.turns import owns_claim

        if operation.cancel_requested:
            raise asyncio.CancelledError
        if not await owns_claim(operation.session_id, operation.token):
            operation.ownership_error = "operation lost ownership of its parent session"
            self._request_cancel(operation)
            raise asyncio.CancelledError

    async def _approve(self, operation: _Operation, card: dict[str, Any]) -> str:
        """Park one owned child action; the broker retains executable authority."""
        from machina.core.orchestrator import Orchestrator
        from machina.core.session import session_manager
        from machina.memory.redaction import redact_value
        from machina.permissions import store
        from machina.permissions.broker import ApprovalDenied
        from machina.protocol import approval_fields

        request_id = str(card.get("request_id") or "")
        turn_id = f"operation-{operation.snapshot['id']}"
        try:
            child_id = uuid.UUID(str(card.get("session_id") or ""))
            child = await session_manager.get_fresh(child_id)
            agent_id = str(card.get("agent") or "")
            if (
                child is None
                or child.agent_id != agent_id
                or child.is_fork
                or child.status != "active"
                or child.id == operation.session_id
                or card.get("turn_id") != turn_id
            ):
                raise ValueError("approval does not belong to this operation's child")
            ancestor = child
            for _ in range(Orchestrator.MAX_HOP_COUNT + 1):
                if ancestor.is_fork:
                    raise ValueError("approval does not belong to this operation's child")
                if ancestor.parent_session_id == operation.session_id:
                    break
                if ancestor.parent_session_id is None:
                    raise ValueError("approval does not belong to this operation's child")
                ancestor = await session_manager.get_fresh(ancestor.parent_session_id)
                if ancestor is None:
                    raise ValueError("approval child ancestry is unavailable")
            else:
                raise ValueError("approval child ancestry exceeds the delegation limit")
            record = await store.get_approval(request_id)
            if (
                record is None
                or record.get("session_id") != str(child_id)
                or record.get("agent_id") != agent_id
                or record.get("turn_id") != turn_id
                or record.get("status") != "pending"
            ):
                raise ValueError("approval origin is unavailable")
            proposal = redact_value(approval_fields(card))
            review = {
                "requestId": request_id,
                "sessionId": str(child_id),
                "agentId": agent_id,
                "operation": proposal["operation"],
                "target": proposal["target"],
                "argv": proposal["argv"],
                "cwd": proposal["cwd"],
                "proposal": proposal,
            }
            # Claim checks may yield to another child's approver. Admit only
            # after that await, then measure and publish without yielding so
            # parallel cards cannot race past the shared snapshot budget.
            await self._check_approval_owner(operation)
            if len(json.dumps(review, ensure_ascii=False).encode("utf-8")) > MAX_APPROVAL_BYTES:
                raise ValueError("approval review exceeds the operation size limit")
            if len(operation.snapshot.get("approvals", [])) >= MAX_PENDING_APPROVALS:
                raise ValueError("too many pending operation approvals")
            if request_id in self._gateway._pending_approvals:
                raise ValueError("approval already has a live owner")
            pending_reviews = [*operation.snapshot.get("approvals", []), review]
            wire_bytes = json.dumps(pending_reviews, ensure_ascii=True, allow_nan=False).encode(
                "utf-8"
            )
            if len(wire_bytes) > MAX_PENDING_APPROVAL_BYTES:
                raise ValueError("pending approval reviews exceed the operation size limit")
        except (TypeError, ValueError) as error:
            await store.resolve_decision(request_id, "cancelled", principal="tui")
            raise ApprovalDenied(str(error)) from None

        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        entry = (future, turn_id, str(child_id))
        self._gateway._pending_approvals[request_id] = entry
        operation.snapshot.setdefault("approvals", []).append(review)
        try:
            verdict = await future
            await self._check_approval_owner(operation)
            return verdict
        finally:
            if self._gateway._pending_approvals.get(request_id) is entry:
                self._gateway._pending_approvals.pop(request_id, None)
            operation.snapshot["approvals"] = [
                pending
                for pending in operation.snapshot.get("approvals", [])
                if pending["requestId"] != request_id
            ]

    async def _run(self, operation: _Operation, params: dict[str, Any]) -> None:
        from machina.gateway.features.interop import _operation_cancel
        from machina.permissions.broker import set_approval_handler

        operation.entered = True
        cancel_context = _operation_cancel.set(operation.cancel_event)
        renewal = None
        live_approvals = operation.snapshot["method"] == "agents.run"
        turn_id = f"operation-{operation.snapshot['id']}"
        outcome: dict[str, Any]
        try:
            if operation.cancel_requested:
                raise asyncio.CancelledError
            if operation.session_id is not None:
                renewal = asyncio.create_task(self._renew(operation))
            if live_approvals:
                set_approval_handler(
                    lambda card: self._approve(operation, card),
                    principal="tui",
                    turn_id=turn_id,
                )
            result = await self._gateway._methods[operation.snapshot["method"]](params)
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
                outcome = {
                    "status": "failed",
                    "truncated": True,
                    "error": "Operation finished, but its result exceeded the retention byte limit.",
                }
            else:
                outcome = {"status": "completed", "result": json.loads(encoded)}
        except asyncio.CancelledError:
            outcome = {"status": "cancelled"}
        except Exception as error:
            outcome = {"status": "failed", "error": _diagnostic(error)}
        finally:
            if live_approvals:
                set_approval_handler(None)
                self._gateway.cancel_turn_approvals(turn_id)
                operation.snapshot.pop("approvals", None)
            _operation_cancel.reset(cancel_context)
            if renewal is not None:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
            if operation.session_id is not None:
                from machina.core.turns import end_turn

                try:
                    await end_turn(operation.session_id, operation.token)
                except Exception:
                    # Keep the durable owner token fenced until stale-claim
                    # recovery rather than pretending an uncertain release.
                    logger.warning("operation parent claim release failed")
        if operation.ownership_error is not None:
            outcome = {"status": "failed", "error": operation.ownership_error}
        operation.snapshot.update(outcome)

    async def close(self, timeout: float) -> None:
        self._closed = True
        active = [item for item in self._items.values() if item.active]
        for item in active:
            self._request_cancel(item)
        tasks = [item.task for item in active if item.task is not None]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0, timeout))
            if pending:
                # Keep strong references and honest running/cancelling states.
                # In particular, never cancel an interop to_thread await and
                # lose the only observer of the still-running process.
                logger.warning(
                    "%d gateway operation(s) still owned after close deadline", len(pending)
                )
