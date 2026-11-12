"""Live manual workflows retain their exact child action while humans review it."""

import asyncio
import json
import uuid
from collections import Counter
from types import SimpleNamespace

import pytest
import pytest_asyncio

from machina.core.agent import ReActAgent
from machina.core.agent_def import AgentDef
from machina.core.models import AgentResponse, LLMResponse, Session
from machina.core.orchestrator import Orchestrator
from machina.core.runtime import RuntimeServices
from machina.gateway.server import Gateway
from machina.permissions import store
from machina.tools.base import Tool, registry


@pytest_asyncio.fixture
async def workflow(monkeypatch, tmp_path):
    """Keep orchestration, tool execution and consent real; replace external IO."""
    from machina.core import orchestrator as orchestration
    from machina.core import session_mode
    from machina.core.config import config
    from machina.core.session import session_manager

    parent = Session(id=uuid.uuid4(), agent_id="harness")
    foreign = Session(id=uuid.uuid4(), agent_id="harness")
    sessions = {parent.id: parent, foreign.id: foreign}
    executions = []
    requests = []
    history = []

    async def get_session(session_id):
        return sessions.get(session_id)

    async def create_session(**values):
        if settings.get("foreign_child"):
            values["parent_session_id"] = foreign.id
        child = Session(id=uuid.uuid4(), **values)
        sessions[child.id] = child
        return child

    async def delete_session(session_id):
        return sessions.pop(session_id, None) is not None

    async def add_chunk(**_values):
        return None

    async def effective_mode(_session_id):
        return "ask"

    async def definition(name):
        return AgentDef(name=name, tools=["workflow_observe", "workflow_effect"])

    async def fixture_tool(label, secret="", review=""):
        executions.append(label)
        return f"{label} executed"

    schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "secret": {"type": "string"},
            "review": {"type": "string"},
        },
        "required": ["label"],
    }
    for name, effects in [("workflow_observe", ()), ("workflow_effect", ("exec",))]:
        monkeypatch.setitem(
            registry._tools,
            name,
            Tool(name, "In-process fixture", schema, fixture_tool, True, effects),
        )
    monkeypatch.setattr(registry, "_definitions_cache", None)
    monkeypatch.setattr(store, "_mem", store._MemoryStore())
    monkeypatch.setattr(session_manager, "get", get_session)
    monkeypatch.setattr(session_manager, "get_fresh", get_session)
    monkeypatch.setattr(session_manager, "create", create_session)
    monkeypatch.setattr(session_manager, "delete", delete_session)
    monkeypatch.setattr(session_mode, "resolve_effective_mode", effective_mode)
    monkeypatch.setattr(orchestration.agent_registry, "get", definition)
    monkeypatch.setattr(orchestration.context_manager, "add_chunk", add_chunk)
    original_get = config.get
    settings = {
        "approval_timeout": 20,
        "terminal_sandbox": "none",
        "machina_home": str(tmp_path / "state"),
    }
    monkeypatch.setattr(
        config, "get", lambda key: settings[key] if key in settings else original_get(key)
    )

    class ScriptedProvider:
        async def respond(self, agent_id, task):
            requests.append((agent_id, task))
            name = "workflow_effect" if agent_id.startswith("second") else "workflow_observe"
            return LLMResponse(
                content="",
                model="fixture",
                tool_calls=[
                    {
                        "id": uuid.uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": {
                                "label": agent_id,
                                "secret": "sk-abcdefghijklmnopqrstuvwx",
                                "review": settings.get("review_text", ""),
                            },
                        },
                    }
                ],
            )

    class ScriptedAgent(ReActAgent):
        async def run(self, session_id, task, **_kwargs):
            response = await self.provider.respond(self.agent_id, task)
            made = []
            async for _event in self._execute_tool_calls_stream(response, [], made, session_id, []):
                pass
            denied = any(entry["outcome"] == "denied" for entry in made)
            return AgentResponse(
                content=f"{self.agent_id} result: {made[0]['result_preview']}",
                tool_calls=made,
                outcome="failed" if denied else "completed",
            )

    runner = Orchestrator(
        agent_factory=lambda definition: ScriptedAgent(
            provider=ScriptedProvider(),
            agent_id=definition.name,
            allowed_tools=definition.tools,
            delegated=True,
        )
    )

    async def record_start(_session, _source, agent, task, **_kwargs):
        message_id = uuid.uuid4()
        history.append({"id": message_id, "agent": agent, "task": task})
        return message_id

    async def record_end(message_id, **values):
        next(row for row in history if row["id"] == message_id).update(values)

    async def record_status(*_args, **_kwargs):
        return None

    async def parent_context(_session_id):
        return ""

    async def tokens(*_args):
        return 0

    monkeypatch.setattr(runner, "_record_start", record_start)
    monkeypatch.setattr(runner, "_record_end", record_end)
    monkeypatch.setattr(runner, "_record_status", record_status)
    monkeypatch.setattr(runner, "_parent_context", parent_context)
    monkeypatch.setattr(runner, "_settle_child_tokens", tokens)
    monkeypatch.setattr(runner, "_charged_child_tokens", tokens)
    monkeypatch.setattr(orchestration, "orchestrator", runner)
    frames = []
    gateway = Gateway(
        frames.append, owns_db=False, runtime=RuntimeServices(), inherit_env_token=False
    )
    gateway._db_ready = True
    fixture = SimpleNamespace(
        gateway=gateway,
        parent=parent,
        foreign=foreign,
        sessions=sessions,
        executions=executions,
        requests=requests,
        history=history,
        settings=settings,
    )
    try:
        yield fixture
    finally:
        await gateway.close(timeout=1)


async def start(workflow, agents=("first", "second", "third"), mode="sequential"):
    return await workflow.gateway.operations.start(
        {
            "method": "agents.run",
            "params": {
                "sessionId": str(workflow.parent.id),
                "mode": mode,
                "steps": [{"agent": agent, "task": f"{agent} task"} for agent in agents],
            },
        }
    )


async def snapshot_until(workflow, operation, predicate):
    async with asyncio.timeout(3):
        while True:
            value = workflow.gateway.operations.get({"id": operation["id"]})
            if predicate(value):
                return value
            if value["status"] not in ("running", "cancelling"):
                pytest.fail(f"workflow settled before expected state: {value}")
            await asyncio.sleep(0)


async def pending(workflow, operation, count=1):
    return await snapshot_until(
        workflow, operation, lambda value: len(value.get("approvals", [])) == count
    )


async def settled(workflow, operation):
    return await snapshot_until(
        workflow, operation, lambda value: value["status"] not in ("running", "cancelling")
    )


async def resolve(workflow, approval, verdict="approved", session_id=None):
    from machina.gateway.features.mode import approvals_resolve

    return await approvals_resolve(
        workflow.gateway,
        {
            "requestId": approval["requestId"],
            "sessionId": session_id or approval["sessionId"],
            "verdict": verdict,
            "grant": "once",
        },
    )


@pytest.mark.asyncio
async def test_sequence_waits_and_continues_same_action_once_without_replaying_results(workflow):
    from machina.core.turns import begin_mutation
    from machina.gateway.errors import RpcError

    operation = await start(workflow)
    waiting = await pending(workflow, operation)
    approval = waiting["approvals"][0]
    assert waiting["status"] == "running" and waiting["cancellable"] is True
    assert workflow.executions == ["first"]
    assert await begin_mutation(workflow.parent.id) is None
    assert approval["sessionId"] != str(workflow.parent.id)
    assert approval["agentId"] == "second"
    assert approval["operation"] == "tool.workflow_effect"
    assert "sk-abcdefghijklmnopqrstuvwx" not in str(approval)
    assert "contentPreview" in approval["proposal"]
    with pytest.raises(RpcError, match="does not belong"):
        await resolve(workflow, approval, session_id=str(workflow.parent.id))
    with pytest.raises(RpcError, match="does not belong"):
        await resolve(workflow, approval, session_id=str(workflow.foreign.id))
    waiting["approvals"][0]["argv"].append("injected argument")
    current = workflow.gateway.operations.get({"id": operation["id"]})
    assert current["approvals"][0]["argv"] == []
    assert workflow.executions == ["first"]
    await resolve(workflow, approval)
    with pytest.raises(RpcError, match="already consumed"):
        await resolve(workflow, approval)
    final = await settled(workflow, operation)
    assert [row["status"] for row in final["result"]["results"]] == ["complete"] * 3
    assert Counter(workflow.executions) == {"first": 1, "second": 1, "third": 1}
    assert len(workflow.requests) == 3
    assert "first result: first executed" in workflow.requests[2][1]
    assert "second result: second executed" in workflow.requests[2][1]
    assert not final.get("approvals")
    assert not workflow.gateway._pending_approvals
    record = await store.get_approval(approval["requestId"])
    assert record["status"] == "completed" and record["consumed"] is True
    assert await store.list_grants(approval["sessionId"]) == []
    assert not workflow.gateway.turn_running(workflow.parent.id)


@pytest.mark.asyncio
async def test_parallel_approval_resolution_keeps_other_child_waiting(workflow):
    operation = await start(workflow, agents=("second-a", "second-b"), mode="parallel")
    waiting = await pending(workflow, operation, count=2)
    first, second = waiting["approvals"]
    assert first["sessionId"] != second["sessionId"]
    await resolve(workflow, first)
    remaining = await snapshot_until(
        workflow,
        operation,
        lambda value: (
            len(value.get("approvals", [])) == 1 and first["agentId"] in workflow.executions
        ),
    )
    assert remaining["approvals"][0]["requestId"] == second["requestId"]
    assert first["agentId"] in workflow.executions
    assert second["agentId"] not in workflow.executions
    await resolve(workflow, second)
    final = await settled(workflow, operation)
    assert Counter(workflow.executions) == {"second-a": 1, "second-b": 1}
    assert not final.get("approvals")
    assert not workflow.gateway._pending_approvals


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["cancel", "shutdown", "denied", "expired"])
async def test_pending_child_cards_and_futures_retire_without_running_effect(workflow, ending):
    operation = await start(workflow, agents=("second",))
    waiting = await pending(workflow, operation)
    approval = waiting["approvals"][0]
    if ending == "cancel":
        workflow.gateway.operations.cancel({"id": operation["id"]})
        workflow.gateway.operations.cancel({"id": operation["id"]})
    elif ending == "shutdown":
        await workflow.gateway.close(timeout=1)
    else:
        await resolve(workflow, approval, verdict=ending)
    final = await settled(workflow, operation)
    assert not workflow.executions
    assert not final.get("approvals")
    assert not workflow.gateway._pending_approvals
    assert (await store.get_approval(approval["requestId"]))["status"] == (
        "cancelled" if ending in ("cancel", "shutdown") else ending
    )
    assert not workflow.gateway.turn_running(workflow.parent.id)


@pytest.mark.asyncio
async def test_operation_refuses_an_approval_from_another_parents_child(workflow):
    workflow.settings["foreign_child"] = True
    operation = await start(workflow, agents=("second",))
    final = await settled(workflow, operation)
    assert not workflow.executions
    assert not final.get("approvals")
    assert not workflow.gateway._pending_approvals
    assert workflow.history[0]["status"] == "error"
    records = list(store._mem.approvals.values())
    assert len(records) == 1 and records[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancelling_parallel_children_retires_each_pending_decision(workflow):
    operation = await start(workflow, agents=("second-a", "second-b"), mode="parallel")
    approvals = (await pending(workflow, operation, count=2))["approvals"]
    workflow.gateway.operations.cancel({"id": operation["id"]})
    final = await settled(workflow, operation)
    assert final["status"] == "cancelled"
    assert not workflow.executions
    assert not final.get("approvals")
    assert not workflow.gateway._pending_approvals
    assert [(await store.get_approval(card["requestId"]))["status"] for card in approvals] == [
        "cancelled",
        "cancelled",
    ]


@pytest.mark.asyncio
async def test_parent_claim_loss_while_waiting_cancels_child_and_reports_failure(
    workflow, monkeypatch
):
    from machina.core import turns
    from machina.gateway import operations

    operation = await start(workflow, agents=("second",))
    approval = (await pending(workflow, operation))["approvals"][0]

    async def ownership_lost(*_args):
        return False

    monkeypatch.setattr(turns, "renew_turn", ownership_lost)
    monkeypatch.setattr(operations, "CLAIM_RENEW_INTERVAL_SECONDS", 0)
    await workflow.gateway.operations._renew(workflow.gateway.operations._items[operation["id"]])
    final = await settled(workflow, operation)
    assert final["status"] == "failed" and "ownership" in final["error"]
    assert not workflow.executions
    assert not final.get("approvals")
    assert not workflow.gateway._pending_approvals
    assert (await store.get_approval(approval["requestId"]))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_broker_deadline_retires_operation_card_and_claim(workflow):
    workflow.settings["approval_timeout"] = 0.02
    operation = await start(workflow, agents=("second",))
    approval = (await pending(workflow, operation))["approvals"][0]
    final = await settled(workflow, operation)
    assert not workflow.executions
    assert not final.get("approvals")
    assert not workflow.gateway._pending_approvals
    assert (await store.get_approval(approval["requestId"]))["status"] == "expired"
    assert final["result"]["results"][0]["status"] == "waiting_approval"
    assert not workflow.gateway.turn_running(workflow.parent.id)


@pytest.mark.asyncio
async def test_ownership_loss_after_human_decision_stops_effect_before_execution(workflow):
    from machina.core import turns

    operation = await start(workflow, agents=("second",))
    approval = (await pending(workflow, operation))["approvals"][0]

    owner = workflow.gateway.operations._items[operation["id"]]
    await turns.end_turn(workflow.parent.id, owner.token)
    await resolve(workflow, approval)
    final = await settled(workflow, operation)
    assert final["status"] == "failed" and "ownership" in final["error"]
    assert not workflow.executions
    assert not final.get("approvals")
    assert not workflow.gateway._pending_approvals
    record = await store.get_approval(approval["requestId"])
    assert record["status"] == "approved" and record["consumed"] is False


@pytest.mark.asyncio
async def test_parallel_review_budget_counts_escaped_wire_bytes_and_rejects_only_excess_card(
    workflow, monkeypatch
):
    from machina.gateway import operations

    # Both cards fit separately; unicode escapes make their combined wire
    # representation exceed this deliberately smaller aggregate test budget.
    monkeypatch.setattr(operations, "MAX_PENDING_APPROVAL_BYTES", 16_000, raising=False)
    workflow.settings["review_text"] = "界" * 2000
    operation = await start(workflow, agents=("second-a", "second-b"), mode="parallel")
    waiting = await snapshot_until(
        workflow,
        operation,
        lambda value: len(store._mem.approvals) == 2
        and len(value.get("approvals", [])) == 1,
    )
    assert len(waiting.get("approvals", [])) == 1
    assert (
        8_000 < len(json.dumps(waiting["approvals"], ensure_ascii=True).encode("utf-8")) <= 16_000
    )
    assert len(json.dumps(waiting["approvals"], ensure_ascii=False).encode("utf-8")) < 8_000
    assert len(workflow.gateway._pending_approvals) == 1
    records = list(store._mem.approvals.values())
    assert sorted(record["status"] for record in records) == ["cancelled", "pending"]
    approval = waiting["approvals"][0]
    await resolve(workflow, approval)
    final = await settled(workflow, operation)
    assert workflow.executions == [approval["agentId"]]
    assert sorted(row["status"] for row in final["result"]["results"]) == ["complete", "error"]
    assert not final.get("approvals")
    assert not workflow.gateway._pending_approvals
