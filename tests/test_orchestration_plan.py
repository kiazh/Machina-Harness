"""Plan validation and first-wave scheduler contracts."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from machina.core.orchestration.executor import PlanResult, TaskOutcome, run_plan
from machina.core.orchestration.plan import (
    DelegationPlan,
    PlanError,
    WorkerTask,
    fallback_plan,
    parse_plan,
    waves,
)
from machina.core.orchestration.planner import plan_parallel_tasks
from machina.core.routing import build_inventory


@dataclass
class FakeConfig:
    values: dict

    def get(self, key: str, default=None):
        return self.values.get(key, default)


def _config():
    return FakeConfig(
        {
            "provider": "openai",
            "model": "gpt-6.1-sol",
            "openai_api_key": "sk-test",
            "anthropic_api_key": "sk-ant",
            "max_delegate_concurrency": 8,
            "max_worker_routes": 4,
            "orchestration_mode": "multi_model",
            "worker_transport": "native",
        }
    )


def _inventory():
    return build_inventory(
        _config(),
        providers=("openai", "anthropic"),
        probe_kiro=False,
    )


def _plan(*tasks: WorkerTask) -> DelegationPlan:
    return DelegationPlan(reason="test", tasks=tuple(tasks))


@pytest.mark.asyncio
async def test_selected_supervisor_performs_bounded_synthesis(monkeypatch) -> None:
    from machina.core.orchestration import auto as auto_module
    from machina.core.usage import usage_store

    class Provider:
        closed = False

        async def close(self):
            self.closed = True

    provider = Provider()
    seen: dict = {}

    async def fake_complete(provider_arg, session_id, agent_id, messages, **kwargs):
        seen.update(messages=messages, kwargs=kwargs)
        return SimpleNamespace(content="supervised result", usage={"total_tokens": 5})

    monkeypatch.setattr("machina.core.provider.get_provider", lambda **kwargs: provider)
    monkeypatch.setattr(usage_store, "complete_call", fake_complete)
    decision = SimpleNamespace(
        supervisor=SimpleNamespace(
            transport="native",
            profile=SimpleNamespace(provider="test", model="supervisor-1"),
        )
    )

    content, tokens = await auto_module._supervisor_synthesis(
        SimpleNamespace(agent_id="harness"),
        uuid.uuid4(),
        decision,
        "worker reports",
    )

    assert content == "supervised result"
    assert tokens == 5
    assert seen["messages"][1]["content"] == "worker reports"
    assert seen["kwargs"]["tools"] == []
    assert provider.closed is True


@pytest.mark.asyncio
async def test_planner_can_emit_twenty_tracks_when_configured() -> None:
    captured: dict = {}
    tasks = [
        {"id": f"t{index}", "agent": "coder", "task": f"independent task {index}"}
        for index in range(20)
    ]

    async def fake_complete(messages, tools, *, max_tokens, temperature):
        captured["system"] = messages[0]["content"]
        captured["max_tokens"] = max_tokens
        return SimpleNamespace(content=json.dumps({"tasks": tasks}))

    plan = await plan_parallel_tasks(
        fake_complete,
        "Do twenty independent tracks in parallel",
        max_tasks=20,
    )

    assert len(plan.tasks) == 20
    assert "no more than 20" in captured["system"]
    assert captured["max_tokens"] >= 14_000


def test_approval_pending_is_not_counted_as_completed() -> None:
    plan = _plan(
        WorkerTask(id="one", agent="coder", task="work one"),
        WorkerTask(id="two", agent="coder", task="work two"),
    )
    result = PlanResult(
        plan=plan,
        outcomes=(
            TaskOutcome(task_id="one", agent="coder", status="complete", response="ok"),
            TaskOutcome(
                task_id="two",
                agent="coder",
                status="waiting_approval",
                error="needs approval",
            ),
        ),
    )

    assert result.ok is False
    assert "1 completed" in result.summary()
    assert "1 awaiting approval" in result.summary()
    assert "two (coder, awaiting approval)" in result.summary()


def test_parallel_gate_reports_actionable_reasons(monkeypatch) -> None:
    from machina.core.orchestration import auto as auto_module

    class Agent:
        delegated = False
        allowed_tools = None

        def _call_llm_with_retry(self):
            pass

        def _finish_turn_completion(self):
            pass

    class ModeConfig:
        mode = "single_model"
        single_auto = False

        def get(self, key):
            if key == "auto_parallel_delegation":
                return True
            if key == "orchestration_mode":
                return self.mode
            if key == "single_model_auto_parallel":
                return self.single_auto
            return None

    monkeypatch.setattr(auto_module, "config", ModeConfig())
    assert auto_module._gate_reason(Agent()) == "single_model_mode"
    ModeConfig.single_auto = True
    assert auto_module._gate_reason(Agent()) == "allowed"
    ModeConfig.mode = "multi_model"
    Agent.allowed_tools = ["read_file"]
    assert auto_module._gate_reason(Agent()) == "delegate_not_allowed"
    Agent.allowed_tools = None
    assert auto_module._gate_reason(Agent()) == "allowed"

    class StrictConfig:
        """The live Config.get() takes exactly one argument."""

        def get(self, key):
            if key == "auto_parallel_delegation":
                return True
            if key == "orchestration_mode":
                return "multi_model"
            return None

    monkeypatch.setattr(auto_module, "config", StrictConfig())
    assert auto_module._gate_reason(Agent()) == "allowed"


def test_parse_plan_accepts_fenced_json_and_dependencies() -> None:
    content = """```json
    {"reason":"independent frontend/backend","tasks":[
      {"id":"frontend","agent":"coder","task":"Build the UI in src/ui","expected_artifact_path":"src/ui/index.html"},
      {"id":"tests","agent":"coder","task":"Write tests after the UI","depends_on":["frontend"]}
    ]}
    ```"""
    plan = parse_plan(content)

    assert len(plan.tasks) == 2
    assert plan.tasks[1].depends_on == ("frontend",)
    assert tuple(task.id for task in waves(plan)[0]) == ("frontend",)


def test_parse_plan_rejects_duplicate_artifact_ownership() -> None:
    content = json.dumps(
        {
            "tasks": [
                {
                    "id": "one",
                    "agent": "coder",
                    "task": "write a",
                    "expected_artifact_path": "src/index.html",
                },
                {
                    "id": "two",
                    "agent": "coder",
                    "task": "write b",
                    "expected_artifact_path": "src/index.html",
                },
            ]
        }
    )
    with pytest.raises(PlanError, match="duplicate artifact"):
        parse_plan(content)


def test_parse_plan_rejects_cycles_and_serial_parallel_plans() -> None:
    with pytest.raises(PlanError, match="cycle"):
        parse_plan(
            json.dumps(
                {
                    "tasks": [
                        {"id": "a", "agent": "coder", "task": "a", "depends_on": ["b"]},
                        {"id": "b", "agent": "coder", "task": "b", "depends_on": ["a"]},
                    ]
                }
            )
        )
    with pytest.raises(PlanError, match="fewer than two"):
        parse_plan(
            json.dumps(
                {"tasks": [{"id": "only", "agent": "coder", "task": "do it"}]}
            )
        )


def test_fallback_plan_only_splits_clear_named_tracks() -> None:
    plan = fallback_plan("Build the frontend page; write the API tests")
    assert plan is not None
    assert len(plan.tasks) == 2
    assert fallback_plan("Fix the failing test") is None


@pytest.mark.asyncio
async def test_auto_parallel_gate_plans_and_executes_without_model_tool_calls(monkeypatch) -> None:
    from machina.core.models import AgentResponse
    from machina.core.orchestration import auto as auto_module

    plan_json = json.dumps(
        {
            "reason": "independent UI and tests",
            "tasks": [
                {
                    "id": "ui",
                    "agent": "coder",
                    "task": "Build the UI page",
                    "expected_artifact_path": "src/index.html",
                },
                {
                    "id": "tests",
                    "agent": "coder",
                    "task": "Write the browser test",
                    "expected_artifact_path": "tests/ui.spec.js",
                },
            ],
        }
    )
    captured: dict = {}

    class FakeAgent:
        agent_id = "harness"
        delegated = False
        allowed_tools = None

        async def _call_llm_with_retry(self, messages, tools, session_id, **kwargs):
            return SimpleNamespace(content=plan_json, usage={"total_tokens": 12})

        async def _finish_turn_completion(
            self,
            session_id,
            user_message,
            content,
            tool_calls,
            tokens,
            iterations,
            pending,
            *,
            outcome="completed",
        ):
            captured.update(
                content=content,
                tool_calls=tool_calls,
                tokens=tokens,
                iterations=iterations,
                outcome=outcome,
            )
            return AgentResponse(content=content, tool_calls=tool_calls, tokens_used=tokens)

    class FakeOrchestrator:
        async def delegate(self, agent, task, **kwargs):
            return SimpleNamespace(
                status="complete",
                session_id=uuid.uuid4(),
                response=f"finished {task}",
                tokens=7,
            )

    class FakeConfig:
        values = {
            "auto_parallel_delegation": True,
            "orchestration_mode": "multi_model",
            "provider": "openai",
            "model": "gpt-6.1-sol",
            "openai_api_key": "sk-test",
            "max_delegate_concurrency": 8,
            "max_worker_routes": 4,
            "worker_transport": "native",
        }

        def get(self, key, default=None):
            return self.values.get(key, default)

    fake_config = FakeConfig()
    monkeypatch.setattr(auto_module, "config", fake_config)
    monkeypatch.setattr(auto_module, "build_inventory", lambda _config: _inventory())
    monkeypatch.setattr(
        auto_module,
        "route_from_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("planner unavailable")),
    )
    monkeypatch.setattr(
        "machina.core.orchestration.executor.build_inventory",
        lambda _config: _inventory(),
    )

    response = await auto_module.maybe_auto_parallel(
        FakeAgent(),
        SimpleNamespace(provider="openai", model="gpt-6.1-sol"),
        uuid.uuid4(),
        "Build the UI and write the browser test in parallel",
        [],
        orchestrator=FakeOrchestrator(),
    )

    assert response is not None
    assert "Parallel plan" in response.content
    assert captured["tokens"] == 12 + 14
    assert captured["outcome"] == "completed"
    assert len(captured["tool_calls"]) == 2
    assert all(call["tool"] == "delegate" for call in captured["tool_calls"])



@pytest.mark.asyncio
async def test_scheduler_launches_the_first_wave_together_and_passes_routes() -> None:
    starts: list[float] = []
    seen: list[dict] = []

    class FakeOrchestrator:
        async def delegate(self, agent, task, **kwargs):
            starts.append(time.monotonic())
            seen.append({"agent": agent, "task": task, **kwargs})
            await asyncio.sleep(0.02)
            return SimpleNamespace(
                status="complete",
                session_id=uuid.uuid4(),
                response=f"done: {task}",
                tokens=11,
            )

    plan = _plan(
        WorkerTask(id="ui", agent="coder", task="Build the UI page"),
        WorkerTask(id="api", agent="coder", task="Build the API tests"),
    )
    result = await run_plan(
        plan,
        parent_session_id=uuid.uuid4(),
        config=_config(),
        inventory=_inventory(),
        orchestrator=FakeOrchestrator(),
    )

    assert result.ok
    assert len(result.outcomes) == 2
    assert max(starts) - min(starts) < 0.1
    assert all(item["provider_override"] for item in seen)
    assert all(item["model_override"] for item in seen)
    assert result.tokens == 22


@pytest.mark.asyncio
async def test_pinned_model_selection_inherits_parent_route() -> None:
    seen: list[dict] = []

    class FakeOrchestrator:
        async def delegate(self, agent, task, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(
                status="complete",
                session_id=uuid.uuid4(),
                response="ok",
                tokens=1,
            )

    config = _config()
    config.values["model_selection"] = "pinned"
    plan = _plan(
        WorkerTask(id="one", agent="coder", task="one"),
        WorkerTask(id="two", agent="coder", task="two"),
    )
    result = await run_plan(
        plan,
        config=config,
        inventory=_inventory(),
        parent_provider="deepseek",
        parent_model="deepseek-v4-pro",
        orchestrator=FakeOrchestrator(),
    )

    assert result.ok
    assert all(item["provider_override"] is None for item in seen)
    assert all(item["model_override"] is None for item in seen)
    assert all(outcome.route["reason"] == "model_selection=pinned" for outcome in result.outcomes)


@pytest.mark.asyncio
async def test_twenty_worker_plan_runs_with_bounded_concurrency() -> None:
    active = 0
    max_active = 0

    class FakeOrchestrator:
        async def delegate(self, agent, task, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.01)
                return SimpleNamespace(
                    status="complete",
                    session_id=uuid.uuid4(),
                    response=f"done: {task}",
                    tokens=1,
                )
            finally:
                active -= 1

    plan = _plan(
        *(
            WorkerTask(id=f"worker-{index}", agent="coder", task=f"work item {index}")
            for index in range(20)
        )
    )
    result = await run_plan(
        plan,
        config=_config(),
        inventory=_inventory(),
        orchestrator=FakeOrchestrator(),
    )

    assert result.ok
    assert len(result.outcomes) == 20
    assert 1 < max_active <= 8
    assert result.tokens == 20


@pytest.mark.asyncio
async def test_mcp_transport_delegates_to_installed_harness(monkeypatch) -> None:
    from types import SimpleNamespace

    from machina import interop

    monkeypatch.setattr(
        interop,
        "detect_cached",
        lambda: ({"opencode": SimpleNamespace(installed=True)}, {"cached": True}),
    )
    monkeypatch.setattr(
        interop,
        "run_task",
        lambda platform, prompt, *, timeout: SimpleNamespace(
            ok=True,
            stdout=f"{platform} handled: {prompt}",
            stderr="",
            exit_code=0,
        ),
    )
    config = _config()
    config.values["worker_transport"] = "mcp"
    plan = _plan(
        WorkerTask(id="ui", agent="coder", task="Build the UI"),
        WorkerTask(id="api", agent="coder", task="Build the API"),
    )
    result = await run_plan(
        plan,
        config=config,
        inventory=_inventory(),
        orchestrator=object(),
    )

    assert result.ok
    assert all(outcome.route and outcome.route["transport"] == "mcp" for outcome in result.outcomes)
    assert all("opencode handled" in outcome.response for outcome in result.outcomes)


@pytest.mark.asyncio
async def test_failed_dependency_blocks_dependents_but_not_independent_tracks() -> None:
    order: list[str] = []

    class FakeOrchestrator:
        async def delegate(self, agent, task, **kwargs):
            order.append(task)
            if "fail" in task:
                raise RuntimeError("boom")
            return SimpleNamespace(
                status="complete",
                session_id=uuid.uuid4(),
                response="ok",
                tokens=1,
            )

    plan = _plan(
        WorkerTask(id="bad", agent="coder", task="fail this branch"),
        WorkerTask(id="dependent", agent="coder", task="dependent work", depends_on=("bad",)),
        WorkerTask(id="free", agent="coder", task="independent work"),
    )
    result = await run_plan(
        plan,
        config=_config(),
        inventory=_inventory(),
        orchestrator=FakeOrchestrator(),
    )

    by_id = {outcome.task_id: outcome for outcome in result.outcomes}
    assert by_id["bad"].failed
    assert by_id["dependent"].status == "blocked"
    assert not by_id["free"].failed
    assert not result.ok
    assert order == ["fail this branch", "independent work"]
