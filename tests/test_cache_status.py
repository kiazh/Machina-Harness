"""Prompt-cache capability reporting must distinguish unsupported from misses."""

from __future__ import annotations

from machina.core.provider.prompt_cache import cache_status


def test_kiro_reports_unsupported_not_zero_cache() -> None:
    status = cache_status("kiro", "claude-sonnet-5.5")

    assert status["supported"] is False
    assert status["mode"] == "none"
    assert "not reported" in status["note"]
    assert "kiro" in status["note"]


def test_openai_reports_its_implicit_cache_contract() -> None:
    status = cache_status("openai", "gpt-6.1-sol")

    assert status["supported"] is True
    assert status["mode"] == "implicit"
    assert "prompt_tokens_details.cached_tokens" in status["usageFields"]


def test_opencode_session_reports_implicit_cache_usage() -> None:
    status = cache_status("opencode_session", "deepseek/deepseek-flash")

    assert status["supported"] is True
    assert status["mode"] == "implicit"
    assert "cache_read_tokens" in status["usageFields"]


def test_unknown_provider_is_conservatively_unsupported() -> None:
    status = cache_status("acme-local", "mystery-1")

    assert status["supported"] is False
    assert status["label"] == "unsupported"
