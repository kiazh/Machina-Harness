"""Approval-gated execution of one bounded external harness task.

Routing stays local in :mod:`machina.core.meta_harness`; this tool is the only
place that spawns an external agent harness. The permission broker sees the
``exec`` and ``delegate`` effects, so normal approval policy still applies.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from machina import interop
from machina.core.exceptions import ToolError
from machina.core.meta_harness import route_prompt
from machina.tools.base import registry
from machina.workspace.service import active_workspace_service

_MAX_TASK_CHARS = 20_000
_MIN_TIMEOUT = 15
_MAX_TIMEOUT = 900
_DEFAULT_TIMEOUT = 120


@registry.register(
    name="harness_delegate",
    description=(
        "Run one bounded task in an installed external agent harness. Omit "
        "platform to let the local meta-harness router choose from OpenCode, "
        "Codex, and Hermes. Requires normal execution approval."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The complete task or prompt for the external harness",
            },
            "platform": {
                "type": "string",
                "description": "Optional harness key: opencode, codex, or hermes",
            },
            "timeout": {
                "type": "integer",
                "minimum": _MIN_TIMEOUT,
                "maximum": _MAX_TIMEOUT,
                "description": "Deadline in seconds; defaults to 120",
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    },
    effects=("exec", "delegate"),
)
async def harness_delegate(
    task: str,
    platform: str = "",
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Route and execute one external harness task through the bounded runner."""
    text = str(task or "").strip()
    if not text:
        raise ToolError("task must not be empty")
    if len(text) > _MAX_TASK_CHARS:
        raise ToolError(f"task must be at most {_MAX_TASK_CHARS} characters")
    try:
        seconds = int(timeout)
    except (TypeError, ValueError):
        raise ToolError("timeout must be an integer number of seconds") from None
    if not _MIN_TIMEOUT <= seconds <= _MAX_TIMEOUT:
        raise ToolError(f"timeout must be between {_MIN_TIMEOUT} and {_MAX_TIMEOUT} seconds")

    supported = interop.supported_runs()
    requested = str(platform or "").strip().lower()
    routed = False
    run_text = text
    route_payload: dict = {}
    if requested:
        if requested not in supported:
            choices = ", ".join(sorted(supported))
            raise ToolError(f"unsupported harness {requested!r}; choose one of: {choices}")
    else:
        route = route_prompt(text)
        route_payload = {
            "primary": route.primary,
            "mode": route.mode,
            "confidence": round(route.confidence, 4),
            "steps": [step.to_dict() for step in route.steps],
        }
        routed = True
        if route.mode != "external" or route.primary == "machina":
            raise ToolError(
                "the local router found no reliable external harness route; "
                "call harness_delegate with an explicit platform if you want to override it"
            )
        requested = route.primary
        if requested not in supported:
            raise ToolError(f"routed harness {requested!r} has no headless run command")
        if len(route.steps) > 1:
            external_steps = [step for step in route.steps if step.mode == "external"]
            selected = max(external_steps, key=lambda step: step.confidence)
            run_text = selected.intent

    service = active_workspace_service()
    identity = service.identity if service is not None else None
    cwd = Path(identity.resolved_root) if identity is not None else None
    result = await asyncio.to_thread(
        interop.run_task,
        requested,
        run_text,
        timeout=float(seconds),
        cwd=cwd,
    )
    payload = {
        "platform": result.platform,
        "command": result.command,
        "exit_code": result.exit_code,
        "ok": result.ok,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "unsupported_reason": result.unsupported_reason,
        "routed": routed,
        "selected_intent": run_text if routed else None,
        "route": route_payload,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
