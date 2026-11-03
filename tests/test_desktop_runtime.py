import json

import pytest


@pytest.mark.asyncio
async def test_runtime_capabilities_are_available_before_database_initialization(monkeypatch):
    from machina.core.shell_jobs import shell_jobs
    from machina.gateway.server import Gateway

    monkeypatch.setenv("MACHINA_GATEWAY_TOKEN", "desktop-test-token")
    frames = []
    gateway = Gateway(frames.append, owns_db=False)
    try:
        await gateway.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "runtime.capabilities",
                    "params": {"token": "desktop-test-token"},
                }
            )
        )
        result = frames[-1]["result"]
        assert result["protocolVersion"] == 2
        assert result["storageProfile"] == "postgres"
        assert result["database"] == "not-initialized"
        assert "prompt.steer" in result["methods"]
        assert "workspace.documentChange" in result["methods"]
        assert result["engineVersion"]
        assert "desktop-test-token" not in json.dumps(result)
        assert not gateway._db_ready
    finally:
        shell_jobs.listeners.discard(gateway._shell_listener)


@pytest.mark.asyncio
async def test_runtime_capabilities_rejects_unauthenticated_requests(monkeypatch):
    from machina.core.shell_jobs import shell_jobs
    from machina.gateway.errors import UNAUTHORIZED
    from machina.gateway.server import Gateway

    monkeypatch.setenv("MACHINA_GATEWAY_TOKEN", "desktop-test-token")
    frames = []
    gateway = Gateway(frames.append, owns_db=False)
    try:
        await gateway.handle_line(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "runtime.capabilities", "params": {}})
        )
        assert frames[-1]["error"]["code"] == UNAUTHORIZED
        assert not gateway._db_ready
    finally:
        shell_jobs.listeners.discard(gateway._shell_listener)
