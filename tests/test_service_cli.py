"""Service lifecycle helpers are lease-based, not PID-file based."""

from __future__ import annotations

from machina.cli import service
from machina.db import lifecycle


def test_only_serve_leases_are_service_clients(monkeypatch) -> None:
    monkeypatch.setattr(
        lifecycle,
        "live_clients",
        lambda: [
            {"pid": 10, "role": "serve", "child_pid": 0},
            {"pid": 20, "role": "tui", "child_pid": 21},
            {"pid": 30, "role": "serve", "child_pid": 31},
        ],
    )
    clients = service._serve_clients()
    assert [service._serve_pid(item) for item in clients] == [10, 31]


def test_service_metadata_uses_machina_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MACHINA_HOME", str(tmp_path))
    assert service._metadata_path() == tmp_path / "run" / "service.json"


def test_health_url_does_not_probe_wildcard_bind(monkeypatch) -> None:
    assert service._health_url("0.0.0.0", 8123) == "http://127.0.0.1:8123/health"
    assert service._health_url("::", 8123) == "http://127.0.0.1:8123/health"


def test_metadata_pid_requires_health(monkeypatch) -> None:
    monkeypatch.setattr(service, "_read_metadata", lambda: {"pid": 4242, "host": "127.0.0.1", "port": 9000})
    monkeypatch.setattr(service.lifecycle, "_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(service, "_health", lambda host, port: "unreachable (ConnectError)")
    assert service._verified_metadata_client("127.0.0.1", 9000) is None
    monkeypatch.setattr(service, "_health", lambda host, port: "healthy v1")
    client = service._verified_metadata_client("127.0.0.1", 9000)
    assert client is not None
    assert client["pid"] == 4242
