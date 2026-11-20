"""Terminal sandbox backend selection."""

from __future__ import annotations

import pytest

from machina.tools.sandbox import (
    ALL_BACKENDS,
    container_backend,
    is_container_backend,
    is_isolated_backend,
    is_wasm_backend,
    isolation_required_error,
    runtime_args,
    validate_backend,
)


def test_backend_validation_is_closed() -> None:
    assert validate_backend("GVISOR") == "gvisor"
    with pytest.raises(ValueError, match="terminal sandbox must be one of"):
        validate_backend("none")
    assert ALL_BACKENDS == {
        "disabled",
        "local",
        "docker",
        "gvisor",
        "kata",
        "firecracker",
        "wasm",
    }


def test_runtime_backends_are_container_backends() -> None:
    assert container_backend("docker") is True
    assert container_backend("gvisor") is True
    assert container_backend("kata") is True
    assert container_backend("firecracker") is True
    assert container_backend("local") is False
    assert is_container_backend("gvisor") is True
    assert is_container_backend("none") is False
    assert is_wasm_backend("wasm") is True
    assert is_isolated_backend("wasm") is True
    assert is_isolated_backend("local") is False


def test_gvisor_and_kata_runtime_flags(monkeypatch) -> None:
    monkeypatch.delenv("MACHINA_GVISOR_RUNTIME", raising=False)
    monkeypatch.delenv("MACHINA_KATA_RUNTIME", raising=False)
    assert runtime_args("docker") == []
    assert runtime_args("gvisor") == ["--runtime", "runsc"]
    assert runtime_args("kata") == ["--runtime", "kata-runtime"]
    assert runtime_args("firecracker") == ["--runtime", "kata-runtime"]
    monkeypatch.setenv("MACHINA_GVISOR_RUNTIME", "custom-runsc")
    assert runtime_args("gvisor") == ["--runtime", "custom-runsc"]


def test_isolation_error_names_the_configured_backend() -> None:
    assert "terminal_sandbox=docker, gvisor, kata" in isolation_required_error("local")
    assert "configured: local" in isolation_required_error("local")
