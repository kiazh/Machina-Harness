import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest


def test_desktop_bootstrap_isolates_protocol_before_managed_database_setup(monkeypatch):
    from machina.desktop import bootstrap

    calls = []
    entry = SimpleNamespace(
        isolate_protocol_streams=lambda: calls.append("isolate"),
        main=lambda: calls.append("serve"),
    )
    monkeypatch.setattr(bootstrap, "gateway_entry", lambda: entry)
    monkeypatch.setattr(bootstrap, "acquire", lambda role: calls.append(role) or "lease")
    monkeypatch.setattr(bootstrap, "release", lambda lease: calls.append(f"release:{lease}"))
    bootstrap.main()
    assert calls == ["isolate", "desktop", "serve", "release:lease"]


def test_desktop_bootstrap_releases_its_lease_when_gateway_fails(monkeypatch):
    from machina.desktop import bootstrap

    calls = []

    def failed():
        raise RuntimeError("gateway failed")

    entry = SimpleNamespace(isolate_protocol_streams=lambda: None, main=failed)
    monkeypatch.setattr(bootstrap, "gateway_entry", lambda: entry)
    monkeypatch.setattr(bootstrap, "acquire", lambda role: "owned-lease")
    monkeypatch.setattr(bootstrap, "release", calls.append)
    with pytest.raises(RuntimeError, match="gateway failed"):
        bootstrap.main()
    assert calls == ["owned-lease"]


def test_desktop_bootstrap_does_not_release_an_unacquired_lease(monkeypatch):
    from machina.desktop import bootstrap

    calls = []

    def denied(role):
        raise PermissionError("lease directory")

    monkeypatch.setattr(
        bootstrap, "gateway_entry", lambda: SimpleNamespace(isolate_protocol_streams=lambda: None)
    )
    monkeypatch.setattr(bootstrap, "acquire", denied)
    monkeypatch.setattr(bootstrap, "release", calls.append)
    with pytest.raises(PermissionError):
        bootstrap.main()
    assert calls == []


@pytest.mark.asyncio
async def test_real_desktop_entrypoint_preserves_authenticated_json_channel():
    env = {
        **os.environ,
        "MACHINA_DB_MANAGE": "0",
        "MACHINA_GATEWAY_TOKEN": "desktop-channel-test",
        "PYTHONIOENCODING": "utf-8",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "machina.desktop",
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "runtime.capabilities",
            "params": {"token": "desktop-channel-test"},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "shutdown",
            "params": {"token": "desktop-channel-test"},
        },
    ]
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(("\n".join(map(json.dumps, requests)) + "\n").encode()), 25
        )
        assert process.returncode == 0, stderr.decode(errors="replace")[-1000:]
        frames = [json.loads(line) for line in stdout.decode("utf-8").splitlines()]
        reply = next(frame for frame in frames if frame.get("id") == 1)
        assert reply["result"]["protocolVersion"] == 2
        assert reply["result"]["database"] == "not-initialized"
        assert "desktop-channel-test" not in stdout.decode()
        assert "desktop-channel-test" not in stderr.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
