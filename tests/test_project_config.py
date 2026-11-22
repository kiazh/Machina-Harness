"""Project-local defaults are bounded and non-authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from machina import project_config


def test_nearest_project_config_is_discovered(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    (root / "machina.json").write_text(
        json.dumps({"model": "deepseek/deepseek-v4-flash", "formatter": "auto"}),
        encoding="utf-8",
    )
    assert project_config.discover(nested) == root / "machina.json"


def test_project_config_rejects_authority_and_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "machina.json"
    for payload in (
        {"execution_mode": "full"},
        {"database_url": "postgresql://secret"},
        {"formatter": "auto", "typo": True},
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError):
            project_config.load(path)


def test_project_config_validates_types(tmp_path: Path) -> None:
    path = tmp_path / "machina.json"
    path.write_text(json.dumps({"snapshots_enabled": "yes"}), encoding="utf-8")
    with pytest.raises(ValueError):
        project_config.load(path)


def test_project_config_applies_in_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "machina.json"
    path.write_text(
        json.dumps({"formatter": "auto", "snapshots_enabled": False}),
        encoding="utf-8",
    )
    from machina.core.config import config

    monkeypatch.delenv("MACHINA_FORMATTER", raising=False)
    monkeypatch.delenv("MACHINA_SNAPSHOTS_ENABLED", raising=False)
    old_formatter = config.get("formatter")
    old_snapshots = config.get("snapshots_enabled")
    try:
        assert project_config.apply(tmp_path) == path
        assert config.get("formatter") == "auto"
        assert config.get("snapshots_enabled") is False
    finally:
        config.set("formatter", old_formatter, persist=False)
        config.set("snapshots_enabled", old_snapshots, persist=False)
        monkeypatch.delenv("MACHINA_FORMATTER", raising=False)
        monkeypatch.delenv("MACHINA_SNAPSHOTS_ENABLED", raising=False)
