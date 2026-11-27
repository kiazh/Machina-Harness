import asyncio

import pytest

from machina.core.config import config
from machina.core.exceptions import ToolError
from machina.tools import file as file_tools


def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path):
    root = tmp_path / "workspace"
    (root / "src").mkdir(parents=True)
    monkeypatch.setattr(config, "workspace_root", str(root))
    return root


def test_edit_file_replaces_an_exact_block_and_returns_a_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    root = _workspace(monkeypatch, tmp_path)
    target = root / "src" / "app.py"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = asyncio.run(file_tools.edit_file("src/app.py", "beta", "delta"))

    assert target.read_text(encoding="utf-8") == "alpha\ndelta\ngamma\n"
    assert "Edited src/app.py" in result
    assert "-beta" in result
    assert "+delta" in result


def test_edit_file_rejects_a_wrong_match_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    root = _workspace(monkeypatch, tmp_path)
    target = root / "src" / "app.py"
    target.write_text("same\nsame\n", encoding="utf-8")

    with pytest.raises(ToolError, match="expected 1 exact match"):
        asyncio.run(file_tools.edit_file("src/app.py", "same", "other"))
    assert target.read_text(encoding="utf-8") == "same\nsame\n"


def test_edit_file_rejects_an_empty_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _workspace(monkeypatch, tmp_path)
    with pytest.raises(ToolError, match="old_text must not be empty"):
        asyncio.run(file_tools.edit_file("src/app.py", "", "other"))


def test_edit_file_approval_binds_the_replacement_diff() -> None:
    from machina.permissions.tools import action_for_tool

    spec = action_for_tool(
        "edit_file",
        {"path": "src/app.py", "old_text": "one", "new_text": "two"},
    )
    assert spec["operation"] == "file.write"
    assert spec["targets"] == ["src/app.py"]
    assert "-one" in spec["content"]
    assert "+two" in spec["content"]
    assert "count: 1" in spec["content"]


def test_edit_file_approval_digest_binds_the_count() -> None:
    from machina.permissions.policy import build_request
    from machina.permissions.tools import action_for_tool

    def digest(count: int) -> str:
        spec = action_for_tool(
            "edit_file",
            {"path": "src/app.py", "old_text": "one", "new_text": "two", "count": count},
        )
        return build_request(
            operation=spec["operation"],
            targets=spec["targets"],
            content=spec["content"],
            tool="edit_file",
            cwd="/tmp",
            workspace="/tmp",
        ).digest

    assert digest(1) != digest(100)
