"""Lightweight imports stay free of eager tool modules; registry lookup still works."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_lightweight_imports_do_not_load_optional_tool_modules() -> None:
    script = """
import sys

import machina.tools

optional = (
    "machina.tools.rag",
    "machina.tools.browser",
    "machina.tools.builtins",
    "machina.tools.memory",
    "machina.tools.web_crawl",
    "machina.tools.web_research",
)
assert not [name for name in optional if name in sys.modules], sorted(sys.modules)
assert "rag" in dir(machina.tools)

# The Typer CLI imports the tools package for its registry but must not force
# the optional tool modules in at startup.
import machina.cli  # noqa: E402,F401

assert not [name for name in optional if name in sys.modules], sorted(sys.modules)

from machina.tools.base import registry  # noqa: E402

assert registry._tools_loaded is False
assert machina.tools.browser.__name__ == "machina.tools.browser"
assert "machina.tools.rag" not in sys.modules

names = registry.list_tools()
for expected in ("read_file", "search_documents", "browser_verify"):
    assert expected in names, expected
assert registry.get_tool("read_file") is not None
print("lazy-tools-ok")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert "lazy-tools-ok" in completed.stdout
