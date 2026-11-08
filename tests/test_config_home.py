"""MACHINA_HOME must isolate the config file, including on Windows."""

from __future__ import annotations

from pathlib import Path

from machina.core.config import Config, default_config_path


def test_machina_home_selects_config_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path / "state"))

    assert default_config_path() == tmp_path / "state" / "config.yaml"


def test_config_save_and_load_use_machina_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path / "state"))
    instance = Config()
    instance.set("theme", "dracula", persist=True)

    path = tmp_path / "state" / "config.yaml"
    assert path.is_file()
    loaded = Config.load()
    assert loaded.get("theme") == "dracula"
