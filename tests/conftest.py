"""Shared pytest configuration.

Only the golden-trace flag lives here; every other test keeps working without
a conftest (the ``tests`` directory is added to ``sys.path`` by pytest).
"""

from __future__ import annotations


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite tests/golden/turns/*.json from the current turn loop.",
    )
