"""Fast harness snapshots: no probe spawns on the picker path."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from machina import interop
from machina.gateway.features import interop as interop_feature


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path / "machina-home"))
    monkeypatch.setenv(interop.STATE_ENV_OVERRIDE, str(tmp_path / "interop.json"))
    interop.clear_detection_cache()
    yield
    interop.clear_detection_cache()


def _result(platform: str, *, version: str | None = None) -> interop.DetectResult:
    spec = interop.PLATFORMS[platform]
    return interop.DetectResult(
        platform=platform,
        label=spec.label,
        installed=True,
        binary="fake",
        version=version,
        running=False,
        registered=False,
        registered_detail="not registered",
        scopes=[],
        enabled=None,
        state_scope=None,
        notes=[],
    )


def test_first_snapshot_is_fast_and_refresh_is_background(monkeypatch) -> None:
    monkeypatch.setattr(interop, "_DETECT_THREAD", None)
    monkeypatch.setattr(interop, "_which", lambda _spec: "fake")
    monkeypatch.setattr(interop, "_file_scopes", lambda _spec: [])
    monkeypatch.setattr(interop, "_start_detection_refresh", lambda: True)

    started = time.perf_counter()
    results, meta = interop.detect_cached(refresh=True)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert elapsed_ms < 250.0
    assert meta == {"cached": True, "checked_at": None, "refreshing": True}
    assert set(results) == set(interop.PLATFORMS)
    assert all(result.installed for result in results.values())
    assert all(result.version is None for result in results.values())


def test_fresh_complete_detection_is_served_without_probe_spawn(monkeypatch) -> None:
    monkeypatch.setattr(interop, "_which", lambda _spec: "fake")
    monkeypatch.setattr(interop, "_file_scopes", lambda _spec: [])
    interop._remember_detection(
        {key: _result(key, version=f"{key}-v1") for key in interop.PLATFORMS}
    )

    results, meta = interop.detect_cached(refresh=True)

    assert meta["cached"] is False
    assert meta["checked_at"]
    assert results["codex"].version == "codex-v1"


@pytest.mark.asyncio
async def test_status_defaults_to_cached_snapshot_and_refresh_forces_live(monkeypatch) -> None:
    results = {key: _result(key, version="v1") for key in interop.PLATFORMS}
    meta = {
        "cached": True,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "refreshing": True,
    }
    calls: list[bool] = []

    def fake_cached(*, refresh: bool = True):
        calls.append(refresh)
        return results, meta

    monkeypatch.setattr(interop_feature, "detect_cached", fake_cached)
    monkeypatch.setattr(
        interop_feature,
        "detect_parallel",
        lambda: {key: _result(key, version="v2") for key in interop.PLATFORMS},
    )

    fast = await interop_feature.interop_status(object(), {})
    live = await interop_feature.interop_status(object(), {"refresh": True})

    assert calls == [True]
    assert fast["cached"] is True
    assert fast["refreshing"] is True
    assert live["cached"] is False
    assert live["platforms"][0]["version"] == "v2"


def test_detect_platform_runs_independent_probes_together(monkeypatch) -> None:
    spec = interop.PLATFORMS["codex"]
    monkeypatch.setattr(interop, "_which", lambda _spec: "fake")

    def slow_version(*_args, **_kwargs):
        time.sleep(0.12)
        return "v1"

    def slow_list(*_args, **_kwargs):
        time.sleep(0.12)
        return interop._ListProbe(False, False, "", "not listed")

    monkeypatch.setattr(interop, "_probe_version", slow_version)
    monkeypatch.setattr(interop, "_probe_list", slow_list)
    monkeypatch.setattr(interop, "_is_running", lambda _spec: False)

    started = time.perf_counter()
    result = interop.detect_platform(spec)
    elapsed = time.perf_counter() - started

    assert result.version == "v1"
    assert elapsed < 0.30


@pytest.mark.asyncio
async def test_legacy_detect_override_preserves_old_wire_shape(monkeypatch) -> None:
    rows = {"opencode": _result("opencode", version="legacy-v1")}
    monkeypatch.setattr(interop_feature, "detect", lambda: rows)
    monkeypatch.setattr(interop_feature, "supported_runs", lambda: {})
    monkeypatch.setattr(interop_feature, "state_path", lambda: "/tmp/interop-state.json")

    result = await interop_feature.interop_list(object(), {})
    status = await interop_feature.interop_status(object(), {})

    assert result == {
        "harnesses": [
            {
                "key": "opencode",
                "label": "OpenCode",
                "installed": True,
                "version": "legacy-v1",
                "running": False,
                "registered": False,
                "scope": None,
                "runSupported": False,
                "runHint": None,
            }
        ],
        "stateFile": "/tmp/interop-state.json",
        "runTimeoutSeconds": interop_feature.DEFAULT_RUN_TIMEOUT_SECONDS,
    }
    assert set(status) == {"platforms"}
    assert status["platforms"][0]["platform"] == "opencode"
    assert status["platforms"][0]["version"] == "legacy-v1"
