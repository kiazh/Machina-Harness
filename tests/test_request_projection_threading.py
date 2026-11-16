"""Request projection runs off the event loop and copies each message once."""

from __future__ import annotations

import threading

import pytest

from machina.core import request_gate as gate_module
from machina.core import request_projection as projection_module
from machina.core.request_projection import ProjectionInput, ProjectionResult


def _result(tokens: int = 1) -> ProjectionResult:
    return ProjectionResult(
        retained=(),
        projected=(),
        omitted=(),
        retained_pins=(),
        planned_messages=(),
        pinned_survived=True,
        estimated_tokens=tokens,
        basis="test",
        passes=1,
        refusal=None,
        input_revision="revision",
    )


def _input() -> ProjectionInput:
    return ProjectionInput(
        messages=(),
        budget_tokens=100,
        count_tokens=lambda _messages: 1,
    )


@pytest.mark.asyncio
async def test_wired_projection_runs_in_a_worker_thread(monkeypatch) -> None:
    main_thread = threading.get_ident()
    seen: list[int] = []

    async def fake_build_input(*_args, **_kwargs):
        return _input()

    def fake_project(_inp):
        seen.append(threading.get_ident())
        return _result()

    monkeypatch.setattr(gate_module, "_build_input", fake_build_input)
    monkeypatch.setattr(gate_module, "project_request", fake_project)
    monkeypatch.setattr(gate_module, "audit_log", lambda *_args, **_kwargs: None)

    gate = await gate_module._wired_gate(
        [],
        session=object(),
        provider=object(),
        tool_defs=[],
        session_id="session",
        agent_id="harness",
        user_message="",
        total_tokens=0,
        iterations=0,
        tool_calls_made=None,
        effective_budget=100,
    )

    assert gate is not None
    assert gate.projected is True
    assert seen and seen[0] != main_thread


@pytest.mark.asyncio
async def test_shadow_projection_runs_in_a_worker_thread(monkeypatch) -> None:
    main_thread = threading.get_ident()
    seen: list[int] = []

    async def fake_build_input(*_args, **_kwargs):
        return _input()

    def fake_shadow(_inp, *, baseline=None):
        seen.append(threading.get_ident())
        assert baseline == []
        return {
            "would_fit": True,
            "refusal": None,
            "estimated_tokens": 1,
            "effective_budget_tokens": 100,
            "passes": 1,
            "retained": (),
            "projected": (),
            "omitted": (),
            "retained_pins": (),
            "pinned_survived": True,
            "basis": "test",
            "input_revision": "revision",
            "baseline": {"messages": 0, "sha256": "0" * 64},
        }

    monkeypatch.setattr(gate_module, "_build_input", fake_build_input)
    monkeypatch.setattr(gate_module, "shadow_report", fake_shadow)

    report = await gate_module._shadow_note(
        [],
        session=object(),
        provider=object(),
        tool_defs=[],
        session_id="session",
        agent_id="harness",
        user_message="",
    )

    assert report is not None and report["would_fit"] is True
    assert seen and seen[0] != main_thread


def test_provider_messages_to_units_copies_each_message_once(monkeypatch) -> None:
    real_deepcopy = projection_module.copy.deepcopy
    copies: list[object] = []

    def counting_deepcopy(value):
        copies.append(value)
        return real_deepcopy(value)

    monkeypatch.setattr(projection_module.copy, "deepcopy", counting_deepcopy)
    message = {
        "role": "assistant",
        "content": "working",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }
        ],
        "_native_blocks": [{"type": "text", "text": "signed"}],
    }

    units = projection_module.provider_messages_to_units(
        [message],
        native_key="_native_blocks",
    )

    assert len(copies) == 1
    assert units[0].raw is not message
    assert units[0].tool_calls[0]["id"] == "call-1"
    assert units[0].native_blocks == [{"type": "text", "text": "signed"}]
