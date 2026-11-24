"""Hermes harness runs retry once when its provider names a replacement model."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from machina import interop


@pytest.fixture(autouse=True)
def _clear_hermes_model_cache():
    """The last-good model is process state; each test starts fresh."""
    interop._LAST_GOOD_HERMES_MODEL.clear()
    yield
    interop._LAST_GOOD_HERMES_MODEL.clear()


def test_suggested_retry_command_parses_the_provider_hint():
    command = ["/usr/bin/hermes", "--oneshot=hello"]
    stderr = (
        "Provider said: HTTP 404: This model's free period has ended. To continue "
        "using Step 5 Preview, switch to 'stepfun/step-5-preview'."
    )
    assert interop._suggested_retry_command("hermes", command, "", stderr) == [
        "/usr/bin/hermes",
        "-m",
        "stepfun/step-5-preview",
        "--oneshot=hello",
    ]
    # No hint, an explicit model, and a different platform are all no-ops.
    assert interop._suggested_retry_command("hermes", command, "", "boom") is None
    assert (
        interop._suggested_retry_command(
            "hermes", ["hermes", "-m", "x", "--oneshot=hello"], "", stderr
        )
        is None
    )
    assert interop._suggested_retry_command("codex", command, "", stderr) is None


def test_run_task_does_not_retry_or_annotate_an_unrelated_failure(monkeypatch):
    calls: list[list[str]] = []

    def _run(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(
            returncode=1,
            output="",
            stderr="Error loading config.toml: key with no value",
            timed_out=False,
            truncated=False,
        )

    monkeypatch.setattr(interop, "resolve_platform", lambda key: SimpleNamespace(label="Codex"))
    monkeypatch.setattr(interop, "_which", lambda spec: "/usr/bin/codex")
    monkeypatch.setattr(
        interop,
        "_literal_prompt_invocation",
        lambda key, prefix, binary, prompt: ([binary, "exec", "-"], prompt),
    )
    monkeypatch.setattr(interop, "_child_env", lambda spec: {})
    monkeypatch.setattr(interop, "_run", _run)

    result = interop.run_task("codex", "hello")

    assert result.ok is False
    assert len(calls) == 1
    assert "Retried" not in result.stderr


def test_run_task_falls_back_to_a_free_model_when_credits_are_exhausted(monkeypatch):
    calls: list[list[str]] = []

    def _run(argv, **_kwargs):
        calls.append(list(argv))
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=2,
                output="",
                stderr="HTTP 404: free period ended. switch to 'stepfun/step-5-preview'.",
                timed_out=False,
                truncated=False,
            )
        return SimpleNamespace(
            returncode=0,
            output="free result",
            stderr="",
            timed_out=False,
            truncated=False,
        )

    monkeypatch.setattr(interop, "resolve_platform", lambda key: SimpleNamespace(label="Hermes"))
    monkeypatch.setattr(interop, "_which", lambda spec: "/usr/bin/hermes")
    monkeypatch.setattr(
        interop,
        "_literal_prompt_invocation",
        lambda key, prefix, binary, prompt: ([binary, f"--oneshot={prompt}"], None),
    )
    monkeypatch.setattr(interop, "_child_env", lambda spec: {})
    monkeypatch.setattr(interop, "_run", _run)
    monkeypatch.setattr(interop, "_hermes_free_models", lambda: ["stepfun/step-3.7-flash:free"])

    result = interop.run_task("hermes", "research planes")

    assert result.ok is True
    assert result.stdout == "free result"
    # The provider's suggested model is paid ("stepfun/step-5-preview"), so a
    # credit failure skips it and goes straight to a free model.
    assert calls == [
        ["/usr/bin/hermes", "--oneshot=research planes"],
        ["/usr/bin/hermes", "-m", "stepfun/step-3.7-flash:free", "--oneshot=research planes"],
    ]


def test_run_task_retries_hermes_with_the_suggested_model(monkeypatch):
    calls: list[list[str]] = []

    def _run(argv, **_kwargs):
        calls.append(list(argv))
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=2,
                output="",
                stderr=("Provider said: HTTP 404: ... switch to 'stepfun/step-5-preview'."),
                timed_out=False,
                truncated=False,
            )
        return SimpleNamespace(
            returncode=0,
            output="research complete",
            stderr="",
            timed_out=False,
            truncated=False,
        )

    monkeypatch.setattr(interop, "resolve_platform", lambda key: SimpleNamespace(label="Hermes"))
    monkeypatch.setattr(interop, "_which", lambda spec: "/usr/bin/hermes")
    monkeypatch.setattr(
        interop,
        "_literal_prompt_invocation",
        lambda key, prefix, binary, prompt: ([binary, f"--oneshot={prompt}"], None),
    )
    monkeypatch.setattr(interop, "_child_env", lambda spec: {})
    monkeypatch.setattr(interop, "_run", _run)

    result = interop.run_task("hermes", "research planes")

    assert result.ok is True
    assert result.stdout == "research complete"
    assert calls == [
        ["/usr/bin/hermes", "--oneshot=research planes"],
        ["/usr/bin/hermes", "-m", "stepfun/step-5-preview", "--oneshot=research planes"],
    ]
    assert result.command == calls[1]
