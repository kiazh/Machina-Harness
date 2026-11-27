"""File tools carry file-sized arguments without a 20k validation cap."""

from __future__ import annotations

import pytest

from machina.core.exceptions import ValidationError
from machina.tools.base import registry


def _tool(name: str):
    registry.list_tools()  # force lazy built-in registration
    return registry._registered_tools[name]


def test_write_file_accepts_more_than_the_generic_cap():
    tool = _tool("write_file")

    registry._validate_tool_args(tool, {"path": "large.py", "content": "x" * 30_000})


def test_write_file_still_rejects_gigantic_content():
    tool = _tool("write_file")

    with pytest.raises(ValidationError, match="max 1000000"):
        registry._validate_tool_args(tool, {"path": "huge.py", "content": "x" * 1_000_001})


def test_edit_file_accepts_a_large_block():
    tool = _tool("edit_file")

    registry._validate_tool_args(
        tool,
        {
            "path": "large.py",
            "old_text": "x" * 100_000,
            "new_text": "y" * 100_000,
        },
    )


def test_other_tools_keep_the_generic_cap():
    tool = _tool("remember")

    with pytest.raises(ValidationError, match="max 20000"):
        registry._validate_tool_args(tool, {"content": "x" * 20_001})
