"""Status probes report readiness without taking over runtime startup."""

from __future__ import annotations

import asyncio

from machina.core import runtime as runtime_module


def test_probe_status_reports_readiness_without_starting(monkeypatch):
    runtime = runtime_module.RuntimeServices()
    monkeypatch.setattr(
        runtime_module,
        "_detect_providers",
        lambda: {"chat_provider": "ready", "chat_provider_reason": ""},
    )
    monkeypatch.setattr(runtime_module, "_detect_sandbox", lambda: {"sandbox": "available"})
    monkeypatch.setattr(runtime_module, "_detect_toggles", lambda: {"memory_config": "enabled"})

    report = asyncio.run(runtime.probe_status())

    assert report["chat_provider"] == "ready"
    assert report["sandbox"] == "available"
    assert runtime._started is False
    # The probe result is not latched as the lifecycle report.
    assert "chat_provider" not in runtime.status()
