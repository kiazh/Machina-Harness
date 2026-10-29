"""ChatGPT auth can recover when Windows denies the default store directory."""

import os

from machina.security import chatgpt_auth


def test_default_auth_path_recovers_from_inaccessible_directory(monkeypatch, tmp_path):
    primary = tmp_path / "auth"
    primary.mkdir()
    monkeypatch.delenv(chatgpt_auth.AUTH_FILE_ENV, raising=False)
    monkeypatch.setattr(
        chatgpt_auth.config, "get", lambda key: str(tmp_path) if key == "machina_home" else None
    )
    original_scandir = os.scandir

    def denied_default(path):
        if os.fspath(path) == os.fspath(primary):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return original_scandir(path)

    with monkeypatch.context() as scoped:
        scoped.setattr(chatgpt_auth.os, "scandir", denied_default)
        assert chatgpt_auth.auth_path() == tmp_path / "auth-local" / "chatgpt.json"
    assert primary.is_dir()


def test_explicit_auth_path_is_not_redirected(monkeypatch, tmp_path):
    explicit = tmp_path / "custom" / "chatgpt.json"
    monkeypatch.setenv(chatgpt_auth.AUTH_FILE_ENV, str(explicit))
    assert chatgpt_auth.auth_path() == explicit
