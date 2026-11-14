"""MACHINA_HOME must isolate the config file, including on Windows."""

from __future__ import annotations

import logging
from pathlib import Path

from machina.core.config import DEFAULTS, Config, default_config_path


def test_default_completion_budget_leaves_room_for_reasoning() -> None:
    assert DEFAULTS["max_tokens"] == 16384


def test_multi_auto_is_the_default_routing_mode() -> None:
    assert DEFAULTS["orchestration_mode"] == "multi_model"
    assert DEFAULTS["model_selection"] == "auto"
    assert DEFAULTS["single_model_auto_parallel"] is False


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


def test_explicit_default_config_path_override_wins(monkeypatch, tmp_path: Path) -> None:
    import machina.core.config as config_module

    target = tmp_path / "override.yaml"
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", target)

    instance = config_module.Config()
    instance.set("theme", "dracula", persist=True)

    assert target.is_file()
    assert config_module.Config.load().get("theme") == "dracula"


def test_hidden_legacy_config_warns_once(monkeypatch, tmp_path: Path, caplog) -> None:
    legacy_home = tmp_path / "legacy-home"
    (legacy_home / ".machina").mkdir(parents=True)
    legacy = legacy_home / ".machina" / "config.yaml"
    legacy.write_text("theme: monokai\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(legacy_home))
    monkeypatch.setenv("USERPROFILE", str(legacy_home))
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path / "new-home"))

    with caplog.at_level(logging.WARNING, logger="machina.core.config"):
        Config.load()
        Config.load()

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "machina.core.config"
    ]
    assert sum("older config exists" in message for message in messages) == 1
