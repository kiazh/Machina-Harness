"""Best-effort checkout update at interactive launch.

Only a source checkout with a git remote participates. The update is skipped
when the worktree is dirty so local work is never overwritten. Set
``MACHINA_AUTO_UPDATE=0`` to disable it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for piece in re.findall(r"\d+", value)[:4]:
        parts.append(int(piece))
    return tuple(parts)


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


def auto_update() -> None:
    """Fast-forward a clean checkout to origin/main and re-exec once."""
    if os.environ.get("MACHINA_AUTO_UPDATE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    if os.environ.get("CI") or not sys.stdout.isatty():
        return
    root = _repo_root()
    if root is None:
        return

    fetched = _run(["git", "fetch", "--quiet", "origin", "main"], cwd=root, timeout=8)
    if fetched is None or fetched.returncode != 0:
        return
    remote = _run(["git", "show", "origin/main:pyproject.toml"], cwd=root, timeout=5)
    if remote is None or remote.returncode != 0:
        return
    match = _VERSION_RE.search(remote.stdout)
    if match is None:
        return
    from machina import __version__

    if _version_tuple(match.group(1)) <= _version_tuple(__version__):
        return

    dirty = _run(["git", "status", "--porcelain"], cwd=root, timeout=5)
    if dirty is None or dirty.returncode != 0 or dirty.stdout.strip():
        print(
            f"[update] {match.group(1)} is available, but this checkout has local changes; "
            "not pulling automatically.",
            file=sys.stderr,
        )
        return

    pulled = _run(["git", "pull", "--ff-only", "--quiet"], cwd=root, timeout=45)
    if pulled is None or pulled.returncode != 0:
        print(
            "[update] could not fast-forward the checkout; continuing on the local version.",
            file=sys.stderr,
        )
        return
    print(
        f"[update] updated Machina {__version__} -> {match.group(1)}; restarting...",
        file=sys.stderr,
    )
    # Editable installs see source changes immediately; refresh dependency
    # metadata only when packaging files changed. Failure is non-fatal: the
    # updated code may still run with the existing environment.
    changed = _run(["git", "diff", "--name-only", "HEAD@{1}", "HEAD"], cwd=root, timeout=5)
    if changed is not None and changed.stdout.strip():
        if any(line in {"pyproject.toml", "uv.lock"} for line in changed.stdout.splitlines()):
            _run(
                [sys.executable, "-m", "pip", "install", "-e", str(root), "--quiet"],
                cwd=root,
                timeout=180,
            )
    os.execv(sys.executable, [sys.executable, "-m", "machina", *sys.argv[1:]])
