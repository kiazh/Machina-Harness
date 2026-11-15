"""Best-effort checkout update at interactive launch.

Only a source checkout with a git remote participates. The update is skipped
when the worktree is dirty so local work is never overwritten. Set
``MACHINA_AUTO_UPDATE=0`` to disable it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
#: Network checks are best-effort and must not tax every launch. Reuse the
#: result of a recent fetch/check for this long before asking origin again.
_CHECK_INTERVAL_SECONDS = 15 * 60.0
_CHECK_MARKER = "machina-auto-update-check"


def _run(args: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _repo_root() -> Path | None:
    root = Path(__file__).resolve().parents[1]
    if (root / ".git").exists() and (root / "pyproject.toml").is_file():
        return root
    return None


def _git_dir(root: Path) -> Path | None:
    """Resolve the repository metadata directory, including worktrees."""
    dotgit = root / ".git"
    try:
        if dotgit.is_dir():
            return dotgit
        if dotgit.is_file():
            marker = dotgit.read_text(encoding="utf-8", errors="replace").strip()
            if marker.startswith("gitdir:"):
                return (root / marker.partition(":")[2].strip()).resolve()
    except OSError:
        pass
    return None


def _mark_checked(root: Path, status: str = "current", message: str = "") -> None:
    """Record the check result so later launches can report it without the network."""
    git_dir = _git_dir(root)
    if git_dir is None:
        return
    try:
        (git_dir / _CHECK_MARKER).write_text(
            json.dumps({"status": status, "message": message}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _cached_check(root: Path) -> dict[str, object] | None:
    """Return the recent check result, including a human-readable message.

    Only Machina's own marker counts. Other tools write ``FETCH_HEAD`` too,
    and treating any fetch as a completed check hid the launch status (and
    delayed real updates) for 15 minutes after a manual ``git fetch``.
    Worktrees store the marker under their resolved gitdir, not the ``.git``
    file.
    """
    git_dir = _git_dir(root)
    if git_dir is None:
        return None
    marker = git_dir / _CHECK_MARKER
    try:
        age = time.time() - marker.stat().st_mtime
        if age >= _CHECK_INTERVAL_SECONDS:
            return None
        raw = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "status": str(payload.get("status", "unknown")),
        "message": str(payload.get("message", "")),
        "age": age,
    }


def _check_is_fresh(root: Path) -> bool:
    """True when Machina's own recent check already answered the question."""
    return _cached_check(root) is not None


def _age_text(seconds: float) -> str:
    if seconds < 90:
        return "just now"
    return f"{int(seconds // 60)}m ago"


def _report_cached(cached: dict[str, object]) -> None:
    """Print the previous check's result without touching the network."""
    # The post-update re-exec already printed the "restarting" line; don't
    # repeat a cached status into the fresh UI start.
    if os.environ.pop("MACHINA_UPDATE_REEXEC", None):
        return
    message = str(cached.get("message") or "")
    if not message:
        return
    age = float(cached.get("age") or 0.0)
    print(f"{message} Last checked {_age_text(age)}.", file=sys.stderr)


class _UpdateSpinner:
    """Animated launch status on stderr; silent when stderr is not a TTY.

    Import-light on purpose: the interactive entry point runs before the
    Typer/Rich command surface, so a full :mod:`rich` spinner would slow the
    first frame. Frames fall back to ASCII on consoles that cannot encode
    braille.
    """

    def __init__(self, label: str) -> None:
        self._label = label
        self._enabled = sys.stderr.isatty()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._width = 0
        self._cleared = False
        self._frames = "|/-\\"
        braille = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
        encoding = getattr(sys.stderr, "encoding", None) or "utf-8"
        try:
            braille.encode(encoding)
        except (LookupError, UnicodeEncodeError):
            pass
        else:
            self._frames = braille

    def start(self) -> _UpdateSpinner:
        if self._enabled:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def _spin(self) -> None:
        index = 0
        while not self._stop.wait(0.09):
            with self._lock:
                line = f"{self._frames[index % len(self._frames)]} {self._label}"
            self._width = max(self._width, len(line))
            sys.stderr.write("\r" + line + " " * (self._width - len(line)))
            sys.stderr.flush()
            index += 1

    def set(self, label: str) -> None:
        with self._lock:
            self._label = label

    def clear(self) -> None:
        """Stop the animation and erase its line. Safe to call more than once."""
        if self._cleared:
            return
        self._cleared = True
        if not self._enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._width:
            sys.stderr.write("\r" + " " * self._width + "\r")
            sys.stderr.flush()

    def finish(self, message: str) -> None:
        self.clear()
        print(message, file=sys.stderr)


def auto_update() -> None:
    """Fast-forward a clean checkout to origin/main and re-exec once."""
    if os.environ.get("MACHINA_AUTO_UPDATE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    if os.environ.get("CI") or not sys.stdout.isatty():
        return
    root = _repo_root()
    if root is None:
        return
    cached = _cached_check(root)
    if cached is not None:
        _report_cached(cached)
        return

    status = _UpdateSpinner("Checking for updates...").start()
    try:
        _check_and_apply_update(root, status)
    finally:
        status.clear()


def _check_and_apply_update(root: Path, status: _UpdateSpinner) -> None:
    fetched = _run(["git", "fetch", "--quiet", "origin", "main"], cwd=root, timeout=3)
    if fetched is None or fetched.returncode != 0:
        message = "Could not check for updates; continuing on the local version."
        _mark_checked(root, "error", message)
        status.finish(message)
        return
    remote = _run(["git", "show", "origin/main:pyproject.toml"], cwd=root, timeout=5)
    remote_version = None
    if remote is not None and remote.returncode == 0:
        match = _VERSION_RE.search(remote.stdout)
        if match is not None:
            remote_version = match.group(1)

    # Compare history, not version strings: commits merged to main between
    # releases must still reach a source checkout. `--left-right` reports
    # `HEAD...origin/main` as "<ahead>\t<behind>".
    counts = _run(
        ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"],
        cwd=root,
        timeout=5,
    )
    message = "Could not read origin/main; continuing on the local version."
    if counts is None or counts.returncode != 0:
        _mark_checked(root, "error", message)
        status.finish(message)
        return
    try:
        ahead, behind = (int(part) for part in counts.stdout.split())
    except ValueError:
        _mark_checked(root, "error", message)
        status.finish(message)
        return

    from machina import __version__

    noun = "commit" if behind == 1 else "commits"
    if behind == 0:
        message = f"Machina is up to date (v{__version__})."
        _mark_checked(root, "current", message)
        status.finish(message)
        return
    if ahead > 0:
        # Local commits are not on origin/main, so a fast-forward would
        # discard them. Leave that decision to the operator.
        message = (
            f"origin/main has {behind} new {noun}, but this branch has local commits; "
            "skipping auto-update."
        )
        _mark_checked(root, "skipped", message)
        status.finish(message)
        return

    dirty = _run(["git", "status", "--porcelain"], cwd=root, timeout=5)
    if dirty is None or dirty.returncode != 0 or dirty.stdout.strip():
        message = (
            f"origin/main has {behind} new {noun}, but this checkout has local changes; "
            "not pulling automatically."
        )
        _mark_checked(root, "skipped", message)
        status.finish(message)
        return

    status.set(f"Updating ({behind} new {noun})...")
    pulled = _run(
        ["git", "pull", "--ff-only", "--quiet", "origin", "main"],
        cwd=root,
        timeout=45,
    )
    if pulled is None or pulled.returncode != 0:
        message = "Could not fast-forward; continuing on the local version."
        _mark_checked(root, "error", message)
        status.finish(message)
        return
    # Editable installs see source changes immediately; refresh dependency
    # metadata only when packaging files changed. Failure is non-fatal: the
    # updated code may still run with the existing environment.
    changed = _run(["git", "diff", "--name-only", "HEAD@{1}", "HEAD"], cwd=root, timeout=5)
    if changed is not None and changed.stdout.strip():
        if any(line in {"pyproject.toml", "uv.lock"} for line in changed.stdout.splitlines()):
            status.set("Refreshing dependencies...")
            _run(
                [sys.executable, "-m", "pip", "install", "-e", str(root), "--quiet"],
                cwd=root,
                timeout=180,
            )
    version_note = (
        "" if remote_version in (None, __version__) else f" {__version__} -> {remote_version}"
    )
    new_version = remote_version or __version__
    _mark_checked(
        root,
        "updated",
        f"Machina was updated to v{new_version} ({behind} new {noun}).",
    )
    status.finish(f"Updated Machina{version_note} ({behind} new {noun}); restarting...")
    # The re-exec runs this module again; the marker is fresh, so suppress the
    # cached status it would otherwise print over the new UI's first frame.
    os.environ["MACHINA_UPDATE_REEXEC"] = "1"
    os.execv(sys.executable, [sys.executable, "-m", "machina", *sys.argv[1:]])
