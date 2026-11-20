"""OTLP projection is dependency-free and opt-in."""

from __future__ import annotations

from machina.observability import traces
from machina.observability.otlp import install_otlp_exporter, turn_to_otlp
from machina.observability.traces import Span, TurnTrace


def _turn() -> TurnTrace:
    return TurnTrace(
        turn_id="turn-123",
        session_id="session-1",
        spans=[
            Span(
                id="root",
                kind=traces.TURN,
                name="turn",
                turn_id="turn-123",
                parent_id=None,
                started_ms=100.0,
                ended_ms=125.0,
                duration_ms=25.0,
                status=traces.STATUS_OK,
            ),
            Span(
                id="tool-1",
                kind=traces.TOOL,
                name="read_file",
                turn_id="turn-123",
                parent_id="root",
                started_ms=105.0,
                ended_ms=110.0,
                duration_ms=5.0,
                status=traces.STATUS_OK,
                attributes={"tool": "read_file"},
            ),
        ],
    )


def test_turn_projects_to_otlp_json_with_parent_links() -> None:
    body = turn_to_otlp(_turn(), now_ns=1_000_000_000)
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 2
    assert len(spans[0]["traceId"]) == 32
    assert len(spans[0]["spanId"]) == 16
    assert spans[1]["parentSpanId"] == spans[0]["spanId"]
    assert spans[1]["attributes"] == [{"key": "tool", "value": {"stringValue": "read_file"}}]
    assert spans[1]["status"]["code"] == 1


def test_exporter_is_disabled_without_an_endpoint(monkeypatch) -> None:
    monkeypatch.setattr("machina.observability.otlp._INSTALLED", False)
    monkeypatch.delenv("MACHINA_OTLP_ENDPOINT", raising=False)
    assert install_otlp_exporter() is False
