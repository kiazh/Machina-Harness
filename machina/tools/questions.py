"""Interactive user-question tool."""

from __future__ import annotations

from typing import Any

from machina.core.questions import question_broker
from machina.tools.agents import current_session_id
from machina.tools.base import registry


@registry.register(
    name="ask_user",
    description=(
        "Ask the user a short multiple-choice question when a decision materially changes "
        "the work and cannot be inferred safely. The turn pauses until the user answers."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "A concise question shown as the dialog title.",
            },
            "options": {
                "type": "array",
                "minItems": 2,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Short option label"},
                        "description": {
                            "type": "string",
                            "description": "Optional clarification shown beside the label",
                        },
                    },
                    "required": ["label"],
                    "additionalProperties": False,
                },
                "description": "Concrete choices. Mark a recommendation in its label.",
            },
            "allow_other": {
                "type": "boolean",
                "description": "Allow the user to type a custom answer (default true).",
            },
        },
        "required": ["question", "options"],
        "additionalProperties": False,
    },
)
async def ask_user(
    question: str,
    options: list[dict[str, Any]],
    allow_other: bool = True,
) -> str:
    """Ask the active user for a choice and relay their answer."""
    session_id = current_session_id.get()
    if session_id is None:
        return "Error: no active session is available to ask the user."
    answer = await question_broker.ask(
        session_id,
        question,
        options,
        allow_other=allow_other,
    )
    return f"User answered: {answer}"
