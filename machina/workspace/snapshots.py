"""Per-turn workspace snapshots with explicit diff and undo.

The snapshot store is intentionally outside the workspace. Before a file tool
changes a path, the previous bytes are copied once into the current turn's
snapshot. At turn end the manifest records the post-write hashes so an undo can
refuse to overwrite edits made after the turn.

This is not a filesystem sandbox and it cannot observe writes performed by an
opaque child process. It protects the normal file-tool path, which is the path
an agent uses to edit source, and fails closed on paths outside the workspace.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import shutil
import subprocess
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from machina.core.config import config

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1
MAX_FILE_BYTES = 8 * 1024 * 1024
_IGNORED_PARTS = frozenset({".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"})
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_ed25519",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
    }
)
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")

_current: ContextVar[TurnSnapshot | None] = ContextVar("machina_turn_snapshot", default=None)


class SnapshotError(RuntimeError):
    """Base snapshot failure."""


class SnapshotConflict(SnapshotError):
    """A file changed after the turn; an undo would overwrite newer work."""

    def __init__(self, paths: list[str]):
        self.paths = paths
        super().__init__("files changed after the snapshot: " + ", ".join(paths))


def _enabled() -> bool:
    raw = os.environ.get("MACHINA_SNAPSHOTS_ENABLED")
    if raw is not None and raw.strip():
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return bool(config.get("snapshots_enabled"))


def workspace_root() -> Path:
    from machina.core.execution_context import configured_workspace

    return configured_workspace().resolve()


def _store_root(workspace: Path) -> Path:
    home = Path(config.get("machina_home") or Path.home() / ".machina").expanduser().resolve()
    digest = hashlib.sha256(str(workspace).casefold().encode("utf-8")).hexdigest()[:16]
    return home / "snapshots" / digest


def store_root(workspace: Path | None = None) -> Path:
    """Workspace-scoped snapshot store (also hosts review-run state)."""
    return _store_root((workspace or workspace_root()).resolve())


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_bytes(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _decode(payload: bytes | None) -> tuple[str, bool]:
    """Return text plus a binary flag; binary payloads never become diffs."""
    if payload is None:
        return "", False
    if b"\0" in payload[:8192]:
        return "", True
    return payload.decode("utf-8", "replace"), False


def _count_lines(text: str) -> int:
    return len(text.splitlines())


def _diff_counts(before: str, after: str) -> tuple[int, int]:
    additions = deletions = 0
    for line in difflib.unified_diff(
        before.splitlines(), after.splitlines(), lineterm=""
    ):
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return additions, deletions


def _stats(files: dict[str, FileSnapshot]) -> dict[str, int]:
    changed = [
        item
        for item in files.values()
        if not item.ignored and item.change in {"added", "modified", "deleted"}
    ]
    return {
        "files": len(changed),
        "additions": sum(item.additions for item in changed),
        "deletions": sum(item.deletions for item in changed),
    }


def _trusted_executable(name: str) -> str | None:
    path = shutil.which(name)
    if not path:
        return None
    resolved = Path(path).resolve()
    return str(resolved) if resolved.is_absolute() else None


def _git(
    workspace: Path, *args: str, timeout: float = 20.0
) -> subprocess.CompletedProcess[bytes] | None:
    git = _trusted_executable("git")
    if git is None:
        return None
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_COMMON_DIR",
            "GIT_NAMESPACE",
        }
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        return subprocess.run(
            [
                git,
                "-C",
                str(workspace),
                "-c",
                "core.fsmonitor=false",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "diff.external=",
                "-c",
                "core.pager=cat",
                *args,
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_root(workspace: Path) -> Path | None:
    result = _git(workspace, "rev-parse", "--show-toplevel", timeout=5)
    if result is None or result.returncode != 0:
        return None
    try:
        return Path(result.stdout.decode("utf-8", "replace").strip()).resolve()
    except OSError:
        return None


def _git_head(workspace: Path) -> str | None:
    result = _git(workspace, "rev-parse", "HEAD", timeout=5)
    if result is None or result.returncode != 0:
        return None
    head = result.stdout.decode("utf-8", "replace").strip()
    return head or None


def _git_blob(workspace: Path, head: str, relative: Path) -> tuple[bool, bytes | None]:
    spec = f"{head}:{relative.as_posix()}"
    exists = _git(workspace, "cat-file", "-e", spec, timeout=5)
    if exists is None or exists.returncode != 0:
        return False, None
    # Read the raw stored bytes. Applying checkout filters here would execute
    # repository-configured smudge/filter programs; ``checkout_attributes_safe``
    # rejects such paths before this helper is used.
    result = _git(workspace, "cat-file", "blob", spec, timeout=20)
    if result is None or result.returncode != 0:
        return True, None
    return True, result.stdout


def _checkout_attributes_safe(workspace: Path, relative: Path) -> bool:
    """True when restoring the raw blob reproduces the worktree bytes.

    ``git check-attr`` only reads attribute files; it never runs filters. A
    set filter, eol rule, text conversion, or working-tree encoding makes the
    raw blob an unsafe undo source, so the caller must disclose the path as
    ignored instead of restoring the wrong bytes.
    """
    result = _git(
        workspace,
        "check-attr",
        "-z",
        "filter",
        "eol",
        "text",
        "working-tree-encoding",
        "--",
        relative.as_posix(),
        timeout=5,
    )
    if result is None or result.returncode != 0:
        return False
    fields = result.stdout.split(b"\0")
    # Output is path\0attribute\0value\0 triples.
    for index in range(2, len(fields), 3):
        value = fields[index].strip().lower()
        if value not in {b"unspecified", b"unset", b""}:
            return False
    return True


def _core_autocrlf_safe(workspace: Path) -> bool:
    result = _git(workspace, "config", "--get", "core.autocrlf", timeout=5)
    if result is None:
        return False
    value = result.stdout.strip().lower()
    return value not in {b"true", b"input"}


def _git_status_clean(workspace: Path) -> bool:
    result = _git(workspace, "status", "--porcelain", "--untracked-files=normal", timeout=20)
    return result is not None and result.returncode == 0 and not result.stdout.strip()


def _safe_workspace(path: Path, workspace: Path) -> Path | None:
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(workspace):
            return None
        relative = resolved.relative_to(workspace)
        if any(part in _IGNORED_PARTS for part in relative.parts):
            return None
        if resolved.name.casefold() in _SENSITIVE_NAMES:
            return None
        if resolved.suffix.casefold() in _SENSITIVE_SUFFIXES:
            return None
        return resolved
    except (OSError, ValueError):
        return None


def _manifest_path(snapshot: TurnSnapshot) -> Path:
    return snapshot.root / "manifest.json"


def _write_manifest(snapshot: TurnSnapshot) -> None:
    snapshot.root.mkdir(parents=True, exist_ok=True)
    payload = snapshot.to_dict()
    temporary = _manifest_path(snapshot).with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(_manifest_path(snapshot))


#: Manifest reads dominate the Changes screen's 3-second refresh once a
#: workspace has hundreds of prompts. Cache by path + size + mtime; every
#: write is an atomic replace, so a changed file always misses.
_MANIFEST_CACHE: dict[str, tuple[int, int, dict[str, Any] | None]] = {}
_MANIFEST_CACHE_LIMIT = 2048


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        _MANIFEST_CACHE.pop(str(path), None)
        return None
    key = str(path)
    cached = _MANIFEST_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != SNAPSHOT_VERSION
        or not isinstance(payload.get("snapshot_id"), str)
        or not isinstance(payload.get("files"), list)
    ):
        payload = None
    if len(_MANIFEST_CACHE) >= _MANIFEST_CACHE_LIMIT:
        _MANIFEST_CACHE.clear()
    _MANIFEST_CACHE[key] = (stat.st_mtime_ns, stat.st_size, payload)
    return payload


@dataclass
class FileSnapshot:
    path: str
    backup: str | None
    existed: bool
    before_sha: str | None
    after_sha: str | None = None
    ignored: bool = False
    #: Stable end-of-turn content. Without it an old prompt's diff would be
    #: computed against the file as it exists today, so later prompts would
    #: leak into earlier rows. ``after_captured`` distinguishes "the turn
    #: deleted this file" from an older manifest that has no after snapshot.
    after_backup: str | None = None
    after_captured: bool = False
    change: str = ""
    additions: int = 0
    deletions: int = 0
    binary: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "backup": self.backup,
            "existed": self.existed,
            "before_sha": self.before_sha,
            "after_sha": self.after_sha,
            "ignored": self.ignored,
            "after_backup": self.after_backup,
            "after_captured": self.after_captured,
            "change": self.change,
            "additions": self.additions,
            "deletions": self.deletions,
            "binary": self.binary,
        }


@dataclass
class TurnSnapshot:
    snapshot_id: str
    session_id: str
    workspace: Path
    root: Path
    created_at: str = field(default_factory=_now)
    files: dict[str, FileSnapshot] = field(default_factory=dict)
    status: str = "open"
    #: The prompt that opened this turn. Stored so the Changes screen can show
    #: changes per prompt instead of per commit.
    prompt: str = ""
    provider: str = ""
    model: str = ""
    #: Clean Git baseline captured lazily before the first terminal command.
    #: Terminal edits are diffed against this commit at turn end. It is never
    #: serialized; finished terminal files are ordinary manifest entries.
    git_head: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SNAPSHOT_VERSION,
            "snapshot_id": self.snapshot_id,
            "session_id": self.session_id,
            "workspace": str(self.workspace),
            "created_at": self.created_at,
            "status": self.status,
            "prompt": self.prompt,
            "provider": self.provider,
            "model": self.model,
            "stats": _stats(self.files),
            "files": [item.to_dict() for item in self.files.values()],
        }

    def _backup_bytes(self, resolved: Path, payload: bytes) -> str | None:
        try:
            backup_dir = self.root / "files"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{len(self.files):04d}-{resolved.name}"
            backup.write_bytes(payload)
            return str(backup)
        except OSError:
            return None

    def _after_backup_bytes(self, resolved: Path, payload: bytes, index: int) -> str | None:
        try:
            backup_dir = self.root / "files"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{index:04d}-after-{resolved.name}"
            backup.write_bytes(payload)
            return str(backup)
        except OSError:
            return None

    def _entry_from_git(self, resolved: Path) -> FileSnapshot | None:
        """Build the pre-turn entry from HEAD for a file-tool write.

        Only called after :meth:`begin_terminal_capture` proved the worktree
        was clean and recorded ``git_head``. That makes HEAD the correct
        before-state even if an earlier terminal command changed the file.
        """
        if self.git_head is None:
            return None
        try:
            relative = resolved.relative_to(self.workspace)
        except ValueError:
            return None
        if not _checkout_attributes_safe(self.workspace, relative):
            return FileSnapshot(
                path=str(resolved),
                backup=None,
                existed=False,
                before_sha=None,
                ignored=True,
            )
        exists, payload = _git_blob(self.workspace, self.git_head, relative)
        item = FileSnapshot(
            path=str(resolved),
            backup=None,
            existed=exists,
            before_sha=None,
        )
        if not exists:
            return item
        if payload is None:
            item.ignored = True
            return item
        if len(payload) > MAX_FILE_BYTES:
            item.ignored = True
            return item
        item.before_sha = _sha256_bytes(payload)
        item.backup = self._backup_bytes(resolved, payload)
        if item.backup is None:
            item.ignored = True
        return item

    def record(self, path: Path) -> None:
        resolved = _safe_workspace(path, self.workspace)
        if resolved is None or str(resolved) in self.files:
            return
        item = self._entry_from_git(resolved)
        if item is None:
            item = FileSnapshot(
                path=str(resolved), backup=None, existed=resolved.exists(), before_sha=None
            )
            try:
                if item.existed:
                    if not resolved.is_file() or resolved.stat().st_size > MAX_FILE_BYTES:
                        item.ignored = True
                    else:
                        item.before_sha = _sha256(resolved)
                        backup_dir = self.root / "files"
                        backup_dir.mkdir(parents=True, exist_ok=True)
                        backup = backup_dir / f"{len(self.files):04d}-{resolved.name}"
                        shutil.copy2(resolved, backup)
                        item.backup = str(backup)
                else:
                    item.before_sha = None
            except OSError:
                item.ignored = True
        self.files[str(resolved)] = item
        _write_manifest(self)

    def begin_terminal_capture(self) -> bool:
        """Enable clean-Git terminal coverage for this turn.

        Fail closed: a dirty worktree, a non-Git workspace, a subdirectory
        workspace, or an earlier file-tool write leaves terminal coverage
        disabled. No whole-tree copy or hash is performed.
        """
        if self.git_head is not None or self.files:
            return False
        root = _git_root(self.workspace)
        if root is None or root != self.workspace:
            return False
        if not _git_status_clean(self.workspace):
            return False
        if not _core_autocrlf_safe(self.workspace):
            return False
        head = _git_head(self.workspace)
        if head is None:
            return False
        self.git_head = head
        return True

    def capture_terminal(self) -> int:
        """Add terminal-changed Git paths that are not already recorded."""
        if self.git_head is None:
            return 0
        changed: list[bytes] = []
        diff = _git(
            self.workspace,
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            self.git_head,
        )
        if diff is not None and diff.returncode == 0:
            changed.extend(item for item in diff.stdout.split(b"\0") if item)
        untracked = _git(
            self.workspace,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        if untracked is not None and untracked.returncode == 0:
            changed.extend(item for item in untracked.stdout.split(b"\0") if item)

        added = 0
        for raw in changed:
            relative = Path(os.fsdecode(raw))
            target = _safe_workspace(self.workspace / relative, self.workspace)
            if target is None or str(target) in self.files or target.is_symlink():
                continue
            if not _checkout_attributes_safe(self.workspace, relative):
                item = FileSnapshot(
                    path=str(target),
                    backup=None,
                    existed=False,
                    before_sha=None,
                    ignored=True,
                )
                item.after_sha = _sha256(target) if target.exists() else None
                self.files[str(target)] = item
                added += 1
                continue
            exists, payload = _git_blob(self.workspace, self.git_head, relative)
            if not exists:
                # New untracked file created during the turn.
                if target.exists() and target.stat().st_size > MAX_FILE_BYTES:
                    item = FileSnapshot(
                        path=str(target),
                        backup=None,
                        existed=False,
                        before_sha=None,
                        ignored=True,
                    )
                else:
                    item = FileSnapshot(
                        path=str(target),
                        backup=None,
                        existed=False,
                        before_sha=None,
                    )
            else:
                if payload is None or len(payload) > MAX_FILE_BYTES:
                    item = FileSnapshot(
                        path=str(target),
                        backup=None,
                        existed=True,
                        before_sha=None,
                        ignored=True,
                    )
                else:
                    backup = self._backup_bytes(target, payload)
                    if backup is None:
                        item = FileSnapshot(
                            path=str(target),
                            backup=None,
                            existed=True,
                            before_sha=None,
                            ignored=True,
                        )
                    else:
                        item = FileSnapshot(
                            path=str(target),
                            backup=backup,
                            existed=True,
                            before_sha=_sha256_bytes(payload),
                        )
            item.after_sha = _sha256(target) if target.exists() else None
            self.files[str(target)] = item
            added += 1
        if added:
            _write_manifest(self)
        return added

    def _finalize_file(self, item: FileSnapshot, index: int) -> None:
        target = Path(item.path)
        after_exists = target.is_file()
        item.after_sha = _sha256(target) if after_exists else None
        if item.ignored:
            item.change = "ignored"
            return

        before_bytes = _read_bytes(_backup_path(item.backup, self.workspace))
        after_bytes = _read_bytes(target) if after_exists else None
        before_text, before_binary = _decode(before_bytes)
        after_text, after_binary = _decode(after_bytes)
        item.binary = before_binary or after_binary

        if item.existed and not after_exists:
            item.change = "deleted"
            item.deletions = 0 if item.binary else _count_lines(before_text)
            item.after_captured = True
            return
        if not item.existed and after_exists:
            item.change = "added"
            item.additions = 0 if item.binary else _count_lines(after_text)
        elif item.existed and after_exists:
            if item.before_sha and item.after_sha and item.before_sha == item.after_sha:
                item.change = "unchanged"
                return
            item.change = "modified"
            if not item.binary:
                item.additions, item.deletions = _diff_counts(before_text, after_text)
        else:
            item.change = "unchanged"
            return

        if not after_exists:
            item.after_captured = True
        elif not item.binary and after_bytes is not None:
            item.after_backup = self._after_backup_bytes(target, after_bytes, index)
            # If the copy failed, fall back to the live file rather than
            # silently presenting an empty diff; the summary still marks the
            # turn's own before/after hashes.
            item.after_captured = item.after_backup is not None

    def finish(self) -> bool:
        """Finalize stable per-prompt statistics and end-state content.

        Returns false when every recorded path ended unchanged, allowing the
        caller to discard the empty snapshot. The after copy is what keeps an
        earlier prompt's diff stable when a later prompt edits the same file.
        """
        for index, item in enumerate(self.files.values()):
            self._finalize_file(item, index)
        # Keep ignored entries: they are the honest disclosure that a path was
        # touched but could not be captured or restored. Drop only no-op
        # writes, which would otherwise clutter every prompt row.
        self.files = {
            path: item
            for path, item in self.files.items()
            if item.change in {"added", "modified", "deleted", "ignored"}
        }
        if not self.files:
            self.status = "complete"
            shutil.rmtree(self.root, ignore_errors=True)
            return False
        self.status = "complete"
        _write_manifest(self)
        return True

    def has_changes(self) -> bool:
        return bool(self.files)


def begin_turn(
    *,
    session_id: str | None = None,
    workspace: Path | None = None,
    prompt: str = "",
    provider: str = "",
    model: str = "",
) -> TurnSnapshot | None:
    """Open a snapshot for the current agent turn and bind it to the context."""
    if not _enabled():
        return None
    root_workspace = (workspace or workspace_root()).resolve()
    sid = str(session_id or "unsessioned")
    snapshot = TurnSnapshot(
        snapshot_id=uuid.uuid4().hex,
        session_id=sid,
        workspace=root_workspace,
        root=_store_root(root_workspace) / sid / uuid.uuid4().hex,
        prompt=str(prompt or "").replace("\x00", "")[:4000],
        provider=str(provider or "")[:200],
        model=str(model or "")[:300],
    )
    _current.set(snapshot)
    return snapshot


def end_turn(snapshot: TurnSnapshot | None) -> None:
    """Finalize the current snapshot; empty turns leave no manifest behind."""
    if snapshot is not None:
        if snapshot.git_head is not None:
            try:
                snapshot.capture_terminal()
            except Exception:
                # Snapshot completion is additive; a terminal diff failure
                # must not turn a successful agent turn into a failure.
                pass
        if snapshot.has_changes():
            snapshot.finish()
        else:
            shutil.rmtree(snapshot.root, ignore_errors=True)
    _current.set(None)


def begin_terminal_capture() -> bool:
    """Capture a clean Git baseline before the first terminal command.

    Returns false when terminal coverage is unavailable. This is an explicit
    conservative boundary, not a claim that every terminal edit is covered.
    """
    snapshot = _current.get()
    if snapshot is None:
        return False
    try:
        return snapshot.begin_terminal_capture()
    except Exception:
        return False


def record_before_write(path: str | Path) -> None:
    """Copy *path* before a file-tool write, if a turn snapshot is active."""
    snapshot = _current.get()
    if snapshot is not None:
        snapshot.record(Path(path))


def current_snapshot() -> TurnSnapshot | None:
    return _current.get()


def _manifests(workspace: Path, session_id: str | None = None) -> list[tuple[dict[str, Any], Path]]:
    root = _store_root(workspace)
    if not root.is_dir():
        return []
    found: list[tuple[dict[str, Any], Path]] = []
    for path in root.glob("*/**/manifest.json"):
        payload = _load_manifest(path)
        if payload is None:
            continue
        if session_id is not None and payload.get("session_id") != session_id:
            continue
        if str(payload.get("workspace") or "") != str(workspace):
            continue
        found.append((payload, path))
    found.sort(key=lambda item: str(item[0].get("created_at") or ""), reverse=True)
    return found


def list_snapshots(
    *, workspace: Path | None = None, session_id: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    root_workspace = (workspace or workspace_root()).resolve()
    return [dict(payload) for payload, _ in _manifests(root_workspace, session_id)[: max(1, limit)]]


def latest_snapshot(
    *, workspace: Path | None = None, session_id: str | None = None
) -> tuple[dict[str, Any], Path] | None:
    root_workspace = (workspace or workspace_root()).resolve()
    for payload, path in _manifests(root_workspace, session_id):
        if payload.get("status") == "complete":
            return payload, path
    return None


def snapshot_by_id(
    snapshot_id: str,
    *,
    workspace: Path | None = None,
    session_id: str | None = None,
) -> tuple[dict[str, Any], Path] | None:
    root_workspace = (workspace or workspace_root()).resolve()
    for payload, path in _manifests(root_workspace, session_id):
        if payload.get("snapshot_id") == snapshot_id:
            return payload, path
    return None


def mark_undone(payload: dict[str, Any], path: Path) -> None:
    payload["status"] = "undone"
    payload["undone_at"] = _now()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _read_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _relative(path: str | Path, workspace: Path) -> str:
    try:
        return Path(path).relative_to(workspace).as_posix()
    except (OSError, ValueError):
        return Path(path).name


def _after_text(raw: dict[str, Any], workspace: Path) -> str:
    """End-of-turn text, stable even when a later prompt edits the same file."""
    if raw.get("after_captured"):
        return _read_text(_backup_path(raw.get("after_backup"), workspace))
    target = _safe_workspace(Path(str(raw.get("path") or "")), workspace)
    return _read_text(target) if target is not None and target.exists() else ""


def _backup_path(raw: Any, workspace: Path) -> Path | None:
    """Only restore backups stored under this workspace's snapshot root."""
    if not raw:
        return None
    try:
        path = Path(str(raw)).expanduser().resolve()
        root = _store_root(workspace)
        if not path.is_relative_to(root) or not path.is_file():
            return None
        return path
    except (OSError, ValueError):
        return None


def _file_diff(raw: dict[str, Any], workspace: Path) -> str:
    target = _safe_workspace(Path(str(raw.get("path") or "")), workspace)
    if target is None:
        return ""
    before = _read_text(_backup_path(raw.get("backup"), workspace))
    after = _after_text(raw, workspace)
    relative = _relative(target, workspace)
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
    )
    return "".join(diff)


def diff_snapshot(
    payload: dict[str, Any],
    *,
    path: str | None = None,
    max_chars: int = 0,
) -> str:
    """Return a unified diff from the snapshot's before state to its end state.

    ``max_chars`` is a safety bound for transport callers; the CLI leaves it
    unbounded. When the bound is reached the diff is cut at a line boundary.
    """
    workspace = Path(str(payload.get("workspace") or ""))
    requested: str | None = None
    if path:
        requested = path.replace("\\", "/")
        while requested.startswith("./"):
            requested = requested[2:]
        requested = requested.lstrip("/")
    chunks: list[str] = []
    size = 0
    for raw in payload.get("files") or []:
        if not isinstance(raw, dict) or raw.get("ignored"):
            continue
        relative = _relative(str(raw.get("path") or ""), workspace)
        if requested is not None and relative != requested:
            continue
        text = _file_diff(raw, workspace)
        if not text:
            continue
        if max_chars:
            remaining = max_chars - size
            if remaining <= 0:
                break
            if len(text) > remaining:
                text = text[:remaining].rsplit("\n", 1)[0] + "\n"
        chunks.append(text)
        size += len(text)
    return "".join(chunks)


MAX_DIFF_BYTES = 512 * 1024
MAX_FILE_DIFF_BYTES = 120 * 1024


def diff_files(
    payload: dict[str, Any],
    *,
    max_chars: int = MAX_DIFF_BYTES,
    path: str | None = None,
) -> dict[str, Any]:
    """Bounded per-file diffs for the Changes inspector.

    ``path`` selects one file before the byte budget is applied, so a large
    earlier file in the same prompt cannot hide a later one.
    """
    workspace = Path(str(payload.get("workspace") or ""))
    files: list[dict[str, Any]] = []
    total = 0
    truncated = False
    for raw in payload.get("files") or []:
        if not isinstance(raw, dict) or raw.get("ignored"):
            continue
        target = _safe_workspace(Path(str(raw.get("path") or "")), workspace)
        if target is None:
            continue
        relative = _relative(target, workspace)
        if path is not None and relative != path:
            continue
        if raw.get("binary"):
            files.append(
                {
                    "path": relative,
                    "change": raw.get("change") or "modified",
                    "text": "",
                    "binary": True,
                    "truncated": False,
                }
            )
            continue
        text = _file_diff(raw, workspace)
        file_truncated = len(text) > MAX_FILE_DIFF_BYTES
        if file_truncated:
            text = text[:MAX_FILE_DIFF_BYTES].rsplit("\n", 1)[0] + "\n"
        remaining = max_chars - total
        if remaining <= 0:
            truncated = True
            break
        if len(text) > remaining:
            text = text[:remaining].rsplit("\n", 1)[0] + "\n"
            truncated = True
            file_truncated = True
        files.append(
            {
                "path": relative,
                "change": raw.get("change") or "modified",
                "text": text,
                "binary": False,
                "truncated": file_truncated,
            }
        )
        total += len(text)
    if truncated:
        logger.debug(
            "diff_files truncated files=%d total=%d max=%d path=%s",
            len(files),
            total,
            max_chars,
            path or "-",
        )
    return {
        "files": files,
        "truncated": truncated,
        "totalChars": total,
        "maxChars": max_chars,
    }


def _stat_int(value: Any, fallback: int = 0) -> int:
    """Manifests are user-editable files; bad counters must not break the list."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(0, parsed)


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    """Bounded manifest view for the Changes screen (never includes diff text)."""
    workspace = Path(str(payload.get("workspace") or ""))
    files: list[dict[str, Any]] = []
    additions = deletions = 0
    for raw in payload.get("files") or []:
        if not isinstance(raw, dict) or raw.get("ignored"):
            continue
        change = str(raw.get("change") or "")
        if not change:
            # Older manifests predate explicit change kinds. Hash equality is
            # the only honest no-op signal available for them.
            if raw.get("before_sha") and raw.get("after_sha") == raw.get("before_sha"):
                continue
            change = "added" if not raw.get("existed") else "modified"
        if change == "unchanged":
            continue
        file_additions = _stat_int(raw.get("additions"))
        file_deletions = _stat_int(raw.get("deletions"))
        additions += file_additions
        deletions += file_deletions
        files.append(
            {
                "path": _relative(str(raw.get("path") or ""), workspace),
                "change": change,
                "additions": file_additions,
                "deletions": file_deletions,
                "binary": bool(raw.get("binary")),
            }
        )
    recorded_stats = payload.get("stats")
    if isinstance(recorded_stats, dict):
        stats = {
            "files": _stat_int(recorded_stats.get("files")) or len(files),
            "additions": _stat_int(recorded_stats.get("additions")) or additions,
            "deletions": _stat_int(recorded_stats.get("deletions")) or deletions,
        }
    else:
        stats = {"files": len(files), "additions": additions, "deletions": deletions}
    return {
        "id": str(payload.get("snapshot_id") or ""),
        "sessionId": str(payload.get("session_id") or ""),
        "prompt": str(payload.get("prompt") or ""),
        "createdAt": str(payload.get("created_at") or ""),
        "provider": str(payload.get("provider") or ""),
        "model": str(payload.get("model") or ""),
        "status": str(payload.get("status") or "complete"),
        "stats": stats,
        "files": files,
    }


def undo_snapshot(
    payload: dict[str, Any],
    *,
    force: bool = False,
) -> list[str]:
    """Restore one snapshot. Refuse when a file changed after the turn."""
    workspace = Path(str(payload.get("workspace") or ""))
    changed_now: list[str] = []
    entries: list[dict[str, Any]] = []
    for raw in payload.get("files") or []:
        if not isinstance(raw, dict) or raw.get("ignored"):
            continue
        target = _safe_workspace(Path(str(raw.get("path") or "")), workspace)
        if target is None:
            continue
        current_sha = _sha256(target) if target.exists() else None
        if not force and current_sha != raw.get("after_sha"):
            changed_now.append(str(target))
        entries.append({**raw, "target": target, "current_sha": current_sha})
    if changed_now:
        raise SnapshotConflict(changed_now)

    restored: list[str] = []
    for entry in entries:
        target: Path = entry["target"]
        if entry.get("existed"):
            backup = _backup_path(entry.get("backup"), workspace)
            if backup is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        elif target.exists():
            target.unlink()
        restored.append(str(target))
    logger.debug("undo_snapshot files=%d skipped=%d", len(restored), len(entries) - len(restored))
    return restored


# Keep the ContextVar token type visible for embedders that want explicit reset.
def reset_current(token: Token) -> None:  # pragma: no cover - thin typing helper
    _current.reset(token)
