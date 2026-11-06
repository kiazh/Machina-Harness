"""Real repository acceptance for read-only, workspace-bound Git review."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from machina.workspace.models import WorkspaceError
from machina.workspace.service import WorkspaceService

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="Git is required")


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), *args],
        check=True,
        capture_output=True,
        timeout=10,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "review-test")
    git(tmp_path, "config", "user.email", "review@example.invalid")
    git(tmp_path, "config", "user.name", "Workspace Review Test")
    git(tmp_path, "config", "core.autocrlf", "false")
    return tmp_path


def commit(root: Path) -> None:
    git(root, "add", "--all")
    git(root, "commit", "-m", "fixture")


def gateway(root: Path) -> tuple[SimpleNamespace, dict]:
    service = WorkspaceService()
    identity = service.open(str(root), trust=False)
    return SimpleNamespace(workspace_service=service), {
        "workspaceId": identity.workspace_id,
        "epoch": identity.epoch,
        "clientId": identity.client_id,
    }


async def changes(root: Path) -> dict:
    from machina.gateway.features.workspace_git import workspace_changes

    gw, params = gateway(root)
    return await workspace_changes(gw, params)


async def diff(root: Path, path: str, target: str = "working") -> dict:
    from machina.gateway.features.workspace_git import workspace_diff

    gw, params = gateway(root)
    return await workspace_diff(gw, {**params, "path": path, "target": target})


@pytest.mark.asyncio
async def test_real_staged_unstaged_untracked_deleted_and_rename(repo: Path):
    for name in ("mixed.txt", "deleted.txt", "before.txt", "clean.txt"):
        (repo / name).write_text(f"original {name}\n", encoding="utf-8")
    commit(repo)
    (repo / "mixed.txt").write_text("index content\n", encoding="utf-8")
    git(repo, "add", "--", "mixed.txt")
    (repo / "mixed.txt").write_text("disk content\n", encoding="utf-8")
    (repo / "deleted.txt").unlink()
    git(repo, "mv", "before.txt", "after.txt")
    (repo / "new.txt").write_text("new content\n", encoding="utf-8")
    index_before = (repo / ".git/index").read_bytes()

    result = await changes(repo)

    assert result["state"] == "ready"
    assert result["branch"] == "review-test"
    assert result["workspaceId"] and result["workspaceEpoch"] == 1
    assert result["truncated"] is False
    entries = {entry["path"]: entry for entry in result["entries"]}
    assert set(entries) == {"mixed.txt", "deleted.txt", "after.txt", "new.txt"}
    assert entries["mixed.txt"]["staged"] and entries["mixed.txt"]["unstaged"]
    assert entries["deleted.txt"]["exists"] is False
    assert entries["new.txt"]["untracked"] is True
    assert entries["after.txt"]["oldPath"] == "before.txt"
    assert (repo / ".git/index").read_bytes() == index_before
    assert "+disk content" in (await diff(repo, "mixed.txt"))["patch"]
    assert "-index content" in (await diff(repo, "mixed.txt"))["patch"]
    assert "+index content" in (await diff(repo, "mixed.txt", "staged"))["patch"]
    renamed = await diff(repo, "after.txt", "staged")
    assert renamed["oldPath"] == "before.txt"
    assert "rename from before.txt" in renamed["patch"]
    assert "-original deleted.txt" in (await diff(repo, "deleted.txt"))["patch"]
    assert "+new content" in (await diff(repo, "new.txt"))["patch"]


@pytest.mark.asyncio
async def test_unborn_head_and_clean_repository(repo: Path):
    (repo / "first.txt").write_text("first\n", encoding="utf-8")
    git(repo, "add", "--", "first.txt")
    result = await changes(repo)
    assert result["branch"] == "review-test"
    assert result["entries"][0]["staged"] is True
    assert "+first" in (await diff(repo, "first.txt", "staged"))["patch"]
    git(repo, "commit", "-m", "initial")
    assert (await changes(repo))["entries"] == []


@pytest.mark.asyncio
async def test_literal_dash_and_pathspec_filenames(repo: Path):
    names = ["-leading.txt", "[literal].txt", ":(glob)literal.txt", " space .txt"]
    if os.name == "nt":
        names.remove(":(glob)literal.txt")
    else:
        names.append("line\nbreak.txt")
    for name in names:
        (repo / name).write_text(f"literal {name}\n", encoding="utf-8")
    result = await changes(repo)
    assert {entry["path"] for entry in result["entries"]} == set(names)
    for name in names:
        result = await diff(repo, name)
        assert result["path"] == name
        assert "+literal" in result["patch"]


@pytest.mark.asyncio
async def test_subdirectory_never_exposes_neighbor_paths_or_rename_source(repo: Path):
    selected = repo / "selected"
    selected.mkdir()
    (selected / "inside.txt").write_text("inside\n", encoding="utf-8")
    (repo / "neighbor-secret.txt").write_text("NEIGHBOR SECRET\n", encoding="utf-8")
    commit(repo)
    (selected / "inside.txt").write_text("changed\n", encoding="utf-8")
    (repo / "neighbor-secret.txt").write_text("CHANGED SECRET\n", encoding="utf-8")
    result = await changes(selected)
    assert [entry["path"] for entry in result["entries"]] == ["inside.txt"]
    assert "SECRET" not in str(result)
    assert "SECRET" not in (await diff(selected, "inside.txt"))["patch"]
    refused = await diff(selected, "../neighbor-secret.txt")
    assert refused["refused"]["reason"] == "denied"
    git(repo, "mv", "neighbor-secret.txt", "selected/moved.txt")
    moved = await changes(selected)
    assert all("oldPath" not in entry for entry in moved["entries"])
    # The moved index content is now inside the workspace. Its old neighboring
    # version and source name must not be exposed as a rename/delete side.
    moved_diff = await diff(selected, "moved.txt", "staged")
    assert "oldPath" not in moved_diff
    assert "-NEIGHBOR SECRET" not in moved_diff["patch"]
    assert "neighbor-secret.txt" not in moved_diff["patch"]


@pytest.mark.asyncio
async def test_external_symlink_is_refused_before_index_blob_read(repo: Path, tmp_path: Path):
    outside = repo.parent / f"{repo.name}-outside.txt"
    outside.write_text("OUTSIDE SECRET\n", encoding="utf-8")
    link = repo / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires an available OS privilege")
    git(repo, "add", "--", "escape.txt")
    result = await changes(repo)
    assert result["entries"] == []
    result = await diff(repo, "escape.txt", "staged")
    assert result["refused"]["reason"] == "denied"
    assert "OUTSIDE SECRET" not in str(result)


@pytest.mark.asyncio
async def test_malicious_filters_fsmonitor_diff_and_hooks_never_execute(repo: Path):
    marker = repo / "executed-marker"
    callback = repo / "callback.py"
    callback.write_text(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    (repo / "tracked.txt").write_text("original\n", encoding="utf-8")
    commit(repo)
    (repo / ".gitattributes").write_text("tracked.txt filter=evil diff=evil\n", encoding="utf-8")
    command = f'"{sys.executable}" "{callback}"'
    for key in (
        "filter.evil.clean",
        "filter.evil.process",
        "diff.evil.command",
        "diff.evil.textconv",
    ):
        git(repo, "config", key, command)
    git(repo, "config", "filter.evil.required", "true")
    git(repo, "config", "core.fsmonitor", command)
    git(repo, "config", "core.hooksPath", str(repo))
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

    result = await changes(repo)
    assert result["state"] == "ready"
    assert "tracked.txt" in {entry["path"] for entry in result["entries"]}
    result = await diff(repo, "tracked.txt")
    assert result["refused"]["reason"] == "denied"
    assert "filter" in result["refused"]["message"]
    assert not marker.exists()


@pytest.mark.asyncio
async def test_crlf_checkout_compares_using_git_line_ending_semantics(repo: Path):
    (repo / ".gitattributes").write_bytes(b"*.txt text eol=crlf\n")
    (repo / "windows.txt").write_bytes(b"first\r\nsecond\r\n")
    commit(repo)
    assert (await changes(repo))["entries"] == []
    (repo / "windows.txt").write_bytes(b"first\r\nchanged\r\n")
    result = await diff(repo, "windows.txt")
    assert "-second\n" in result["patch"]
    assert "+changed\n" in result["patch"]
    assert "\r" not in result["patch"]


@pytest.mark.asyncio
async def test_binary_and_large_diffs_are_truthful_and_bounded(repo: Path, monkeypatch):
    from machina.workspace import git_review

    (repo / "binary.bin").write_bytes(b"before\x00bytes")
    (repo / "large.txt").write_text("old\n", encoding="utf-8")
    commit(repo)
    (repo / "binary.bin").write_bytes(b"after\x00bytes")
    binary = await diff(repo, "binary.bin")
    assert binary["binary"] is True
    assert "Binary files" in binary["patch"]
    monkeypatch.setattr(git_review, "MAX_FILE_BYTES", 128)
    (repo / "large.txt").write_text("large\n" * 500, encoding="utf-8")
    large = await diff(repo, "large.txt")
    assert large["truncated"] is True
    assert len(large["patch"].encode()) <= git_review.MAX_PATCH_BYTES


@pytest.mark.asyncio
async def test_missing_git_and_non_repository_are_distinct(tmp_path: Path, monkeypatch):
    # pytest's approved basetemp is inside Machina's real repository. A bare
    # fixture provides a genuine directory without a Git working tree.
    git(tmp_path, "init", "--bare")
    assert (await changes(tmp_path))["state"] == "not_repository"
    from machina.workspace import git_review

    monkeypatch.setattr(git_review.shutil, "which", lambda _: None)
    result = await changes(tmp_path)
    assert result["state"] == "unavailable"
    assert "Git" in result["reason"]


@pytest.mark.asyncio
async def test_linked_worktree_reads_its_own_index(repo: Path):
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    commit(repo)
    linked = repo.parent / f"{repo.name}-linked"
    git(repo, "worktree", "add", "-b", "linked-review", str(linked))
    (linked / "tracked.txt").write_text("linked disk\n", encoding="utf-8")
    result = await changes(linked)
    assert result["branch"] == "linked-review"
    assert "+linked disk" in (await diff(linked, "tracked.txt"))["patch"]


@pytest.mark.asyncio
async def test_wrong_client_epoch_and_reopened_during_scan_are_refused(repo: Path, monkeypatch):
    from machina.gateway.features.workspace_git import workspace_changes
    from machina.workspace import git_review

    gw, params = gateway(repo)
    assert "refused" in await workspace_changes(gw, {**params, "epoch": 999})
    assert "refused" in await workspace_changes(gw, {**params, "clientId": "other"})
    entered = asyncio.Event()
    release = asyncio.Event()
    original = git_review.GitReview.changes

    async def held(self):
        entered.set()
        await release.wait()
        return await original(self)

    monkeypatch.setattr(git_review.GitReview, "changes", held)
    task = asyncio.create_task(workspace_changes(gw, params))
    await entered.wait()
    gw.workspace_service.open(str(repo), trust=False)
    release.set()
    assert (await task)["refused"]["reason"] == "denied"


@pytest.mark.asyncio
async def test_status_entry_and_output_caps_are_disclosed(repo: Path, monkeypatch):
    from machina.workspace import git_review

    for index in range(10):
        (repo / f"file-{index}-{'x' * 40}.txt").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr(git_review, "MAX_ENTRIES", 3)
    result = await changes(repo)
    assert len(result["entries"]) == 3
    assert result["truncated"] is True
    monkeypatch.setattr(git_review, "MAX_ENTRIES", 2000)
    # Keep discovery's absolute repo path inside the cap, while cutting the
    # untracked listing regardless of pytest's basetemp path length.
    monkeypatch.setattr(git_review, "MAX_GIT_OUTPUT", len(repo.as_posix().encode()) + 32)
    result = await changes(repo)
    assert result["state"] == "ready"
    assert result["truncated"] is True
    assert len(result["entries"]) < 10
    assert len(str(result)) < 2000


@pytest.mark.asyncio
async def test_bounded_process_is_reaped_on_timeout_and_cancel(tmp_path: Path, monkeypatch):
    from machina.workspace.git_review import GitFailure, run_process

    original = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []
    started = asyncio.Event()

    async def observe_process(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe_process)
    script = "import time; time.sleep(60)"
    with pytest.raises(GitFailure, match="timed out"):
        await run_process([sys.executable, "-c", script], cwd=tmp_path, timeout=0.05)
    assert processes[0].returncode is not None
    started.clear()
    task = asyncio.create_task(
        run_process([sys.executable, "-c", script], cwd=tmp_path, timeout=30)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(processes) == 2
    assert processes[1].returncode is not None


@pytest.mark.asyncio
async def test_process_output_limit_kills_and_drains_large_pipes(tmp_path: Path):
    from machina.workspace.git_review import run_process

    result = await asyncio.wait_for(
        run_process(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 10000000)"],
            cwd=tmp_path,
            timeout=2,
            max_bytes=128,
        ),
        timeout=4,
    )
    assert result.truncated is True
    assert len(result.stdout) == 128
    assert result.returncode != 0


@pytest.mark.asyncio
async def test_repeated_cancel_waits_for_process_teardown_barrier(tmp_path: Path, monkeypatch):
    from machina.workspace.git_review import run_process

    spawned = asyncio.Event()
    teardown_entered = asyncio.Event()
    wait_started = asyncio.Event()
    release_teardown = asyncio.Event()
    original_spawn = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []

    async def observed_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        processes.append(process)
        original_wait = process.wait
        waits = 0

        async def observed_wait():
            nonlocal waits
            waits += 1
            if waits == 1:
                wait_started.set()
            else:
                teardown_entered.set()
                await release_teardown.wait()
            return await original_wait()

        process.wait = observed_wait
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observed_spawn)
    task = asyncio.create_task(
        run_process([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path)
    )
    try:
        await spawned.wait()
        await wait_started.wait()
        task.cancel()
        await asyncio.wait_for(teardown_entered.wait(), timeout=2)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=0.03)
        assert not done, "Cancellation must wait until owned teardown finishes"
    finally:
        release_teardown.set()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if processes:
            await processes[0].wait()
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_process_deadline_includes_semaphore_wait(tmp_path: Path):
    from machina.workspace import git_review

    loop = asyncio.get_running_loop()
    slots = asyncio.Semaphore(0)
    git_review._PROCESS_SLOTS[loop] = slots
    with pytest.raises(git_review.GitFailure, match="timed out"):
        await asyncio.wait_for(
            git_review.run_process(
                [sys.executable, "-c", "raise AssertionError('must not spawn')"],
                cwd=tmp_path,
                timeout=0.03,
            ),
            timeout=0.3,
        )
    assert slots.locked()


@pytest.mark.asyncio
async def test_cancellation_while_queued_does_not_acquire_or_spawn(tmp_path: Path, monkeypatch):
    from machina.workspace import git_review

    loop = asyncio.get_running_loop()
    slots = asyncio.Semaphore(0)
    git_review._PROCESS_SLOTS[loop] = slots
    started = False

    async def unexpected_spawn(*args, **kwargs):
        nonlocal started
        started = True
        raise AssertionError("Queued cancellation must not spawn a process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_spawn)
    task = asyncio.create_task(git_review.run_process([sys.executable, "-c", "pass"], cwd=tmp_path))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not started
    assert slots.locked()


@pytest.mark.asyncio
async def test_spawn_timeout_joins_and_reaps_child_before_releasing_slot(
    tmp_path: Path, monkeypatch
):
    from machina.workspace import git_review

    slots = asyncio.Semaphore(1)
    git_review._PROCESS_SLOTS[asyncio.get_running_loop()] = slots
    original_spawn = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    release_spawn = asyncio.Event()
    processes: list[asyncio.subprocess.Process] = []
    runtime_wait_started = False

    async def held_spawn(*args, **kwargs):
        nonlocal runtime_wait_started
        process = await original_spawn(*args, **kwargs)
        processes.append(process)
        original_wait = process.wait
        original_kill = process.kill
        killed = False

        def observed_kill():
            nonlocal killed
            killed = True
            return original_kill()

        async def observed_wait():
            nonlocal runtime_wait_started
            if not killed:
                runtime_wait_started = True
            return await original_wait()

        process.wait = observed_wait
        process.kill = observed_kill
        spawned.set()
        await release_spawn.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", held_spawn)
    task = asyncio.create_task(
        git_review.run_process(
            [sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, timeout=0.03
        )
    )
    try:
        await spawned.wait()
        await asyncio.sleep(0.06)
        assert not task.done()
        assert slots.locked(), "An in-progress spawn must retain its process slot"
        release_spawn.set()
        with pytest.raises(git_review.GitFailure, match="timed out"):
            await asyncio.wait_for(task, timeout=2)
        assert processes[0].returncode is not None
        assert not slots.locked()
        assert not runtime_wait_started, "Spawn time must consume the process deadline"
    finally:
        release_spawn.set()
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, git_review.GitFailure, TimeoutError):
            await task
        for process in processes:
            if process.returncode is None:
                process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_recreated_staged_deletion_retains_both_versions(repo: Path):
    (repo / "recreated.txt").write_bytes(b"committed\n")
    commit(repo)
    git(repo, "rm", "--", "recreated.txt")
    (repo / "recreated.txt").write_bytes(b"new disk version\n")
    entry = (await changes(repo))["entries"][0]
    assert entry["staged"] is True
    assert entry["untracked"] is True
    assert entry["exists"] is True
    assert "-committed" in (await diff(repo, "recreated.txt", "staged"))["patch"]
    assert "+new disk version" in (await diff(repo, "recreated.txt"))["patch"]


@pytest.mark.asyncio
async def test_unchanged_index_stat_skips_working_content_reads(repo: Path, monkeypatch):
    from machina.workspace.git_review import GitReview

    (repo / "clean.txt").write_bytes(b"unchanged\n")
    commit(repo)

    def unexpected_read(*_):
        raise AssertionError("The unchanged index stat should avoid a disk content read")

    monkeypatch.setattr(GitReview, "_read_disk", unexpected_read)
    assert (await changes(repo))["entries"] == []


@pytest.mark.asyncio
async def test_selected_repository_is_safe_even_with_different_git_owner(repo: Path, monkeypatch):
    from machina.workspace.git_review import GitReview

    selected = repo / "selected"
    selected.mkdir()
    (selected / "owned.txt").write_bytes(b"selected workspace\n")
    original = GitReview.__init__

    def different_owner(self, files):
        original(self, files)
        self.environment["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"

    monkeypatch.setattr(GitReview, "__init__", different_owner)
    result = await changes(selected)
    assert result["state"] == "ready"
    assert [entry["path"] for entry in result["entries"]] == ["owned.txt"]


@pytest.mark.asyncio
async def test_staged_file_mode_change_has_a_readable_diff(repo: Path):
    (repo / "script.sh").write_bytes(b"echo hello\n")
    commit(repo)
    git(repo, "update-index", "--chmod=+x", "--", "script.sh")
    result = await diff(repo, "script.sh", "staged")
    assert "old mode 100644" in result["patch"]
    assert "new mode 100755" in result["patch"]


@pytest.mark.asyncio
async def test_changed_file_inspection_batches_git_processes(repo: Path, monkeypatch):
    from machina.workspace import git_review

    for index in range(8):
        (repo / f"tracked-{index}.txt").write_bytes(b"original\n")
    commit(repo)
    for index in range(8):
        (repo / f"tracked-{index}.txt").write_bytes(b"modified\n")
    original = git_review.run_process
    commands: list[list[str]] = []

    async def observed(command, **kwargs):
        commands.append(command)
        return await original(command, **kwargs)

    monkeypatch.setattr(git_review, "run_process", observed)
    result = await changes(repo)
    assert len(result["entries"]) == 8
    assert all(entry["unstaged"] for entry in result["entries"])
    assert sum("check-attr" in command for command in commands) == 1
    assert sum("cat-file" in command for command in commands) == 1
    assert len(commands) <= 8


@pytest.mark.asyncio
async def test_diff_unknown_missing_and_invalid_paths_are_refusals(repo: Path):
    assert (await diff(repo, "missing.txt"))["refused"]["reason"] == "missing"
    for path in ("../outside.txt", "/absolute", "C:/outside", "bad\x00name"):
        assert (await diff(repo, path))["refused"]["reason"] == "denied"


def test_workspace_file_policy_is_unchanged(repo: Path):
    from machina.workspace.files import WorkspaceFiles

    with pytest.raises(WorkspaceError):
        WorkspaceFiles(repo).resolve("../outside")
