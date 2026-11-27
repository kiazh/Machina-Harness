from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from machina.gateway.features import changes
from machina.workspace import reviews, snapshots


def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("MACHINA_HOME", str(home))
    monkeypatch.setenv("MACHINA_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("MACHINA_SNAPSHOTS_ENABLED", "1")
    return workspace


def _snapshot(workspace: Path) -> dict[str, object]:
    target = workspace / "src" / "app.py"
    target.parent.mkdir(parents=True)
    turn = snapshots.begin_turn(
        session_id="session-model",
        workspace=workspace,
        prompt="Change the app",
        provider="hermes",
        model="qwen/qwen4-max",
    )
    assert turn is not None
    snapshots.record_before_write(target)
    target.write_text("run(shell=True)\n", encoding="utf-8")
    snapshots.end_turn(turn)
    found = snapshots.latest_snapshot(workspace=workspace, session_id="session-model")
    assert found is not None
    return found[0]


class _Session:
    provider = "hermes"
    model = "qwen/qwen4-max"
    state: dict[str, str] = {}


class _Gateway:
    provider = "hermes"
    model = "qwen/qwen4-max"

    async def get_session(self, params: dict[str, object]) -> _Session:
        return _Session()


class _Provider:
    model = "qwen/qwen4-max"

    def __init__(self, content: str = '{"findings":[]}', wait: bool = False) -> None:
        self.content = content
        self.wait = wait
        self.closed = False

    async def complete(self, **kwargs):
        if self.wait:
            await asyncio.sleep(3600)
        return SimpleNamespace(
            content=self.content,
            raw={"choices": [{"finish_reason": "stop"}]},
        )

    async def close(self) -> None:
        self.closed = True


async def _settle() -> None:
    for _ in range(200):
        if not changes._REVIEW_TASKS:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("review task did not settle")


def test_gateway_model_review_queues_runs_and_closes_the_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    payload = _snapshot(workspace)
    monkeypatch.setattr(changes, "configured_workspace", lambda: workspace)
    provider = _Provider(
        '{"findings":[{"severity":"high","title":"Shell injection",'
        '"body":"The command is interpolated.","file":"src/app.py","line":1}]}'
    )
    monkeypatch.setattr("machina.core.provider.get_provider", lambda **kwargs: provider)

    async def scenario():
        started = await changes.review_start(
            _Gateway(),
            {
                "snapshotId": payload["snapshot_id"],
                "sessionId": "session-model",
                "engine": "model",
                "force": True,
            },
        )
        assert started["review"]["status"] == "queued"
        assert started["review"]["engine"] == "model"
        await _settle()
        return reviews.load(workspace, str(payload["snapshot_id"]))

    review = asyncio.run(scenario())
    assert review is not None
    assert review["status"] == "complete"
    assert review["findings"][0]["line"] == 1
    assert provider.closed is True


def test_gateway_cancel_stops_a_running_model_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    payload = _snapshot(workspace)
    monkeypatch.setattr(changes, "configured_workspace", lambda: workspace)
    provider = _Provider(wait=True)
    monkeypatch.setattr("machina.core.provider.get_provider", lambda **kwargs: provider)

    async def scenario():
        started = await changes.review_start(
            _Gateway(),
            {
                "snapshotId": payload["snapshot_id"],
                "sessionId": "session-model",
                "engine": "model",
                "force": True,
            },
        )
        assert started["review"]["status"] == "queued"
        await asyncio.sleep(0)
        cancelled = await changes.review_cancel(
            _Gateway(),
            {
                "snapshotId": payload["snapshot_id"],
                "sessionId": "session-model",
            },
        )
        assert cancelled["review"]["status"] == "cancelled"
        await _settle()
        return reviews.load(workspace, str(payload["snapshot_id"]))

    review = asyncio.run(scenario())
    assert review is not None
    assert review["status"] == "cancelled"
    assert provider.closed is True


def test_gateway_rules_review_replaces_a_running_model_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    payload = _snapshot(workspace)
    monkeypatch.setattr(changes, "configured_workspace", lambda: workspace)
    provider = _Provider(wait=True)
    monkeypatch.setattr("machina.core.provider.get_provider", lambda **kwargs: provider)

    async def scenario():
        await changes.review_start(
            _Gateway(),
            {
                "snapshotId": payload["snapshot_id"],
                "sessionId": "session-model",
                "engine": "model",
                "force": True,
            },
        )
        await asyncio.sleep(0)
        started = await changes.review_start(
            _Gateway(),
            {
                "snapshotId": payload["snapshot_id"],
                "sessionId": "session-model",
                "engine": "rules",
                "force": True,
            },
        )
        await _settle()
        return started["review"], reviews.load(workspace, str(payload["snapshot_id"]))

    started, stored = asyncio.run(scenario())
    assert started["engine"] == "rules"
    assert stored is not None and stored["engine"] == "rules"
    assert provider.closed is True


def test_gateway_provider_failure_is_a_failed_review_not_a_clean_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = _workspace(monkeypatch, tmp_path)
    payload = _snapshot(workspace)
    monkeypatch.setattr(changes, "configured_workspace", lambda: workspace)

    def fail(**kwargs):
        raise ValueError("provider key missing")

    monkeypatch.setattr("machina.core.provider.get_provider", fail)

    async def scenario():
        return await changes.review_start(
            _Gateway(),
            {
                "snapshotId": payload["snapshot_id"],
                "sessionId": "session-model",
                "engine": "model",
                "force": True,
            },
        )

    started = asyncio.run(scenario())
    assert started["review"]["status"] == "failed"
    assert "provider key missing" in started["review"]["error"]
    assert "failed" in started["review"]["summary"]
