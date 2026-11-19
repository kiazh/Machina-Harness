"""Interactive questions raised by a running agent turn.

A question is a short, structured pause in the current turn. The agent asks
for a decision, the active client renders the options, and the answer is
returned to the same tool call so the turn can continue with its original
context. Non-interactive routes get an explicit refusal rather than hanging.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Callable

# Keep a question bounded so a foreground turn cannot wait forever. This is
# deliberately longer than the default tool timeout: waiting for a human is
# not provider or tool latency.
QUESTION_TIMEOUT_SECONDS = 600.0
MAX_QUESTION_CHARS = 2_000
MAX_OPTIONS = 6
MAX_ANSWER_CHARS = 4_000


@dataclass(frozen=True)
class QuestionOption:
    id: str
    label: str
    description: str = ""


@dataclass
class PendingQuestion:
    request_id: str
    session_id: str
    future: asyncio.Future[str]


class QuestionBroker:
    """Session-scoped broker for one interactive question at a time.

    The gateway binds a listener for the duration of a turn. The listener
    emits a ``question.asked`` event. Any authenticated client can answer via
    ``question.answer``; the waiting tool call receives the answer.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._pending: dict[str, PendingQuestion] = {}

    def bind(self, session_id: Any, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners[str(session_id)] = listener

    def unbind(self, session_id: Any) -> None:
        self._listeners.pop(str(session_id), None)
        self.cancel_session(session_id)

    async def ask(
        self,
        session_id: Any,
        question: str,
        options: list[QuestionOption],
        *,
        allow_other: bool = True,
    ) -> str:
        """Ask one question and wait for the answer.

        The returned string is model-facing tool output. It is intentionally
        plain text so it remains understandable to every provider.
        """
        session = str(session_id)
        listener = self._listeners.get(session)
        if listener is None:
            return (
                "No interactive client is connected to ask the user. "
                "Do not block; continue with the best available option or ask in your final response."
            )
        text = " ".join(str(question or "").split())
        if not text:
            return "Error: ask_user requires a non-empty question."
        if len(text) > MAX_QUESTION_CHARS:
            return f"Error: question is too long (max {MAX_QUESTION_CHARS} characters)."
        cleaned: list[QuestionOption] = []
        for index, option in enumerate(options):
            if isinstance(option, str):
                label, description = option, ""
            elif isinstance(option, QuestionOption):
                label, description = option.label, option.description
            elif isinstance(option, dict):
                label = str(option.get("label") or "").strip()
                description = str(option.get("description") or "").strip()
            else:
                continue
            if not label:
                continue
            label = " ".join(label.split())
            if len(label) > 200:
                label = label[:197] + "..."
            if len(description) > 500:
                description = description[:497] + "..."
            cleaned.append(QuestionOption(f"option-{index + 1}", label, description))
        if len(cleaned) < 2:
            return "Error: ask_user needs at least two distinct options."
        if len(cleaned) > MAX_OPTIONS:
            cleaned = cleaned[:MAX_OPTIONS]

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        request_id = uuid.uuid4().hex
        self._pending[request_id] = PendingQuestion(request_id, session, future)
        payload = {
            "requestId": request_id,
            "question": text,
            "options": [
                {"id": option.id, "label": option.label, "description": option.description}
                for option in cleaned
            ],
            "allowOther": bool(allow_other),
        }
        try:
            try:
                listener(payload)
            except Exception as error:
                from machina.observability.diagnostics import record_failure

                record_failure("questions.emit", error)
                return "No interactive client is available to answer the question. Continue without blocking."
            return await asyncio.wait_for(asyncio.shield(future), timeout=QUESTION_TIMEOUT_SECONDS)
        except TimeoutError:
            return "The user did not answer in time. Continue with your best judgment."
        except asyncio.CancelledError:
            raise
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    def answer(self, session_id: Any, request_id: str, answer: str) -> bool:
        pending = self._pending.get(str(request_id))
        if pending is None or pending.session_id != str(session_id):
            return False
        if pending.future.done():
            return False
        cleaned = " ".join(str(answer or "").split())
        if not cleaned:
            cleaned = "User skipped the question. Continue with your best judgment."
        if len(cleaned) > MAX_ANSWER_CHARS:
            cleaned = cleaned[:MAX_ANSWER_CHARS]
        pending.future.set_result(cleaned)
        return True

    def cancel_session(self, session_id: Any) -> None:
        session = str(session_id)
        for pending in list(self._pending.values()):
            if pending.session_id == session and not pending.future.done():
                pending.future.set_result(
                    "The question was cancelled because the interactive client closed. "
                    "Continue with your best judgment."
                )
                self._pending.pop(pending.request_id, None)


question_broker = QuestionBroker()

__all__ = [
    "QuestionBroker",
    "QuestionOption",
    "QUESTION_TIMEOUT_SECONDS",
    "question_broker",
]
