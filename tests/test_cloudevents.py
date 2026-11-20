"""CloudEvents projection preserves the native Machina event payload."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from machina.api.routes.models import PromptRequest
from machina.protocol.cloudevents import wrap_event


def test_cloudevent_envelope_preserves_the_native_event() -> None:
    native = {"protocolVersion": 2, "type": "text", "sessionId": "s", "turnId": "t", "text": "hi"}
    event = wrap_event(
        native,
        source="/machina/sessions/s",
        event_id="fixed-id",
        event_time=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    assert event == {
        "specversion": "1.0",
        "id": "fixed-id",
        "source": "/machina/sessions/s",
        "type": "io.machina.text",
        "time": "2026-01-02T03:04:05+00:00",
        "datacontenttype": "application/json",
        "data": native,
    }


def test_prompt_request_accepts_only_known_event_formats() -> None:
    assert PromptRequest(text="hello", eventFormat="cloudevents").eventFormat == "cloudevents"
    with pytest.raises(ValidationError):
        PromptRequest(text="hello", eventFormat="other")
