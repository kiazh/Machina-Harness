"""Branch discovery must not spawn a process during gateway startup."""

from pathlib import Path

from machina.gateway.server import _git_branch


def test_git_branch_reads_head_without_spawning_git(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(
        "ref: refs/heads/feature/instant-start\n", encoding="utf-8"
    )
    monkeypatch.chdir(root)

    assert _git_branch() == "feature/instant-start"


def test_git_branch_follows_a_worktree_gitdir_file(monkeypatch, tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    git_dir = tmp_path / "repo" / ".git" / "worktrees" / "worktree"
    git_dir.mkdir(parents=True)
    worktree.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/wt-branch\n", encoding="utf-8")
    (worktree / ".git").write_text(f"gitdir: {git_dir.as_posix()}\n", encoding="utf-8")
    monkeypatch.chdir(worktree)

    assert _git_branch() == "wt-branch"


def test_git_branch_reports_detached_head(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("a" * 40 + "\n", encoding="utf-8")
    monkeypatch.chdir(root)

    assert _git_branch() == "HEAD"
