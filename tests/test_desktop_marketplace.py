"""Desktop marketplace boundaries: public catalog, reviewed bytes, actor ownership."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from machina.gateway.errors import INVALID_PARAMS, NOT_FOUND, UNAUTHORIZED, RpcError


class GatewayStub:
    def __init__(self, session_id="session-a", agent_id="agent-a"):
        self.session = SimpleNamespace(id=session_id, agent_id=agent_id)

    def require_db(self):
        pass

    async def get_session(self, params):
        return self.session


@pytest.mark.asyncio
async def test_installation_review_works_without_conversation_and_stays_gateway_owned(monkeypatch):
    from machina.gateway.features import marketplace as module

    digest = "c" * 64
    monkeypatch.setattr(
        module,
        "_stage",
        AsyncMock(
            return_value=(
                {"digest": digest, "skills": [{"name": "demo", "content": "read me", "files": []}]},
                Path(__file__).parent,
            )
        ),
    )
    activate = AsyncMock(return_value={"installed": ["demo"]})
    monkeypatch.setattr(module, "_activate", activate)
    gateway = GatewayStub()
    gateway.get_session = AsyncMock(side_effect=AssertionError("must not create or require a chat"))
    review = await module.skills_marketplace_preview(
        gateway, {"source": "owner/repo", "skill": "demo"}
    )
    assert review["sessionId"] is None
    params = {"reviewId": review["reviewId"], "digest": digest, "confirmed": True}
    with pytest.raises(RpcError) as exc:
        await module.skills_marketplace_install(GatewayStub(), params)
    assert exc.value.code == NOT_FOUND
    for patch in ({"confirmed": False}, {"digest": "d" * 64}):
        with pytest.raises(RpcError) as exc:
            await module.skills_marketplace_install(gateway, {**params, **patch})
        assert exc.value.code == INVALID_PARAMS
    activate.assert_not_called()
    assert (await module.skills_marketplace_install(gateway, params))["installed"] == ["demo"]
    gateway.get_session.assert_not_called()


@pytest.mark.asyncio
async def test_marketplace_uses_public_cli_search_and_rejects_foreign_sources(monkeypatch):
    from machina.gateway.features import marketplace as module

    captured = []

    def respond(request):
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "skills": [
                    {
                        "id": "vercel-labs/agent-skills/web-design-guidelines",
                        "name": "web-design-guidelines",
                        "source": "vercel-labs/agent-skills",
                        "installs": 42,
                    },
                    {
                        "id": "bad",
                        "name": "bad",
                        "source": "https://attacker.test/code",
                        "installs": 99,
                    },
                ]
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    result = await module.skills_marketplace_search(
        GatewayStub(), {"query": "web design", "limit": 10}
    )
    assert str(captured[0].url).startswith("https://skills.sh/api/search?")
    assert captured[0].url.params["q"] == "web design"
    assert result["provider"] == "skills.sh"
    assert len(result["skills"]) == 1
    assert result["skills"][0]["repository"] == "https://github.com/vercel-labs/agent-skills"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["../escape/repo", "a/b;whoami", "https://evil.test/a/b", "a/b/tree/main", "a\\b/c"]
)
async def test_marketplace_source_validation_precedes_network(source, monkeypatch):
    from machina.gateway.features import marketplace as module

    stage = AsyncMock()
    monkeypatch.setattr(module, "_stage", stage)
    with pytest.raises(RpcError) as exc:
        await module.skills_marketplace_preview(
            GatewayStub(), {"source": source, "skill": "demo", "sessionId": "session-a"}
        )
    assert exc.value.code == INVALID_PARAMS
    stage.assert_not_called()


@pytest.mark.asyncio
async def test_install_requires_same_session_agent_and_exact_confirmed_digest(monkeypatch):
    from machina.gateway.features import marketplace as module

    digest = "a" * 64
    monkeypatch.setattr(
        module,
        "_stage",
        AsyncMock(
            return_value=(
                {
                    "digest": digest,
                    "source": "https://github.com/owner/repo",
                    "revision": "rev",
                    "skills": [{"name": "demo", "content": "read me", "files": []}],
                },
                Path(__file__).parent,
            )
        ),
    )
    activate = AsyncMock(return_value={"installed": ["demo"], "usable": True, "outcomes": []})
    monkeypatch.setattr(module, "_activate", activate)
    gateway = GatewayStub()
    review = await module.skills_marketplace_preview(
        gateway, {"source": "owner/repo", "skill": "demo", "sessionId": "session-a"}
    )
    params = {
        "reviewId": review["reviewId"],
        "digest": digest,
        "sessionId": "session-a",
        "confirmed": True,
    }

    gateway.session = SimpleNamespace(id="session-b", agent_id="agent-a")
    with pytest.raises(RpcError) as exc:
        await module.skills_marketplace_install(gateway, params)
    assert exc.value.code == UNAUTHORIZED
    gateway.session = SimpleNamespace(id="session-a", agent_id="agent-b")
    with pytest.raises(RpcError) as exc:
        await module.skills_marketplace_install(gateway, params)
    assert exc.value.code == UNAUTHORIZED
    gateway.session = SimpleNamespace(id="session-a", agent_id="agent-a")
    for patch in ({"confirmed": False}, {"confirmed": "true"}, {"digest": "b" * 64}):
        with pytest.raises(RpcError) as exc:
            await module.skills_marketplace_install(gateway, {**params, **patch})
        assert exc.value.code == INVALID_PARAMS
    activate.assert_not_called()
    assert (await module.skills_marketplace_install(gateway, params))["installed"] == ["demo"]
    with pytest.raises(RpcError) as exc:
        await module.skills_marketplace_install(gateway, params)
    assert exc.value.code == NOT_FOUND


@pytest.mark.asyncio
async def test_mcp_edit_config_is_nonsecret_and_owner_checked():
    from machina.connectors.models import ConnectorConfig
    from machina.gateway.features.connectors import mcp_config

    config = ConnectorConfig(
        id="demo",
        owner="agent-a",
        command="node",
        argv=["server.js"],
        cwd="C:/project",
        secret_refs=["API_TOKEN"],
    )
    service = SimpleNamespace(refresh=AsyncMock(), registry=SimpleNamespace(get=lambda _: config))
    gateway = GatewayStub()
    gateway.runtime = SimpleNamespace(get_connector_service=lambda: service)
    result = await mcp_config(gateway, {"id": "demo", "sessionId": "session-a"})
    assert result["config"]["argv"] == ["server.js"]
    assert result["config"]["secret_refs"] == ["API_TOKEN"]
    assert "owner" not in result["config"] and "revision" not in result["config"]
    config.owner = "other-agent"
    with pytest.raises(RpcError) as exc:
        await mcp_config(gateway, {"id": "demo", "sessionId": "session-a"})
    assert exc.value.code == UNAUTHORIZED
