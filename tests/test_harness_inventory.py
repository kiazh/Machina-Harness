"""Harness capability inventory parsing, cache, and redaction contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

from machina.core import harness_inventory


def test_inventory_parses_opencode_models_agents_and_auth(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MACHINA_HARNESS_FEATURES_FILE", str(tmp_path / "features.json"))
    monkeypatch.setattr(
        harness_inventory.interop,
        "detect",
        lambda platforms=None: {
            "opencode": SimpleNamespace(
                platform="opencode",
                label="OpenCode",
                installed=True,
                binary="opencode",
                version="2.1.1",
                running=True,
                registered=True,
                notes=[],
            )
        },
    )

    def fake_probe(binary, args, *, timeout=10.0):
        if args == ("models",):
            return SimpleNamespace(
                returncode=0,
                output="opencode/big-pickle\nopenai/gpt-6.1-sol\n",
            )
        if args == ("debug", "agents"):
            return SimpleNamespace(
                returncode=0,
                output=json.dumps(
                    [
                        {"id": "build", "name": "Build", "description": "default"},
                        {"id": "plan", "description": "planner"},
                    ]
                ),
            )
        if args == ("auth", "list"):
            return SimpleNamespace(returncode=0, output="DeepSeek  DeepSeek  stored\n")
        return SimpleNamespace(returncode=0, output="")

    monkeypatch.setattr(harness_inventory, "_probe", fake_probe)
    payload = harness_inventory.discover(platforms=("opencode",), use_parallel=False)
    profile = payload["opencode"]

    assert profile["installed"] is True
    assert profile["models"] == ["opencode/big-pickle", "openai/gpt-6.1-sol"]
    assert [agent["id"] for agent in profile["agents"]] == ["build", "plan"]
    assert profile["auth_providers"] == ["DeepSeek"]
    cached = harness_inventory.cached()
    assert cached["profiles"]["opencode"]["version"] == "2.1.1"
    compact = harness_inventory.summary()
    assert compact["profiles"]["opencode"]["features"]["models"] == 2


def test_inventory_persists_no_secret_and_detects_staleness(monkeypatch, tmp_path) -> None:
    target = tmp_path / "features.json"
    monkeypatch.setenv("MACHINA_HARNESS_FEATURES_FILE", str(target))
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2020-01-01T00:00:00+00:00",
                "profiles": {
                    "opencode": {
                        "platform": "opencode",
                        "installed": True,
                        "models": ["opencode/big-pickle"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cached = harness_inventory.cached()
    assert cached["stale"] is True
    summary = harness_inventory.summary()
    assert summary["stale"] is True
    assert "secret" not in target.read_text(encoding="utf-8").lower()


def test_inventory_clear_cache(monkeypatch, tmp_path) -> None:
    target = tmp_path / "features.json"
    monkeypatch.setenv("MACHINA_HARNESS_FEATURES_FILE", str(target))
    target.write_text("{}", encoding="utf-8")
    harness_inventory.clear_cache()
    assert not target.exists()
