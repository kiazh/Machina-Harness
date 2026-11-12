"""Opt-in stale-result condensation under token pressure."""

from __future__ import annotations

from machina.core.prompt import condense_history, stale_tool_preview


def _messages(content: str) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": content},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-2", "function": {"name": "terminal"}}],
        },
        {"role": "tool", "tool_call_id": "call-2", "content": content},
    ]


def test_default_preview_keeps_historical_size() -> None:
    content = "A" * 1000
    preview = stale_tool_preview(content)
    assert preview.startswith("A" * 350)
    assert preview.endswith("A" * 350)
    assert "350+350" in preview


def test_tightened_preview_marks_the_omission() -> None:
    content = "HEAD" + ("x" * 5000) + "TAIL"
    messages = _messages(content)
    condense_history(messages, stale_chars=300)

    condensed = messages[1]["content"]
    assert condensed.startswith("HEAD")
    assert condensed.endswith("TAIL")
    assert "150+150" in condensed
    assert len(condensed) < 500


def test_condensation_is_idempotent_and_preserves_latest_batch() -> None:
    content = "x" * 5000
    messages = _messages(content)

    condense_history(messages, stale_chars=300)
    first = messages[1]["content"]
    condense_history(messages, stale_chars=300)

    assert messages[1]["content"] == first
    assert messages[-1]["content"] == content


def test_duplicate_stale_outputs_are_masked_only_under_pressure() -> None:
    repeated = "same output\n" * 500
    latest = "new output\n" * 500
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": repeated},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-2", "function": {"name": "terminal"}}],
        },
        {"role": "tool", "tool_call_id": "call-2", "content": repeated},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-3", "function": {"name": "terminal"}}],
        },
        {"role": "tool", "tool_call_id": "call-3", "content": latest},
    ]

    condense_history(messages, stale_chars=300)
    assert "duplicate tool result omitted" in messages[3]["content"]
    assert messages[5]["content"] == latest

    normal = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": repeated},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-2", "function": {"name": "terminal"}}],
        },
        {"role": "tool", "tool_call_id": "call-2", "content": repeated},
    ]
    condense_history(normal)
    assert "duplicate tool result omitted" not in normal[3]["content"]


def test_skill_pages_and_reduced_views_are_never_condensed() -> None:
    skill = "Skill demo [1:2/9]:\n" + ("x" * 5000)
    reduced = "failure details\n[machina view: reduced by pytest; artifact=abc]"
    messages = _messages(skill)
    messages[-1] = {"role": "tool", "tool_call_id": "call-2", "content": reduced}

    condense_history(messages, stale_chars=300)
    assert messages[1]["content"] == skill
