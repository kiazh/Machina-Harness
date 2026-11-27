"""The live LLM compaction summary stays visible after continuity folds.

Regression: ``build_context_messages`` filtered every ``compression_summary``
out of the assembler input. Once ``session.state.compaction_summary`` was
populated, the compressor's summary could no longer reach the prompt at all,
even though it is the only record of pre-compaction work.
"""

from __future__ import annotations

from machina.core import continuity


def _summary_entry(text: str) -> dict:
    return {"type": "compression_summary", "payload": {"content": text}, "tokens": 20}


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    def _assemble(_assembler, system, goal, recent, retrieved, query, **kwargs):
        captured["recent"] = recent
        return ""

    monkeypatch.setattr(continuity, "assemble_turn_prompt", _assemble)
    monkeypatch.setattr(continuity, "build_native_turn_messages", lambda *a, **k: [])
    return captured


def _build(summary: str | None, recent: list[dict]):
    return continuity.build_context_messages(
        "system",
        summary,
        [],
        object(),
        None,
        recent,
        [],
        "query",
        0,
        [],
        8000,
    )


def test_live_summary_reaches_the_assembler(monkeypatch):
    captured = _capture(monkeypatch)
    recent = [_summary_entry("LLM compaction summary of the archived work.")]

    _build("deterministic anchor of evicted turns", recent)

    assert recent[0] in captured["recent"]


def test_summary_already_folded_into_the_anchor_is_not_duplicated(monkeypatch):
    captured = _capture(monkeypatch)
    text = "LLM compaction summary of the archived work."
    recent = [_summary_entry(text)]

    _build(f"{text}\n\nUser: newer evicted turn", recent)

    assert recent[0] not in captured["recent"]
