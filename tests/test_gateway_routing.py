"""Gateway route-preview and inventory contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from machina.gateway.errors import RpcError
from machina.gateway.features import routing as routing_feature


class _Decision:
    def to_dict(self):
        return {"mode": "multi_model", "planner": "test/planner"}

    def explain(self):
        return "planner=test/planner"


@pytest.mark.asyncio
async def test_model_route_validates_and_returns_explanation(monkeypatch) -> None:
    seen: dict = {}

    def fake_route(session, prompt, **kwargs):
        seen.update(session=session, prompt=prompt, kwargs=kwargs)
        return _Decision()

    monkeypatch.setattr(routing_feature, "route_from_session", fake_route)
    payload = await routing_feature.model_route(
        None,
        {"prompt": "Build a page", "mode": "multi_model", "workerCount": 4},
    )

    assert payload["explanation"] == "planner=test/planner"
    assert payload["route"]["mode"] == "multi_model"
    assert seen["prompt"] == "Build a page"
    assert seen["kwargs"]["worker_count"] == 4


@pytest.mark.asyncio
async def test_model_route_rejects_bad_mode_and_worker_count() -> None:
    with pytest.raises(RpcError):
        await routing_feature.model_route(None, {"prompt": "x", "mode": "serial"})
    with pytest.raises(RpcError):
        await routing_feature.model_route(None, {"prompt": "x", "workerCount": 65})
    with pytest.raises(RpcError):
        await routing_feature.model_route(None, {"prompt": "  "})


@pytest.mark.asyncio
async def test_providers_list_reports_multi_auto_mode(monkeypatch) -> None:
    from machina.gateway.features import providers_feature

    values = {
        "orchestration_mode": "multi_model",
        "model_selection": "auto",
        "auto_parallel_delegation": True,
        "single_model_auto_parallel": False,
    }
    monkeypatch.setattr(providers_feature.config, "get", values.get)
    monkeypatch.setattr(providers_feature.store, "provider_names", lambda: ())

    class Gateway:
        provider = "deepseek"

        def require_db(self):
            return None

    payload = await providers_feature.providers_list(Gateway(), {})

    assert payload["routing"] == {
        "mode": "multi_auto",
        "modelSelection": "auto",
        "autoParallel": True,
        "singleModelAutoParallel": False,
    }

    values["orchestration_mode"] = "single_model"
    payload = await providers_feature.providers_list(Gateway(), {})
    assert payload["routing"]["mode"] == "single"


@pytest.mark.asyncio
async def test_opencode_session_models_use_entitlement_list(monkeypatch) -> None:
    from machina.core.provider import opencode_session
    from machina.gateway.features import providers_feature

    monkeypatch.setattr(
        opencode_session,
        "available_models",
        lambda force=False: ("opencode/big-pickle", "deepseek/deepseek-flash"),
    )
    payload = await providers_feature._opencode_session_models(
        "opencode_session", False
    )

    assert payload["source"] == "opencode-models"
    assert [model["id"] for model in payload["models"]] == [
        "opencode/big-pickle",
        "deepseek/deepseek-flash",
    ]
    assert all(model["tools"] is False for model in payload["models"])


@pytest.mark.asyncio
async def test_model_inventory_is_read_only_serializable(monkeypatch) -> None:
    inventory = SimpleNamespace(to_dict=lambda: {"profiles": [], "providers": {}})
    monkeypatch.setattr(routing_feature, "build_inventory", lambda _config: inventory)

    payload = await routing_feature.model_inventory(None, {})

    assert payload == {"inventory": {"profiles": [], "providers": {}}}
