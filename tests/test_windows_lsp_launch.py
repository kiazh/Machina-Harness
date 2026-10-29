"""Bundled language servers must launch through Node on Windows."""

import asyncio
import os
from pathlib import Path

import pytest

from machina.workspace.lsp import LspFraming, resolve_profile
from machina.workspace.service import WorkspaceService


@pytest.mark.skipif(os.name != "nt", reason="Windows npm shim resolution")
def test_explicit_npm_shim_uses_node():
    root = Path(__file__).resolve().parents[1] / "ui" / "node_modules" / ".bin"
    shim = root / "pyright-langserver.cmd"
    if not shim.is_file():
        pytest.skip("bundled pyright is not installed")
    profile = resolve_profile("python", str(shim))
    assert profile is not None
    assert Path(profile.executable).name.lower() in {"node", "node.exe"}
    assert Path(profile.args[0]).name == "langserver.index.js"


@pytest.mark.skipif(os.name != "nt", reason="Windows drive URI conversion")
def test_windows_file_uri_stays_inside_workspace(tmp_path):
    service = WorkspaceService()
    service.open(str(tmp_path), trust=True)
    try:
        assert service._uri_to_rel((tmp_path / "example.py").as_uri()) == "example.py"
    finally:
        service.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess launcher check")
@pytest.mark.parametrize("language", ["python", "typescript"])
@pytest.mark.asyncio
async def test_bundled_lsp_initializes_on_windows(language):
    profile = resolve_profile(language)
    if profile is None:
        pytest.skip("Node language server is not installed")
    child = await asyncio.create_subprocess_exec(
        profile.executable,
        *profile.args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"processId": None, "rootUri": None, "capabilities": {}},
        }
        child.stdin.write(LspFraming.encode(request))
        await child.stdin.drain()
        framing = LspFraming()
        while True:
            data = await asyncio.wait_for(child.stdout.read(4096), timeout=15)
            assert data, "language server exited before initialize"
            for message in framing.feed(data):
                if message.get("id") == 1:
                    assert isinstance(message.get("result"), dict)
                    return
    finally:
        if child.returncode is None:
            child.terminate()
        try:
            await asyncio.wait_for(child.communicate(), timeout=3)
        except TimeoutError:
            child.kill()
            await child.communicate()
