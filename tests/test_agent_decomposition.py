"""The decomposed agent seams preserve their callable contracts."""

from __future__ import annotations

from machina.core.agent import ReActAgent
from machina.core.tool_results import ToolResultRecorder


def test_condense_history_remains_callable_on_class_and_instance():
    agent = ReActAgent(provider=object(), agent_id="harness")

    # Regression: the move dropped @staticmethod, turning `messages` into self.
    ReActAgent._condense_history([])
    agent._condense_history([])


def test_fallback_final_content_is_static():
    text = ReActAgent._fallback_final_content([{"tool": "read_file"}, {"tool": "edit_file"}])

    assert "2 tool call(s)" in text
    assert "read_file" in text


def test_tool_entry_labels_a_paused_action():
    recorder = ToolResultRecorder(object())

    entry = recorder.tool_entry(
        {"name": "terminal", "args": {}, "result": "", "_needs_approval": {"id": "x"}}
    )

    assert entry["outcome"] == "paused"
    assert entry["needs_approval"] == {"id": "x"}
