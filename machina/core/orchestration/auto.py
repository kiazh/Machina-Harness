"""Harness-level auto-delegation for explicitly parallel user requests."""

from __future__ import annotations

import uuid
from typing import Any

from machina.core.config import config
from machina.core.orchestration.executor import run_plan
from machina.core.orchestration.plan import DelegationPlan, fallback_plan
from machina.core.orchestration.planner import plan_parallel_tasks
from machina.core.routing import build_inventory, detect_parallel_intent, route_from_session
from machina.observability.diagnostics import record_failure


def _workspace() -> str:
    try:
        from machina.permissions.policy import workspace_root

        return str(workspace_root())
    except Exception:
        return ""


def _gate_reason(agent: Any) -> str:
    if bool(getattr(agent, "delegated", False)):
        return "child_agent"
    try:
        auto_parallel = config.get("auto_parallel_delegation")
        if str(True if auto_parallel is None else auto_parallel).lower() in {
            "0",
            "false",
            "no",
            "off",
        }:
            return "disabled"
        mode = str(config.get("orchestration_mode") or "single_model").lower()
        if mode == "single_model" and not bool(config.get("single_model_auto_parallel")):
            return "single_model_mode"
        if mode not in {"single_model", "multi_model"}:
            return "invalid_mode"
    except Exception:
        return "config_unavailable"
    allowed = getattr(agent, "allowed_tools", None)
    if allowed is not None and "delegate" not in allowed:
        return "delegate_not_allowed"
    if not hasattr(agent, "_call_llm_with_retry") or not hasattr(agent, "_finish_turn_completion"):
        return "agent_contract_missing"
    return "allowed"


async def _supervisor_synthesis(
    agent: Any,
    session_id: uuid.UUID,
    decision: Any,
    summary: str,
) -> tuple[str | None, int]:
    """Use the selected supervisor route for a bounded final synthesis.

    The supervisor sees only the worker reports, not a fabricated transcript.
    Any failure returns ``None`` so the caller keeps the deterministic summary.
    """
    if decision is None or decision.supervisor.transport not in {"native", "proxy"}:
        return None, 0
    provider = None
    try:
        from machina.core.provider import get_provider
        from machina.core.usage import usage_store

        provider = get_provider(
            provider=decision.supervisor.profile.provider,
            model=decision.supervisor.profile.model,
        )
        response = await usage_store.complete_call(
            provider,
            session_id,
            getattr(agent, "agent_id", "harness"),
            [
                {
                    "role": "system",
                    "content": (
                        "You are the supervisor for a completed parallel plan. "
                        "Use only the worker reports below. Give a concise final "
                        "answer: completed work, evidence, failures or approvals "
                        "still needed. Do not claim a file or test you cannot see "
                        "in the reports. Do not invent new work."
                    ),
                },
                {"role": "user", "content": summary[:24_000]},
            ],
            tools=[],
            max_tokens=1600,
            temperature=0.2,
        )
        content = str(getattr(response, "content", "") or "").strip()
        usage = getattr(response, "usage", {}) or {}
        tokens = int(usage.get("total_tokens", 0) or 0)
        return (content or None), tokens
    except Exception as error:
        record_failure("orchestration.auto.supervisor", error)
        return None, 0
    finally:
        if provider is not None:
            try:
                await provider.close()
            except Exception:
                pass


async def maybe_auto_parallel(
    agent: Any,
    session: Any,
    session_id: uuid.UUID,
    user_message: str,
    pending_chunks: list[dict[str, Any]],
    *,
    orchestrator: Any = None,
) -> Any | None:
    """Run the deterministic parallel path or return ``None`` for normal ReAct.

    The gate is intentionally narrow: only explicit parallel language in
    multi-model mode. Small/serial turns keep the normal loop and its lower
    cost. The planner produces a structured plan; validation and execution are
    harness-owned, so a serial model response cannot cancel the requested
    fan-out.
    """
    gate_reason = _gate_reason(agent)
    parallel_requested = detect_parallel_intent(user_message)
    if gate_reason != "allowed" or not parallel_requested:
        if parallel_requested:
            from machina.core.provider import audit_log

            audit_log(
                "auto_parallel_skipped",
                session_id=str(session_id),
                reason=gate_reason,
                delegated=bool(getattr(agent, "delegated", False)),
                allowed_tools=getattr(agent, "allowed_tools", None),
            )
        return None
    workspace = _workspace()
    planner_tokens = 0
    inventory = build_inventory(config)
    decision = None
    if str(config.get("model_selection") or "auto").lower() != "pinned":
        try:
            decision = route_from_session(
                session,
                user_message,
                inventory=inventory,
                config=config,
                mode=str(config.get("orchestration_mode") or "multi_model"),
                worker_count=1,
            )
        except Exception as error:
            record_failure("orchestration.auto.route", error)
    planner_provider = None
    owns_planner = False
    if decision is not None and decision.planner.transport in {"native", "proxy"}:
        try:
            from machina.core.provider import get_provider

            planner_provider = get_provider(
                provider=decision.planner.profile.provider,
                model=decision.planner.profile.model,
            )
            owns_planner = True
        except Exception as error:
            record_failure("orchestration.auto.planner_provider", error)
            planner_provider = None

    async def complete(messages, tools, **kwargs):
        nonlocal planner_tokens, planner_provider, owns_planner
        max_tokens = int(kwargs.get("max_tokens") or 2200)
        temperature = float(kwargs.get("temperature") or 0.2)
        if planner_provider is not None:
            from machina.core.usage import usage_store

            try:
                response = await usage_store.complete_call(
                    planner_provider,
                    session_id,
                    getattr(agent, "agent_id", "harness"),
                    messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except Exception as error:
                # A planner route that cannot start must not prevent the
                # explicit parallel fan-out; use the parent's route once.
                record_failure("orchestration.auto.planner_call", error)
                if owns_planner:
                    try:
                        await planner_provider.close()
                    except Exception:
                        pass
                planner_provider = None
                owns_planner = False
                response = await agent._call_llm_with_retry(
                    messages,
                    tools,
                    session_id,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
        else:
            response = await agent._call_llm_with_retry(
                messages,
                tools,
                session_id,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        usage = getattr(response, "usage", {}) or {}
        planner_tokens += int(usage.get("total_tokens", 0) or 0)
        return response

    try:
        requested_workers = int(config.get("worker_count") or 0)
    except (TypeError, ValueError):
        requested_workers = 0
    planner_task_cap = max(8, min(requested_workers, 64)) if requested_workers else 8
    plan: DelegationPlan | None = None
    try:
        plan = await plan_parallel_tasks(
            complete,
            user_message,
            workspace=workspace,
            max_tasks=planner_task_cap,
        )
    except Exception as error:
        record_failure("orchestration.auto.plan", error)
        plan = fallback_plan(user_message)
    finally:
        if owns_planner and planner_provider is not None:
            try:
                await planner_provider.close()
            except Exception:
                pass
    if plan is None:
        from machina.core.provider import audit_log

        audit_log("auto_parallel_no_plan", session_id=str(session_id))
        return None
    from machina.core.provider import audit_log

    audit_log(
        "auto_parallel_start",
        session_id=str(session_id),
        tasks=len(plan.tasks),
        planner=(
            f"{decision.planner.profile.provider}/{decision.planner.profile.model}"
            if decision is not None
            else "parent"
        ),
        strategy=plan.strategy,
    )
    try:
        from machina.core.agent_factory import parent_authority_var

        authority = dict(parent_authority_var.get() or {})
    except Exception:
        authority = {}
    try:
        from machina.core.orchestrator import orchestrator as default_orchestrator

        active_orchestrator = orchestrator or default_orchestrator
        result = await run_plan(
            plan,
            parent_session_id=session_id,
            from_agent=getattr(agent, "agent_id", "harness"),
            authority=authority,
            config=config,
            inventory=inventory,
            parent_provider=getattr(session, "provider", None),
            parent_model=getattr(session, "model", None),
            orchestrator=active_orchestrator,
        )
    except Exception as error:
        record_failure("orchestration.auto.execute", error)
        from machina.core.provider import audit_log

        audit_log(
            "auto_parallel_error",
            session_id=str(session_id),
            error_type=type(error).__name__,
        )
        return None
    tool_calls: list[dict[str, Any]] = []
    paused = False
    for outcome in result.outcomes:
        pending = outcome.status in {"waiting_approval", "paused"}
        paused = paused or pending
        entry = {
            "tool": "delegate",
            "args": {
                "agent": outcome.agent,
                "task": plan.task(outcome.task_id).task,
            },
            "result": outcome.response or outcome.error,
            "outcome": "paused" if pending else "failed" if outcome.failed else "success",
            "delegated_tokens": outcome.tokens,
            "delegation_status": outcome.status,
            "child_session_id": outcome.session_id,
        }
        if outcome.needs_approval:
            entry["needs_approval"] = outcome.needs_approval
        tool_calls.append(entry)
    content = result.summary()
    supervisor_tokens = 0
    if str(config.get("orchestration_mode") or "").lower() == "multi_model":
        synthesis, supervisor_tokens = await _supervisor_synthesis(
            agent,
            session_id,
            decision,
            content,
        )
        if synthesis:
            content = synthesis
    total_tokens = planner_tokens + result.tokens + supervisor_tokens
    return await agent._finish_turn_completion(
        session_id,
        user_message,
        content,
        tool_calls,
        total_tokens,
        1,
        pending_chunks,
        outcome="paused" if paused else "completed",
    )


__all__ = ["maybe_auto_parallel"]
