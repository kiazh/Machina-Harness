"""Local Ollama is discoverable by the connected-model router.

Regression: routing called ``OllamaProvider().reachable()`` although no such
method existed. The swallowed AttributeError reported "daemon unavailable"
forever, so a running Ollama could never be selected.
"""

from __future__ import annotations

import socket

from machina.core.provider.ollama import OllamaProvider
from machina.core.routing import catalog


def _listening_socket() -> socket.socket:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server


def test_reachable_accepts_a_listening_endpoint():
    server = _listening_socket()
    try:
        port = server.getsockname()[1]
        assert OllamaProvider.reachable(f"http://127.0.0.1:{port}", timeout=0.2) is True
    finally:
        server.close()


def test_reachable_rejects_a_closed_endpoint():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    assert OllamaProvider.reachable(f"http://127.0.0.1:{port}", timeout=0.2) is False


def test_provider_ready_reports_a_running_daemon(monkeypatch):
    monkeypatch.setattr(OllamaProvider, "reachable", staticmethod(lambda *a, **k: True))

    ready, detail = catalog._provider_ready("ollama", object(), probe_kiro=False)

    assert ready is True
    assert detail == "daemon reachable"
