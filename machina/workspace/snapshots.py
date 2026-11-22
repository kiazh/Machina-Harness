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
import os
import shutil
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from machina.core.config import config

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
    home = Path(config.get("machina_home") or Path.home() / ".machina").expanduser()
    digest = hashlib.sha256(str(workspace).casefold().encode("utf-8")).hexdigest()[:16]
    return home / "snapshots" / digest


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


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_VERSION:
        return None
    if not isinstance(payload.get("snapshot_id"), str):
        return None
    if not isinstance(payload.get("files"), list):
        return None
    return payload


@dataclass
class FileSnapshot:
    path: str
    backup: str | None
    existed: bool
    before_sha: str | None
    after_sha: str | None = None
    ignored: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "backup": self.backup,
            "existed": self.existed,
            "before_sha": self.before_sha,
            "after_sha": self.after_sha,
            "ignored": self.ignored,
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SNAPSHOT_VERSION,
            "snapshot_id": self.snapshot_id,
            "session_id": self.session_id,
            "workspace": str(self.workspace),
            "created_at": self.created_at,
            "status": self.status,
            "files": [item.to_dict() for item in self.files.values()],
        }

    def record(self, path: Path) -> None:
        resolved = _safe_workspace(path, self.workspace)
        if resolved is None or str(resolved) in self.files:
            return
        item = FileSnapshot(path=str(resolved), backup=None, existed=resolved.exists(), before_sha=None)
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

    def finish(self) -> None:
        for item in self.files.values():
            item.after_sha = _sha256(Path(item.path)) if Path(item.path).exists() else None
        self.status = "complete"
        _write_manifest(self)

    def has_changes(self) -> bool:
        return bool(self.files)


def begin_turn(*, session_id: str | None = None, workspace: Path | None = None) -> TurnSnapshot | None:
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
    )
    _current.set(snapshot)
    return snapshot


def end_turn(snapshot: TurnSnapshot | None) -> None:
    """Finalize the current snapshot; empty turns leave no manifest behind."""
    if snapshot is not None:
        if snapshot.has_changes():
            snapshot.finish()
        else:
            shutil.rmtree(snapshot.root, ignore_errors=True)
    _current.set(None)


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


def list_snapshots(*, workspace: Path | None = None, session_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    root_workspace = (workspace or workspace_root()).resolve()
    return [dict(payload) for payload, _ in _manifests(root_workspace, session_id)[: max(1, limit)]]


def latest_snapshot(*, workspace: Path | None = None, session_id: str | None = None) -> tuple[dict[str, Any], Path] | None:
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


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def diff_snapshot(payload: dict[str, Any]) -> str:
    """Return a unified diff from the snapshot's before state to current files."""
    workspace = Path(str(payload.get("workspace") or ""))
    chunks: list[str] = []
    for raw in payload.get("files") or []:
        if not isinstance(raw, dict) or raw.get("ignored"):
            continue
        target = _safe_workspace(Path(str(raw.get("path") or "")), workspace)
        if target is None:
            continue
        before = _read_text(Path(str(raw["backup"]))) if raw.get("backup") else ""
        after = _read_text(target) if target.exists() else ""
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{target.relative_to(workspace)}",
            tofile=f"b/{target.relative_to(workspace)}",
        )
        text = "".join(diff)
        if text:
            chunks.append(text)
    return "".join(chunks)


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
            backup = Path(str(entry.get("backup") or ""))
            if not backup.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        elif target.exists():
            target.unlink()
        restored.append(str(target))
    return restored


# Keep the ContextVar token type visible for embedders that want explicit reset.
def reset_current(token: Token) -> None:  # pragma: no cover - thin typing helper
    _current.reset(token)
