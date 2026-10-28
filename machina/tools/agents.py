"""Multi-agent tools: delegate a task to another agent, or list agents."""

from __future__ import annotations

import asyncio
import difflib
import logging
import uuid
from contextvars import ContextVar

from machina.core.config import config
from machina.core.exceptions import DelegationAccountingError, ToolError
from machina.db.connection import db
from machina.memory.redaction import redact_diagnostic
from machina.observability.diagnostics import record_failure
from machina.tools.base import registry

logger = logging.getLogger(__name__)

# Set only while the current agent executes a tool. ContextVar keeps concurrent
# sessions isolated and avoids accepting a model-supplied parent session ID.
current_session_id: ContextVar[uuid.UUID | None] = ContextVar("agent_session_id", default=None)
current_agent_id: ContextVar[str | None] = ContextVar("agent_tool_owner_id", default=None)
_background_delegations: set[asyncio.Task] = set()


class DelegationToolResult(str):
    """Readable tool output carrying typed child pauses and cumulative spend."""

    def __new__(
        cls,
        text: str,
        *,
        needs_approval: list[dict],
        tokens_used: int,
        delegation_status: str = "complete",
    ):
        result = super().__new__(cls, text)
        result.needs_approval = needs_approval
        result.tokens_used = tokens_used
        result.delegation_status = delegation_status
        return result


async def active_agent_scope() -> tuple[str, uuid.UUID]:
    """Derive a tool caller from the executing agent and its owned session."""
    session_id = current_session_id.get()
    agent_id = current_agent_id.get()
    if session_id is None or agent_id is None:
        raise ToolError("tool requires an active agent session")
    session_owner = await db.fetchval("SELECT agent_id FROM sessions WHERE id = $1", session_id)
    if session_owner != agent_id:
        raise ToolError("active session does not belong to the running agent")
    return agent_id, session_id


@registry.register(
    name="delegate",
    description=(
        "Hand a self-contained task to another agent and get its result. Use for work "
        "that suits a specialist (e.g. 'researcher' to gather info, 'coder' to edit code). "
        "Independent subtasks fan out: issue one delegate call per subtask in a single "
        "block and they run in parallel up to the configured concurrency limit; "
        "additional calls queue. "
        "Use list_agents with a task keyword only when the built-in roles do not fit. "
        "The result includes a child session id; pass it as session_id for a follow-up."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Name of the agent to delegate to"},
            "task": {
                "type": "string",
                "description": "A complete, standalone description of the task",
            },
            "expected_output": {
                "type": "string",
                "description": "Free-text description of the required result shape",
            },
            "expected_artifact_path": {
                "type": "string",
                "description": "Workspace-relative file the child must produce",
            },
            "session_id": {
                "type": "string",
                "description": "Optional child session UUID from an earlier delegate result for a follow-up",
            },
            "wait_seconds": {
                "type": "integer",
                "minimum": 0,
                "maximum": 3600,
                "description": "Optional inline wait override; 0 waits until completion",
            },
        },
        "required": ["agent", "task"],
    },
)
async def delegate(
    agent: str,
    task: str,
    expected_output: str | None = None,
    expected_artifact_path: str | None = None,
    session_id: str | None = None,
    wait_seconds: int | None = None,
) -> str:
    """Delegate *task* to *agent* and return its response and child id."""
    from machina.core.orchestrator import (
        AgentNotFoundError,
        delegation_depth,
        orchestrator,
    )

    if not task.strip():
        raise ToolError("task must not be empty")
    # Zero-cost name validation before any child/session/provider work.
    # Unknown names (e.g. skill-like ecc-* hallucinations) fail here with
    # fuzzy suggestions; no session is created and no provider is touched.
    # The roster is DB-free (builtin + file definitions); harness-<slug>
    # profile scopes are valid targets resolved via _profile_scope.
    try:
        from machina.core.agent_def import frozen_roster

        _names = frozen_roster()
    except Exception as _boundary_error:
        # Optional fallback preserves the primary outcome; report no payload.
        record_failure("tools.delegate.roster", _boundary_error)
        _names = []
    try:
        from machina.memory.user_profile import PROFILE_ID_RE

        _scope_pattern = rf"harness-({PROFILE_ID_RE})"
    except Exception:
        _scope_pattern = r"harness-([a-z0-9][a-z0-9-]{0,39})"
    try:
        import re as _scope_re

        _is_profile_scope = bool(_scope_re.fullmatch(_scope_pattern, agent or ""))
        if agent not in _names and not _is_profile_scope:
            # DB-stored custom agents are not in the frozen roster: one
            # lookup before rejecting so valid saved agents still delegate.
            # Hallucinated names still fail here with zero child work.
            try:
                from machina.core.agent_def import agent_registry as _registry

                _known = await _registry.get(agent)
            except Exception as _boundary_error:
                # Optional fallback preserves the primary outcome; report no payload.
                record_failure("tools.delegate.registry", _boundary_error)
                _known = None
            if _known is None:
                _suggestions = difflib.get_close_matches(agent, _names, n=3, cutoff=0.5)
                _msg = f"no agent named {agent!r}."
                if _suggestions:
                    _msg += f" Did you mean: {', '.join(_suggestions)}?"
                _msg += " Use list_agents to see available agents."
                raise ToolError(_msg)
    except ToolError:
        raise
    except Exception as _e:
        raise ToolError(f"delegation failed: {_e}") from None
    # S1 contract params (dspy-Signature discipline: typed optional inputs,
    # explicit outputs). Validation is pre-launch: zero child work on escape.
    if expected_artifact_path is not None:
        if not isinstance(expected_artifact_path, str) or not expected_artifact_path.strip():
            raise ToolError("expected_artifact_path must be a non-empty string")
        try:
            from machina.tools.file import resolve_path as _resolve_contract_path

            _resolve_contract_path(expected_artifact_path)
        except ValueError as _e:
            raise ToolError(f"{_e}") from _e
    resume_session_id = None
    if session_id is not None:
        try:
            resume_session_id = uuid.UUID(session_id)
        except (ValueError, TypeError):
            raise ToolError("session_id must be a child session UUID") from None
    parent_session_id = current_session_id.get()
    from_agent = "delegate-tool"
    if parent_session_id is not None:
        from_agent, parent_session_id = await active_agent_scope()
    # LP-08: propagate the executing parent's authority so the child cannot
    # escalate through delegation, HTTP/RPC, jobs, or raw helper calls.
    try:
        from machina.core.agent_factory import parent_authority_var

        parent_authority = parent_authority_var.get()
    except Exception as _boundary_error:
        # Optional fallback preserves the primary outcome; report no payload.
        record_failure("tools.delegate.parent_authority", _boundary_error)
        parent_authority = None
    try:
        # Optional output-contract kwargs are forwarded only when set, so
        # contract-free calls keep the exact legacy call shape.
        _contract_kwargs: dict = {}
        if expected_output is not None:
            _contract_kwargs["expected_output"] = expected_output
        if expected_artifact_path is not None:
            _contract_kwargs["expected_artifact_path"] = expected_artifact_path
        if resume_session_id is not None:
            _contract_kwargs["resume_session_id"] = resume_session_id
        child_started = asyncio.get_running_loop().create_future()

        def on_started(child_session_id: uuid.UUID) -> None:
            if not child_started.done():
                child_started.set_result(child_session_id)

        _contract_kwargs["on_started"] = on_started
        operation = asyncio.create_task(
            orchestrator.delegate(
                agent,
                task,
                from_agent=from_agent,
                parent_session_id=parent_session_id,
                _hop_count=delegation_depth.get() + 1,
                authority=parent_authority,
                **_contract_kwargs,
            )
        )
        _background_delegations.add(operation)

        def forget(completed: asyncio.Task) -> None:
            _background_delegations.discard(completed)
            if not completed.cancelled():
                error = completed.exception()
                if error is not None:
                    record_failure("tools.delegate.background", error)

        operation.add_done_callback(forget)
        ready, _ = await asyncio.wait(
            {operation, child_started}, return_when=asyncio.FIRST_COMPLETED, timeout=30
        )
        if operation in ready:
            result = await operation
        elif child_started in ready:
            child_id = child_started.result()
            try:
                configured_wait = (
                    wait_seconds
                    if wait_seconds is not None
                    else config.get("delegate_inline_wait_seconds")
                )
                wait_limit = max(
                    0, min(int(120 if configured_wait is None else configured_wait), 3600)
                )
            except (TypeError, ValueError):
                wait_limit = 120
            if wait_limit:
                done, _ = await asyncio.wait({operation}, timeout=wait_limit)
                if not done:
                    try:
                        history = await orchestrator.history(parent_session_id, limit=500)
                        entry = next(
                            (
                                item
                                for item in history
                                if item.get("childSessionId") == str(child_id)
                            ),
                            None,
                        )
                    except Exception as error:
                        record_failure("tools.delegate.pending_status", error)
                        entry = None
                    status = entry.get("status", "running") if entry else "running"
                    receipt = DelegationToolResult(
                        f"[{agent} {status}]\nChild session: {child_id}\n"
                        "Use delegate_status with this child session ID to check progress; "
                        "use delegate with session_id after it finishes.",
                        needs_approval=[],
                        tokens_used=0,
                        delegation_status=status,
                    )
                    receipt.is_error = False
                    return receipt
            result = await operation
        else:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            raise ToolError("delegated worker did not finish setup within 30 seconds")
    except AgentNotFoundError as e:
        raise ToolError(f"{e}. Use list_agents to see available agents.") from None
    except DelegationAccountingError:
        # A parent must not continue spending when its child's usage is unknown.
        raise
    except Exception as e:
        logger.exception("delegate tool failed")
        raise ToolError(f"delegation failed: {redact_diagnostic(e)}") from None

    label = (
        "failed"
        if result.status in ("error", "failed")
        else "waiting for approval"
        if result.status == "waiting_approval"
        else "responded"
    )
    receipt = DelegationToolResult(
        f"[{agent} {label}]\nChild session: {result.session_id}\n{result.response}",
        needs_approval=getattr(result, "needs_approval", []),
        tokens_used=getattr(result, "tokens", 0),
        delegation_status=result.status,
    )
    # Typed failure for the parent loop (never prose scanning): agent.py marks
    # tool outcome failed via getattr(result, "is_error").
    receipt.is_error = label == "failed"
    return receipt


@registry.register(
    name="delegate_status",
    description="Check whether one of your delegated workers is queued, running, finished, or interrupted.",
    parameters={
        "type": "object",
        "properties": {
            "child_session_id": {
                "type": "string",
                "description": "Child session ID returned by delegate",
            }
        },
        "required": ["child_session_id"],
    },
)
async def delegate_status(child_session_id: str) -> str:
    """Return durable status and the result for a child owned by this session."""
    from machina.core.orchestrator import _delegation_is_live, orchestrator

    _, parent_session_id = await active_agent_scope()
    try:
        child_id = str(uuid.UUID(child_session_id))
    except (ValueError, TypeError):
        raise ToolError("child_session_id must be a UUID") from None
    rows = await orchestrator.history(parent_session_id, limit=500)
    entry = next((item for item in rows if item.get("childSessionId") == child_id), None)
    if entry is None:
        raise ToolError("no delegated worker with that child session ID belongs to this session")
    status = entry["status"]
    response = str(entry.get("response") or "")
    if status in {"queued", "running", "pending"} and not _delegation_is_live(uuid.UUID(child_id)):
        active_claim = await db.fetchval(
            """SELECT EXISTS (
                   SELECT 1 FROM sessions
                   WHERE id = $1 AND claim_owner IS NOT NULL AND claim_expires_at > now()
               )""",
            uuid.UUID(child_id),
        )
        if not active_claim:
            await db.execute(
                """UPDATE agent_messages
                   SET status = 'interrupted',
                       response = COALESCE(response, 'Worker stopped before completing.'),
                       completed_at = COALESCE(completed_at, now())
                   WHERE id = $1 AND status IN ('queued', 'running', 'pending')""",
                uuid.UUID(entry["id"]),
            )
            status = "interrupted"
            response = (
                response
                or "Worker stopped before completing. Resume with delegate using this child session ID."
            )
    if status in {"queued", "running", "pending"}:
        return f"Worker {status}. Child session: {child_id}."
    return f"Worker {status}. Child session: {child_id}.\n{response[:12000]}"


@registry.register(
    name="list_agents",
    description="Find delegation specialists by task keyword. Without a query, show a short roster.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Optional task or specialty keyword"},
        },
    },
)
async def list_agents(query: str = "") -> str:
    """Return the available agents and what they are for."""
    from machina.core.agent_def import agent_registry

    agents = await agent_registry.list()
    if not agents:
        return "No agents are available."
    needle = query.strip().casefold()
    if needle:
        agents = [
            agent
            for agent in agents
            if needle in agent.name.casefold() or needle in agent.description.casefold()
        ]
        if not agents:
            return f"No agents match {query.strip()!r}."
    else:
        # The bounded default roster must still show the standard roles even
        # when an alphabetically sorted specialist catalog is much larger.
        core = {"harness": 0, "researcher": 1, "coder": 2}
        agents = sorted(agents, key=lambda agent: core.get(agent.name, 3))
    shown = agents[:12]
    lines = [f"- {agent.name}: {agent.description or '(no description)'}" for agent in shown]
    if len(agents) > len(shown):
        lines.append(
            f"{len(agents) - len(shown)} more agents; use list_agents with a task keyword."
        )
    return "\n".join(lines)


@registry.register(
    name="share_memory",
    description="Share one signed memory you own with another agent. Delivery checks provenance and the recipient's identity gate.",
    parameters={
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "Memory UUID from remember or recall"},
            "recipient_agent": {"type": "string", "description": "Agent to receive this memory"},
        },
        "required": ["memory_id", "recipient_agent"],
    },
)
async def share_memory(memory_id: str, recipient_agent: str) -> str:
    """Deliver a memory through the durable per-recipient identity gate."""
    from machina.memory.shared_bus import shared_memory_bus

    agent_id, _ = await active_agent_scope()
    try:
        source_id = uuid.UUID(memory_id)
    except ValueError:
        raise ToolError("memory_id must be a UUID") from None
    try:
        receipt = await shared_memory_bus.deliver(
            source_id, recipient_agent, publisher_agent=agent_id
        )
    except (ValueError, PermissionError) as exc:
        raise ToolError(str(exc)) from None
    return (
        f"Memory delivery {receipt.status} for {recipient_agent}; "
        f"receipt source={receipt.source_memory_id}; "
        f"target={receipt.target_memory_id or '-'}; {receipt.reason}"
    )


registry.declare_effects(
    {
        "delegate": ("delegate",),
        "list_agents": (),
        "share_memory": ("memory",),
    }
)
