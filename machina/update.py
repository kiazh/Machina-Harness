"""Best-effort checkout update at interactive launch.

Every interactive launch checks ``origin/main``; there is no check cache.
Only a source checkout with a git remote participates. The update is skipped
when the worktree is dirty so local work is never overwritten. Set
``MACHINA_AUTO_UPDATE=0`` to disable it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
_UPDATE_STATE = "machina-update-state.json"


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


def _is_shallow_repository(root: Path) -> bool:
    """True for the shallow clone made by the installer.

    A shallow checkout has no local merge base with a newer ``origin/main``,
    so the normal ahead/behind comparison can report local commits that are
    not actually local. Unshallow once before comparing histories.
    """
    result = _run(["git", "rev-parse", "--is-shallow-repository"], cwd=root, timeout=5)
    return bool(
        result is not None and result.returncode == 0 and result.stdout.strip().lower() == "true"
    )


def _fetch_origin(root: Path) -> subprocess.CompletedProcess[str] | None:
    if _is_shallow_repository(root):
        return _run(
            ["git", "fetch", "--unshallow", "--quiet", "origin", "main"],
            cwd=root,
            timeout=90,
        )
    return _run(["git", "fetch", "--quiet", "origin", "main"], cwd=root, timeout=15)


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


def _state_path(root: Path) -> Path | None:
    git_dir = _git_dir(root)
    return git_dir / _UPDATE_STATE if git_dir is not None else None


def _read_update_state(root: Path) -> dict[str, object] | None:
    path = _state_path(root)
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("previous_commit"), str):
        return None
    if not isinstance(payload.get("updated_commit"), str):
        return None
    return payload


def _write_update_state(
    root: Path, *, previous_commit: str, updated_commit: str, previous_version: str
) -> None:
    path = _state_path(root)
    if path is None:
        return
    try:
        path.write_text(
            json.dumps(
                {
                    "previous_commit": previous_commit,
                    "updated_commit": updated_commit,
                    "previous_version": previous_version,
                    "updated_at": time.time(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def rollback() -> bool:
    """Reset a clean source checkout to the commit captured by the last update.

    This is intentionally source-checkout-only. It refuses dirty worktrees and
    a HEAD that no longer matches the recorded post-update commit, so a later
    local commit cannot be silently discarded.
    """
    root = _repo_root()
    if root is None:
        print("Rollback is only available from a source checkout.", file=sys.stderr)
        return False
    state = _read_update_state(root)
    if state is None:
        print("No recorded update to roll back.", file=sys.stderr)
        return False
    dirty = _run(["git", "status", "--porcelain"], cwd=root, timeout=5)
    if dirty is None or dirty.returncode != 0 or dirty.stdout.strip():
        print(
            "Rollback refused: this checkout has local changes.",
            file=sys.stderr,
        )
        return False
    current = _run(["git", "rev-parse", "HEAD"], cwd=root, timeout=5)
    current_commit = (
        current.stdout.strip() if current is not None and current.returncode == 0 else ""
    )
    if current_commit != str(state.get("updated_commit") or ""):
        print(
            "Rollback refused: HEAD no longer matches the recorded update.",
            file=sys.stderr,
        )
        return False
    previous = str(state.get("previous_commit") or "")
    exists = _run(["git", "cat-file", "-e", previous], cwd=root, timeout=5)
    if exists is None or exists.returncode != 0:
        print("Rollback refused: the previous commit is no longer present.", file=sys.stderr)
        return False
    reset = _run(["git", "reset", "--hard", previous], cwd=root, timeout=45)
    if reset is None or reset.returncode != 0:
        print("Rollback failed while resetting the checkout.", file=sys.stderr)
        return False
    path = _state_path(root)
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    version = str(state.get("previous_version") or "previous version")
    print(f"Rolled back to {previous[:12]} ({version}).", file=sys.stderr)
    print(
        "Dependencies were not changed; reinstall from the checkout if packaging changed.",
        file=sys.stderr,
    )
    return True


class _UpdateSpinner:
    """Launch-time update scene on stderr; silent when stderr is not a TTY.

    Import-light on purpose: the interactive entry point runs before the
    Typer/Rich command surface. The scene is a small three-line card with a
    moving comet, phase label, version delta and elapsed time. Terminals
    without ANSI keep the old single-line spinner.
    """

    _BRAILLE = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
    _TITLE = "MACHINA"
    _ACCENT = (142, 240, 207)
    _SECONDARY = (209, 180, 246)
    _SUCCESS = (159, 207, 154)

    def __init__(self, label: str, detail: str = "") -> None:
        self._label = label
        self._detail = detail
        self._enabled = sys.stderr.isatty()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame = 0
        self._drawn = 0
        self._started = time.monotonic()
        self._cleared = False
        self._width = 0
        encoding = getattr(sys.stderr, "encoding", None) or "utf-8"
        self._frames = "|/-\\"
        try:
            self._BRAILLE.encode(encoding)
            self._frames = self._BRAILLE
        except (LookupError, UnicodeEncodeError):
            pass
        self._fancy = self._enabled and self._ansi_capable()
        try:
            "\u2726\u25c6\u2501\u2500".encode(encoding)
        except (LookupError, UnicodeEncodeError):
            self._fancy = False
        self._color = self._fancy and not os.environ.get("NO_COLOR")
        self._truecolor = (
            "truecolor" in os.environ.get("COLORTERM", "").lower()
            or "24bit" in os.environ.get("COLORTERM", "").lower()
        )

    @staticmethod
    def _ansi_capable() -> bool:
        if os.name != "nt":
            return os.environ.get("TERM", "dumb") != "dumb"
        return bool(
            os.environ.get("WT_SESSION")
            or os.environ.get("TERM")
            or os.environ.get("ANSICON")
            or os.environ.get("ConEmuANSI")
            or os.environ.get("TERM_PROGRAM")
        )

    def _ink(self, text: str, rgb: tuple[int, int, int], code: int = 121) -> str:
        if not self._color:
            return text
        if self._truecolor:
            return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\x1b[39m"
        return f"\x1b[38;5;{code}m{text}\x1b[39m"

    def _dim(self, text: str) -> str:
        return self._ink(text, (150, 156, 168), 244)

    def _mix(
        self, start: tuple[int, int, int], end: tuple[int, int, int], t: float
    ) -> tuple[int, int, int]:
        return tuple(int(a + (b - a) * t) for a, b in zip(start, end))

    def _title(self) -> str:
        return "".join(
            self._ink(
                char, self._mix(self._ACCENT, self._SECONDARY, index / max(1, len(self._TITLE) - 1))
            )
            for index, char in enumerate(self._TITLE)
        )

    def start(self) -> _UpdateSpinner:
        if self._enabled:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def _spin(self) -> None:
        if self._fancy:
            self._write("\x1b[?25l")
        while not self._stop.wait(0.08):
            self._render()
            self._frame += 1
        if self._fancy:
            self._write("\x1b[?25h")

    def _write(self, text: str) -> None:
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except (OSError, UnicodeEncodeError, ValueError):
            pass

    def _elapsed(self) -> str:
        seconds = max(0, int(time.monotonic() - self._started))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _render(self) -> None:
        with self._lock:
            label = self._label
            detail = self._detail
        if not self._fancy:
            line = f"{self._frames[self._frame % len(self._frames)]} {label}"
            self._width = max(self._width, len(line))
            self._write("\r" + line + " " * (self._width - len(line)))
            return
        columns = shutil.get_terminal_size((80, 24)).columns
        room = max(12, min(48, columns - 22))
        span = max(8, room - 2)
        travel = span * 2 - 2
        position = self._frame % travel
        if position >= span:
            position = travel - position
        bar = []
        for index in range(span):
            if index == position:
                bar.append(self._ink("\u25c6", self._ACCENT))
            elif 0 <= position - index <= 8:
                ratio = 1.0 - (position - index) / 9.0
                bar.append(self._ink("\u2501", self._mix(self._ACCENT, self._SECONDARY, ratio)))
            elif index < position:
                bar.append(self._dim("\u2501"))
            else:
                bar.append(self._dim("\u2500"))
        title = self._title()
        suffix = self._dim("\u00b7  update") if columns >= 64 else ""
        clock = self._dim(self._elapsed())
        star = self._ink("\u2726", self._ACCENT)
        first = f"  {star}  {title}  {suffix}".rstrip()
        gap = max(1, columns - len(self._plain(first)) - len(self._plain(clock)) - 2)
        top = f"{first}{' ' * gap}{clock}"
        label = label[: max(1, columns - span - 5)]
        second = f"  {''.join(bar)}  {self._ink(label, self._SECONDARY, 183)}"
        third = f"  {self._dim(detail)}" if detail else ""
        lines = [top, second, third]
        self._paint(lines)

    @staticmethod
    def _plain(text: str) -> str:
        # Visible width for the short ASCII title/clock; the coloured spans
        # only wrap those characters, so stripping CSI is sufficient here.
        out = []
        index = 0
        while index < len(text):
            if text[index] == "\x1b":
                end = text.find("m", index)
                if end == -1:
                    break
                index = end + 1
                continue
            out.append(text[index])
            index += 1
        return "".join(out)

    def _paint(self, lines: list[str]) -> None:
        for index, line in enumerate(lines):
            self._write(f"\r\x1b[2K{line}")
            if index < len(lines) - 1:
                self._write("\n")
        if len(lines) > 1:
            self._write(f"\x1b[{len(lines) - 1}A\r")
        self._drawn = len(lines)

    def set(self, label: str, detail: str | None = None) -> None:
        with self._lock:
            self._label = label
            if detail is not None:
                self._detail = detail

    def clear(self) -> None:
        """Stop the animation and erase its block. Safe to call more than once."""
        if self._cleared:
            return
        self._cleared = True
        if not self._enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._fancy and self._drawn:
            for index in range(self._drawn):
                self._write("\r\x1b[2K")
                if index < self._drawn - 1:
                    self._write("\n")
            self._write(f"\x1b[{self._drawn - 1}A\r")
            self._drawn = 0
        elif self._width:
            self._write("\r" + " " * self._width + "\r")
            self._width = 0

    def finish(self, message: str, tone: str = "plain") -> None:
        self.clear()
        glyph = {"success": "\u2713", "warning": "!", "error": "\u2717"}.get(tone, "\u00b7")
        encoding = getattr(sys.stderr, "encoding", None) or "utf-8"
        try:
            glyph.encode(encoding)
        except (LookupError, UnicodeEncodeError):
            glyph = {"success": "+", "warning": "!", "error": "x"}.get(tone, "-")
        if self._color:
            colour = (
                self._SUCCESS
                if tone == "success"
                else (228, 183, 122)
                if tone == "warning"
                else (224, 138, 128)
                if tone == "error"
                else self._ACCENT
            )
            message = f"{self._ink(glyph, colour)} {message}"
        else:
            message = f"{glyph} {message}"
        print(message, file=sys.stderr)


def auto_update(*, force: bool = False) -> None:
    """Fast-forward a clean checkout to origin/main and re-exec once.

    ``force`` is for the explicit ``machina update`` command; launch-time
    auto-update stays quiet for CI and non-interactive output.
    """
    if not force and os.environ.get("MACHINA_AUTO_UPDATE", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return
    # One-shot guard for the process that re-execs after an update. The next
    # real launch checks again; this child must not fetch twice.
    if os.environ.pop("MACHINA_UPDATE_REEXEC", None):
        return
    if not force and (os.environ.get("CI") or not sys.stdout.isatty()):
        return
    root = _repo_root()
    if root is None:
        return

    status = _UpdateSpinner("Fetching origin/main", "checking for new commits").start()
    try:
        _check_and_apply_update(root, status, reexec=not force)
    finally:
        status.clear()


def _check_and_apply_update(root: Path, status: _UpdateSpinner, *, reexec: bool = True) -> None:
    fetched = _fetch_origin(root)
    if fetched is None or fetched.returncode != 0:
        message = "Could not check for updates; continuing on the local version."
        status.finish(message, "error")
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
        status.finish(message, "error")
        return
    try:
        ahead, behind = (int(part) for part in counts.stdout.split())
    except ValueError:
        status.finish(message, "error")
        return

    from machina import __version__

    noun = "commit" if behind == 1 else "commits"
    if behind == 0:
        message = f"Machina is up to date (v{__version__})."
        status.finish(message, "success")
        return
    if ahead > 0:
        # Local commits are not on origin/main, so a fast-forward would
        # discard them. Leave that decision to the operator.
        message = (
            f"origin/main has {behind} new {noun}, but this branch has local commits; "
            "skipping auto-update."
        )
        status.finish(message, "warning")
        return

    dirty = _run(["git", "status", "--porcelain"], cwd=root, timeout=5)
    if dirty is None or dirty.returncode != 0 or dirty.stdout.strip():
        message = (
            f"origin/main has {behind} new {noun}, but this checkout has local changes; "
            "not pulling automatically."
        )
        status.finish(message, "warning")
        return

    status.set(
        "Updating checkout", f"{behind} new {noun} · v{__version__} -> {remote_version or '?'}"
    )
    previous = _run(["git", "rev-parse", "HEAD"], cwd=root, timeout=5)
    previous_commit = (
        previous.stdout.strip() if previous is not None and previous.returncode == 0 else ""
    )
    previous_version = __version__
    pulled = _run(
        ["git", "pull", "--ff-only", "--quiet", "origin", "main"],
        cwd=root,
        timeout=45,
    )
    if pulled is None or pulled.returncode != 0:
        message = "Could not fast-forward; continuing on the local version."
        status.finish(message, "error")
        return
    updated = _run(["git", "rev-parse", "HEAD"], cwd=root, timeout=5)
    updated_commit = (
        updated.stdout.strip() if updated is not None and updated.returncode == 0 else ""
    )
    if previous_commit and updated_commit:
        _write_update_state(
            root,
            previous_commit=previous_commit,
            updated_commit=updated_commit,
            previous_version=previous_version,
        )
    # Editable installs see source changes immediately; refresh dependency
    # metadata only when packaging files changed. Failure is non-fatal: the
    # updated code may still run with the existing environment.
    changed = _run(["git", "diff", "--name-only", "HEAD@{1}", "HEAD"], cwd=root, timeout=5)
    if changed is not None and changed.stdout.strip():
        if any(line in {"pyproject.toml", "uv.lock"} for line in changed.stdout.splitlines()):
            status.set("Refreshing dependencies", "packaging changed")
            _run(
                [sys.executable, "-m", "pip", "install", "-e", str(root), "--quiet"],
                cwd=root,
                timeout=180,
            )
    version_note = (
        "" if remote_version in (None, __version__) else f" {__version__} -> {remote_version}"
    )
    if reexec:
        status.finish(
            f"Updated Machina{version_note} ({behind} new {noun}); restarting...", "success"
        )
    else:
        status.finish(f"Updated Machina{version_note} ({behind} new {noun}).", "success")
        print("Restart Machina to use the updated version.", file=sys.stderr)
        return
    # The re-exec runs this module again; the marker is fresh, so suppress the
    # cached status it would otherwise print over the new UI's first frame.
    os.environ["MACHINA_UPDATE_REEXEC"] = "1"
    try:
        os.execv(sys.executable, [sys.executable, "-m", "machina", *sys.argv[1:]])
    except OSError as error:
        print(
            f"Updated, but could not restart automatically ({error}). Restart Machina.",
            file=sys.stderr,
        )
