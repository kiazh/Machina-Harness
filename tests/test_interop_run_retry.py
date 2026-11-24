"""Hermes harness runs retry once when its provider names a replacement model."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from machina import interop


@pytest.fixture(autouse=True)
def _clear_hermes_model_cache(monkeypatch):
    """Process state and local .env overrides must not leak between tests."""
    interop._LAST_GOOD_HERMES_MODEL.clear()
    for name in (
        "MACHINA_HARNESS_MODEL",
        "MACHINA_HARNESS_MODEL_HERMES",
        "MACHINA_HARNESS_MODEL_OPENCODE",
        "MACHINA_HARNESS_MODEL_CODEX",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    interop._LAST_GOOD_HERMES_MODEL.clear()


def test_suggested_retry_command_parses_the_provider_hint():
    command = ["/usr/bin/hermes", "--oneshot=hello"]
    stderr = (
        "Provider said: HTTP 404: This model's free period has ended. To continue "
        "using DeepSeek, switch to 'deepseek/deepseek-chat'."
    )
    assert interop._suggested_retry_command("hermes", command, "", stderr) == [
        "/usr/bin/hermes",
        "-m",
        "deepseek/deepseek-chat",
        "--oneshot=hello",
    ]
    # Another vendor's model is never adopted automatically.
    assert (
        interop._suggested_retry_command(
            "hermes", command, "", "switch to 'stepfun/step-5-preview'."
        )
        is None
    )
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
    monkeypatch.setattr(interop, "_hermes_free_models", lambda: ["deepseek/deepseek-chat:free"])

    result = interop.run_task("hermes", "research planes")

    assert result.ok is True
    assert result.stdout == "free result"
    # The provider's suggested model is paid, so a credit failure skips it and
    # goes straight to a free DeepSeek model.
    assert calls == [
        ["/usr/bin/hermes", "--oneshot=research planes"],
        ["/usr/bin/hermes", "-m", "deepseek/deepseek-chat:free", "--oneshot=research planes"],
    ]


def test_run_task_retries_hermes_with_the_suggested_model(monkeypatch):
    calls: list[list[str]] = []

    def _run(argv, **_kwargs):
        calls.append(list(argv))
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=2,
                output="",
                stderr=("Provider said: HTTP 404: ... switch to 'deepseek/deepseek-chat'."),
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
        ["/usr/bin/hermes", "-m", "deepseek/deepseek-chat", "--oneshot=research planes"],
    ]
    assert result.command == calls[1]


def test_run_task_hermes_streams_tokens_as_one_answer(monkeypatch):
    """Token-level ``text`` events stay one continuous answer, not one line each."""
    events = [
        '{"type": "system", "subtype": "init", "model": "m", "session_id": "s1"}',
        '{"type": "text", "text": "What"}',
        '{"type": "text", "text": " kind of engines"}',
        '{"type": "text", "text": " do you mean?"}',
        '{"type": "result", "session_id": "s1", "exit_code": 0, "text": "What kind of engines do you mean?"}',
    ]
    seen: list[str] = []
    sessions: list[str] = []

    def fake_run(argv, **kwargs):
        callback = kwargs.get("on_output")
        if callback is not None:
            for event in events:
                callback("stdout", event + "\n")
        return SimpleNamespace(
            returncode=0,
            output="\n".join(events) + "\n",
            stderr="",
            timed_out=False,
            truncated=False,
        )

    monkeypatch.setattr(interop, "resolve_platform", lambda key: SimpleNamespace(label="Hermes"))
    monkeypatch.setattr(interop, "_which", lambda spec: "/usr/bin/hermes")
    monkeypatch.setattr(interop, "_child_env", lambda spec: {})
    monkeypatch.setattr(interop, "_run", fake_run)

    result = interop.run_task(
        "hermes",
        "engines?",
        on_output=lambda source, text: seen.append(text),
        on_session_id=sessions.append,
    )

    assert result.ok and result.stdout == "What kind of engines do you mean?"
    shown = "".join(seen)
    assert "What kind of engines do you mean?" in " ".join(shown.split())
    assert "What\n" not in shown, "token events must not be split into lines"
    assert sessions == ["s1"]


def test_run_task_hermes_resumes_a_previous_session(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["stdin"] = kwargs.get("stdin_text")
        return SimpleNamespace(returncode=0, output="", stderr="", timed_out=False, truncated=False)

    monkeypatch.setattr(interop, "resolve_platform", lambda key: SimpleNamespace(label="Hermes"))
    monkeypatch.setattr(interop, "_which", lambda spec: "/usr/bin/hermes")
    monkeypatch.setattr(interop, "_child_env", lambda spec: {})
    monkeypatch.setattr(interop, "_run", fake_run)

    interop.run_task("hermes", "and the follow-up?", resume="20261124_abc")

    assert "--resume" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--resume") + 1] == "20261124_abc"
    assert seen["stdin"] == "and the follow-up?"


def test_harness_model_override_applies_before_any_retry(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        return SimpleNamespace(
            returncode=0, output="ok", stderr="", timed_out=False, truncated=False
        )

    monkeypatch.setattr(interop, "resolve_platform", lambda key: SimpleNamespace(label="OpenCode"))
    monkeypatch.setattr(interop, "_which", lambda spec: "/usr/bin/opencode")
    monkeypatch.setattr(interop, "_child_env", lambda spec: {})
    monkeypatch.setattr(interop, "_run", fake_run)
    monkeypatch.setenv("MACHINA_HARNESS_MODEL_OPENCODE", "deepseek/deepseek-chat")

    interop.run_task("opencode", "hello")

    assert "-m" in seen["argv"]
    assert seen["argv"][seen["argv"].index("-m") + 1] == "deepseek/deepseek-chat"
