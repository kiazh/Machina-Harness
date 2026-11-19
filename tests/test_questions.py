"""Structured interactive questions for a running agent turn."""

from __future__ import annotations

import asyncio

import pytest

from machina.core.questions import QuestionBroker, QuestionOption


@pytest.mark.asyncio
async def test_question_broker_emits_options_and_resumes_with_answer():
    broker = QuestionBroker()
    emitted: list[dict] = []
    broker.bind("session", emitted.append)
    task = asyncio.create_task(
        broker.ask(
            "session",
            "Which stack?",
            [
                QuestionOption("option-1", "Next.js + React (recommended)"),
                QuestionOption("option-2", "Plain HTML"),
            ],
        )
    )
    await asyncio.sleep(0)
    assert emitted[0]["question"] == "Which stack?"
    assert [option["label"] for option in emitted[0]["options"]] == [
        "Next.js + React (recommended)",
        "Plain HTML",
    ]
    assert broker.answer("session", emitted[0]["requestId"], "Next.js + React (recommended)")
    assert await task == "Next.js + React (recommended)"


@pytest.mark.asyncio
async def test_question_broker_refuses_without_interactive_client():
    broker = QuestionBroker()
    result = await broker.ask("session", "Which stack?", [QuestionOption("a", "A"), QuestionOption("b", "B")])
    assert "No interactive client" in result


@pytest.mark.asyncio
async def test_question_broker_cancel_unblocks_pending_tool():
    broker = QuestionBroker()
    broker.bind("session", lambda _payload: None)
    task = asyncio.create_task(
        broker.ask("session", "Which stack?", [QuestionOption("a", "A"), QuestionOption("b", "B")])
    )
    await asyncio.sleep(0)
    broker.cancel_session("session")
    assert "cancelled" in (await task).lower()
