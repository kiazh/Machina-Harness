"""Structured one-call planner for explicit parallel requests."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from machina.core.orchestration.plan import DelegationPlan, PlanError, parse_plan

PLAN_SYSTEM_PROMPT = """You are Machina's deterministic delegation planner.
Return ONLY a JSON object, no prose, with this shape:
{
  "reason": "why these tracks are independent",
  "strategy": "parallel",
  "tasks": [
    {
      "id": "short-kebab-id",
      "agent": "coder|researcher|harness",
      "role": "worker",
      "task": "complete standalone instructions for the worker",
      "expected_output": "what the worker must report",
      "expected_artifact_path": "optional workspace-relative file",
      "depends_on": ["other-task-id"]
    }
  ]
}

Rules:
- The user explicitly requested parallel/independent work. Produce at
  least TWO independent tracks, and no more than {max_tasks}.
- Use coder for code/file changes, researcher for read-only investigation,
  and harness for mixed work when appropriate.
- Each worker receives no memory of the other workers. Its task must be
  self-contained and include the workspace path when files are involved.
- Use workspace-relative paths; never assume a Unix /tmp location.
- Do not assign the same artifact path to two independent workers.
- Only use depends_on when a track truly cannot start before another.
- Do not include commentary outside the JSON object.
"""


def _content(response: Any) -> str:
    if isinstance(response, str):
        return response
    return str(getattr(response, "content", "") or "")


async def plan_parallel_tasks(
    complete: Callable[..., Awaitable[Any]],
    prompt: str,
    *,
    workspace: str = "",
    max_tasks: int = 8,
    max_tokens: int | None = None,
) -> DelegationPlan:
    """Ask the planner model for a structured plan and validate it."""
    bounded_tasks = max(2, min(int(max_tasks or 8), 64))
    token_budget = max_tokens or max(2200, min(bounded_tasks * 700, 16_000))
    user = (
        f"User request:\n{prompt.strip()}\n\n"
        f"Workspace root: {workspace or '(current workspace)'}\n"
        "Return the JSON delegation plan now."
    )
    response = await complete(
        [
            {
                "role": "system",
                "content": PLAN_SYSTEM_PROMPT.replace("{max_tasks}", str(bounded_tasks)),
            },
            {"role": "user", "content": user},
        ],
        [],
        max_tokens=token_budget,
        temperature=0.2,
    )
    return parse_plan(_content(response), max_tasks=bounded_tasks)


def plan_to_json(plan: DelegationPlan) -> str:
    return json.dumps(plan.to_dict(), ensure_ascii=False, separators=(",", ":"))


__all__ = ["PLAN_SYSTEM_PROMPT", "PlanError", "plan_parallel_tasks", "plan_to_json"]
