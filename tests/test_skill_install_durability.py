"""Real filesystem durability checks, including Windows CRT file modes."""

from machina.skills import repo_install
from machina.skills.repo_install import _fsync_file


def test_staged_file_can_be_synced_without_changing_its_bytes(tmp_path):
    path = tmp_path / "reviewed-skill.json"
    content = b'{"reviewed": true}\n'
    path.write_bytes(content)
    _fsync_file(path)
    assert path.read_bytes() == content


def test_reviews_from_one_revision_have_independent_reusable_staging(tmp_path, monkeypatch):
    root = tmp_path / "staging"
    monkeypatch.setattr(repo_install, "staging_root", lambda: root)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "SKILL.md").write_text("reviewed bytes", encoding="utf-8")
    source = repo_install.parse_repo_url("https://github.com/owner/repo")
    first = repo_install.RepoPlan(source=source, revision="a" * 40, digest="b" * 64)
    second = repo_install.RepoPlan(source=source, revision="a" * 40, digest="c" * 64)
    first_path = repo_install.write_staged_plan(first, repo)
    second_path = repo_install.write_staged_plan(second, repo)
    assert first_path != second_path
    assert repo_install.load_staged_plan(first.digest)[1] == first_path
    assert repo_install.load_staged_plan(second.digest)[1] == second_path
    assert repo_install.write_staged_plan(first, repo) == first_path
    assert (first_path / "SKILL.md").read_text(encoding="utf-8") == "reviewed bytes"


def test_owned_cleanup_removes_readonly_git_objects_and_refuses_outside_paths(tmp_path):
    import stat

    import pytest

    owned = tmp_path / "owned"
    owned.mkdir()
    cache = owned / "staged"
    cache.mkdir()
    blob = cache / "readonly-object"
    blob.write_bytes(b"reviewed")
    blob.chmod(stat.S_IREAD)
    repo_install._remove_owned_tree(cache, owned)
    assert not cache.exists()
    with pytest.raises(repo_install.RepoInstallError):
        repo_install._remove_owned_tree(tmp_path, owned)
    assert owned.is_dir()


def test_repeated_review_repairs_changed_staging_bytes(tmp_path, monkeypatch):
    import hashlib
    import subprocess

    root = tmp_path / "staging"
    monkeypatch.setattr(repo_install, "staging_root", lambda: root)
    repo = tmp_path / "repo"
    repo.mkdir()
    content = "---\nname: example\ndescription: Example skill\n---\nReviewed instructions.\n"
    (repo / "SKILL.md").write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "SKILL.md"], check=True)
    source = repo_install.parse_repo_url("https://github.com/owner/repo")
    skill = repo_install.SkillPlan(
        name="example",
        description="Example skill",
        root=".",
        files={"SKILL.md": hashlib.sha256((repo / "SKILL.md").read_bytes()).hexdigest()},
    )
    plan = repo_install.RepoPlan(source=source, revision="a" * 40, skills=[skill])
    plan.digest = hashlib.sha256(
        repo_install._canonical_plan_bytes(
            source.url,
            plan.revision,
            [{"name": skill.name, "root": skill.root, "files": skill.files}],
        )
    ).hexdigest()
    staged = repo_install.write_staged_plan(plan, repo)
    (staged / "SKILL.md").write_text("changed after review", encoding="utf-8")
    repaired = repo_install.write_staged_plan(plan, repo)
    assert (repaired / "SKILL.md").read_text(encoding="utf-8") == content
    assert repo_install.load_staged_plan(plan.digest)[1] == repaired
