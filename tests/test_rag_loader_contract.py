"""FileLoader OS error contracts shared by the Windows and POSIX paths."""

from __future__ import annotations

import pytest

from machina.core.exceptions import ValidationError
from machina.rag import loaders
from machina.rag.loaders import FileLoader


def _raise(exc: BaseException):
    def raiser(*args, **kwargs):
        raise exc

    return raiser


def test_directory_is_reported_as_not_a_file(tmp_path) -> None:
    loader = FileLoader(base_dir=tmp_path)
    with pytest.raises(ValidationError, match="Not a file"):
        loader.load(tmp_path)


def test_fallback_permission_error_for_a_directory_is_not_a_file(
    monkeypatch, tmp_path
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    loader = FileLoader(base_dir=tmp_path)
    monkeypatch.setattr(loaders, "_dir_fd_supported", lambda: False)
    monkeypatch.setattr(loaders, "_os_open", _raise(PermissionError("denied")))

    with pytest.raises(ValidationError, match="Not a file"):
        loader.load(directory)


def test_fallback_permission_error_for_a_file_is_not_accessible(
    monkeypatch, tmp_path
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    loader = FileLoader(base_dir=tmp_path)
    monkeypatch.setattr(loaders, "_dir_fd_supported", lambda: False)
    monkeypatch.setattr(loaders, "_os_open", _raise(PermissionError("denied")))

    with pytest.raises(ValidationError, match="Path is not accessible"):
        loader.load(target)


def test_fallback_runtime_error_is_a_validation_error(monkeypatch, tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    loader = FileLoader(base_dir=tmp_path)
    monkeypatch.setattr(loaders, "_dir_fd_supported", lambda: False)
    monkeypatch.setattr(loaders, "_os_open", _raise(RuntimeError("symlink loop")))

    with pytest.raises(ValidationError, match="Path is not accessible"):
        loader.load(target)
