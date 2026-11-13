"""Auto-update integration: fetch, fast-forward, and safe dirty-checkout skip."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from machina import update


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _repo_pair(tmp_path: Path, *, bump_version: bool = True) -> Path:
    """A clean checkout one commit behind ``origin/main``."""
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init")
    _git(remote, "checkout", "-b", "main")
    _git(remote, "config", "user.email", "test@example.com")
    _git(remote, "config", "user.name", "Test")
    (remote / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    (remote / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(remote, "add", ".")
    _git(remote, "commit", "-m", "initial")

    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(remote, "remote", "add", "origin", str(origin))
    _git(remote, "push", "-u", "origin", "main")

    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", str(origin), str(checkout))
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test")

    if bump_version:
        (remote / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\nversion = "99.0.0"\n', encoding="utf-8"
        )
    (remote / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(remote, "add", ".")
    _git(remote, "commit", "-m", "remote update")
    _git(remote, "push")
    return checkout


def _enable_tty(monkeypatch) -> None:
    class Tty:
        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MACHINA_AUTO_UPDATE", raising=False)
    monkeypatch.setattr(update.sys, "stdout", Tty())


def _capture_exec(monkeypatch) -> list[tuple[str, list[str]]]:
    exec_calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        update.os, "execv", lambda executable, args: exec_calls.append((executable, args))
    )
    return exec_calls


def _skip_pip(monkeypatch) -> None:
    real_run = update._run

    def run(args, *, cwd, timeout):
        if args[0] == sys.executable:
            # Do not run pip during this test; the re-exec itself is the assertion.
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(update, "_run", run)


def test_update_fast_forwards_and_reexecs_the_new_entrypoint(monkeypatch, tmp_path: Path) -> None:
    checkout = _repo_pair(tmp_path)
    _enable_tty(monkeypatch)
    monkeypatch.setattr(update, "_repo_root", lambda: checkout)
    monkeypatch.setattr(update, "_check_is_fresh", lambda _root: False)
    monkeypatch.setattr(update.sys, "argv", ["machina", "--mode", "full"])
    exec_calls = _capture_exec(monkeypatch)
    _skip_pip(monkeypatch)

    update.auto_update()

    assert (checkout / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert 'version = "99.0.0"' in (checkout / "pyproject.toml").read_text(encoding="utf-8")
    assert len(exec_calls) == 1
    executable, args = exec_calls[0]
    assert executable == sys.executable
    assert args[:3] == [sys.executable, "-m", "machina"]
    assert args[3:] == ["--mode", "full"]


def test_update_fast_forwards_commits_without_a_version_bump(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    checkout = _repo_pair(tmp_path, bump_version=False)
    _enable_tty(monkeypatch)
    monkeypatch.setattr(update, "_repo_root", lambda: checkout)
    monkeypatch.setattr(update, "_check_is_fresh", lambda _root: False)
    exec_calls = _capture_exec(monkeypatch)
    _skip_pip(monkeypatch)

    update.auto_update()

    assert (checkout / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert 'version = "1.0.0"' in (checkout / "pyproject.toml").read_text(encoding="utf-8")
    assert len(exec_calls) == 1
    assert "1 new commit" in capsys.readouterr().err


def test_update_never_pulls_a_dirty_checkout(monkeypatch, tmp_path: Path, capsys) -> None:
    checkout = _repo_pair(tmp_path)
    (checkout / "local-work.txt").write_text("uncommitted\n", encoding="utf-8")
    _enable_tty(monkeypatch)
    monkeypatch.setattr(update, "_repo_root", lambda: checkout)
    monkeypatch.setattr(update, "_check_is_fresh", lambda _root: False)
    exec_calls = _capture_exec(monkeypatch)

    update.auto_update()

    assert not exec_calls
    assert "local changes" in capsys.readouterr().err
    assert '"1.0.0"' in (checkout / "pyproject.toml").read_text(encoding="utf-8")
    assert (checkout / "app.py").read_text(encoding="utf-8") == "value = 1\n"


def test_update_never_fast_forwards_over_local_commits(monkeypatch, tmp_path: Path) -> None:
    checkout = _repo_pair(tmp_path)
    (checkout / "local-work.txt").write_text("local\n", encoding="utf-8")
    _git(checkout, "add", "local-work.txt")
    _git(checkout, "commit", "-m", "local work")
    _enable_tty(monkeypatch)
    monkeypatch.setattr(update, "_repo_root", lambda: checkout)
    monkeypatch.setattr(update, "_check_is_fresh", lambda _root: False)
    exec_calls = _capture_exec(monkeypatch)

    update.auto_update()

    assert not exec_calls
    assert (checkout / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert '"1.0.0"' in (checkout / "pyproject.toml").read_text(encoding="utf-8")


def test_update_cache_resolves_a_worktree_gitdir(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    git_dir = tmp_path / "repo" / ".git" / "worktrees" / "worktree"
    git_dir.mkdir(parents=True)
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {git_dir.as_posix()}\n", encoding="utf-8")

    update._mark_checked(worktree)

    assert (git_dir / update._CHECK_MARKER).is_file()
    assert update._check_is_fresh(worktree)
