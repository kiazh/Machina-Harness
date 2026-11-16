"""CI workflow references must point at files that still exist."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_UI_TEST_PATH = re.compile(r"(?<![\w/.-])(test/[A-Za-z0-9_.-]+\.test\.ts)")


def test_ci_ui_test_paths_exist() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    referenced = set(_UI_TEST_PATH.findall(workflow))
    assert referenced, "CI no longer names any UI test files; update this guard"
    missing = sorted(path for path in referenced if not (ROOT / "ui" / path).is_file())
    assert not missing, f"CI references deleted UI test files: {missing}"
