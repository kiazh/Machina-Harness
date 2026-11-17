"""Persistent Linux host shell: tmux + ttyd around the local terminal UI.

This is the first remote-host slice. It deliberately keeps the gateway and the
TUI local to the host: tmux owns persistence, ttyd owns the browser bridge,
and SSH or a private tunnel owns reachability. No new network protocol is
introduced, so the stdio gateway token model is unchanged.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import MISSING, asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_BIND",
    "DEFAULT_MODE",
    "DEFAULT_PORT",
    "DEFAULT_SESSION",
    "DEFAULT_USER",
    "HostError",
    "HostState",
    "build_ttyd_command",
    "host_home",
    "is_loopback",
    "load_state",
    "remove_state",
    "save_state",
    "start_host",
    "status_host",
    "stop_host",
    "ttyd_log_path",
]

DEFAULT_SESSION = "machina"
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 7681
DEFAULT_USER = "machina"
DEFAULT_MODE = "ask"
STATE_VERSION = 1
VALID_MODES = frozenset({"ask", "workspace", "sandbox", "full"})
_TMUX_NAME_RE = r"^[A-Za-z0-9_-]{1,64}$"
_TTYD_USER_RE = r"^[A-Za-z0-9._-]{1,64}$"
_TERMINATE_TIMEOUT_SECONDS = 3.0


class HostError(RuntimeError):
    """A host operation failed with a message safe to show to the operator."""


@dataclass(frozen=True)
class HostState:
    version: int
    session: str
    bind: str
    port: int
    username: str
    password: str
    ttyd_pid: int
    workspace: str
    mode: str
    started_at: str
    ttyd_log: str
    # False when the host attached ttyd to a tmux session it did not create.
    # Stopping the host must not kill a session owned by the operator.
    owns_session: bool = True


def host_home() -> Path:
    override = os.environ.get("MACHINA_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".machina"


def state_path() -> Path:
    return host_home() / "host.json"


def ttyd_log_path() -> Path:
    return host_home() / "logs" / "host-ttyd.log"


def load_state(path: Path | None = None) -> HostState | None:
    """Read the host state file; missing means "not running"."""
    target = path or state_path()
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            raise ValueError("unsupported host state")
        values: dict[str, Any] = {}
        for field in fields(HostState):
            if field.name in data:
                values[field.name] = data[field.name]
            elif field.default is not MISSING:
                values[field.name] = field.default
            else:
                raise ValueError(f"missing host state field: {field.name}")
        return HostState(**values)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HostError(f"cannot read host state at {target}: {error}") from None


def save_state(state: HostState, path: Path | None = None) -> Path:
    """Write the state atomically with owner-only permissions."""
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".host-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(state), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        if os.name == "posix":
            os.chmod(target, 0o600)
        return target
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def remove_state(path: Path | None = None) -> None:
    target = path or state_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass


def is_loopback(bind: str) -> bool:
    return bind.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def build_machina_command(mode: str) -> list[str]:
    return [sys.executable, "-m", "machina", "--mode", mode]


def build_tmux_command(tmux: str, session: str, workspace: str, mode: str) -> list[str]:
    """Create the detached tmux session that owns the persistent TUI."""
    # tmux starts the new session from the server's environment, not this
    # process's. Pass the host-relevant values explicitly so a WSL host can
    # point at the checkout .env and the TUI can still find Node.
    inherited = [
        f"{key}={os.environ[key]}"
        for key in (
            "MACHINA_ENV_FILE",
            "MACHINA_HOME",
            "DATABASE_URL",
            "MACHINA_PROVIDER",
            "MACHINA_MODEL",
            "MACHINA_DB_MANAGE",
            "MACHINA_DB_PORT",
            "MACHINA_DB_CONTAINER",
            "MACHINA_DB_STATE_DIR",
            "PATH",
        )
        if os.environ.get(key)
    ]
    return [
        tmux,
        "new-session",
        "-d",
        "-s",
        session,
        "-c",
        workspace,
        "env",
        "MACHINA_AUTO_UPDATE=0",
        *inherited,
        *build_machina_command(mode),
    ]


def build_ttyd_command(ttyd: str, tmux: str, state: HostState) -> list[str]:
    """Serve the existing browser terminal attached to the tmux session."""
    return [
        ttyd,
        "-i",
        state.bind,
        "-p",
        str(state.port),
        "-W",
        "-O",
        "-c",
        f"{state.username}:{state.password}",
        tmux,
        "attach-session",
        "-t",
        state.session,
    ]


def _ensure_supported() -> None:
    if os.name != "posix":
        raise HostError(
            "machina host requires Linux or macOS. On Windows run it inside WSL2 "
            "or a Linux container."
        )


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, **kwargs)


def _popen(command: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
    return subprocess.Popen(command, **kwargs)


def _tmux_session_alive(tmux: str | None, session: str) -> bool:
    if not tmux:
        return False
    completed = _run(
        [tmux, "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _tmux_kill(tmux: str | None, session: str) -> None:
    if not tmux:
        return
    _run(
        [tmux, "kill-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _signal_process_group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            os.kill(pid, signum)
        except OSError:
            return
    except OSError:
        return


def _terminate_ttyd(pid: int) -> None:
    if not process_alive(pid):
        return
    _signal_process_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + _TERMINATE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return
        time.sleep(0.1)
    _signal_process_group(pid, signal.SIGKILL)


def _validate_start(
    *,
    session: str,
    bind: str,
    port: int,
    mode: str,
    username: str,
    workspace: Path,
    allow_full: bool,
) -> None:
    if not re.fullmatch(_TMUX_NAME_RE, session):
        raise HostError("session must use letters, numbers, underscores or dashes (max 64).")
    if not re.fullmatch(_TTYD_USER_RE, username):
        raise HostError("user must use letters, numbers, dots, underscores or dashes (max 64).")
    if not is_loopback(bind):
        raise HostError(
            "machina host binds loopback only. Use SSH port forwarding, Tailscale, "
            "or Cloudflare Tunnel to reach it."
        )
    if not 1 <= port <= 65535:
        raise HostError("port must be between 1 and 65535.")
    if mode not in VALID_MODES:
        raise HostError(f"mode must be one of: {', '.join(sorted(VALID_MODES))}.")
    if mode == "full" and not allow_full:
        raise HostError(
            "full host mode is refused for remote access by default. "
            "Pass --allow-full only if you accept remote shell control."
        )
    if not workspace.is_dir():
        raise HostError(f"workspace is not a directory: {workspace}")


def start_host(
    *,
    workspace: Path,
    bind: str = DEFAULT_BIND,
    port: int = DEFAULT_PORT,
    mode: str = DEFAULT_MODE,
    session: str = DEFAULT_SESSION,
    username: str = DEFAULT_USER,
    allow_full: bool = False,
) -> tuple[HostState, bool]:
    """Create or reuse the host shell; return (state, created)."""
    _ensure_supported()
    _validate_start(
        session=session,
        bind=bind,
        port=port,
        mode=mode,
        username=username,
        workspace=workspace,
        allow_full=allow_full,
    )
    tmux = _which("tmux")
    ttyd = _which("ttyd")
    if not tmux:
        raise HostError("tmux is required. Install it (for example: sudo apt install tmux).")
    if not ttyd:
        raise HostError(
            "ttyd is required for the browser terminal. Install it "
            "(for example: sudo apt install ttyd) or download a release binary."
        )

    existing = load_state()
    if existing is not None:
        if _tmux_session_alive(tmux, existing.session) and process_alive(existing.ttyd_pid):
            return existing, False
        # A stale state file or a dead ttyd must not leave a half-running host.
        # Keep the state so a restart reuses the browser credential.
        _terminate_ttyd(existing.ttyd_pid)
        if existing.owns_session:
            _tmux_kill(tmux, existing.session)
    credential = existing or None

    created_session = False
    if not _tmux_session_alive(tmux, session):
        completed = _run(
            build_tmux_command(tmux, session, str(workspace), mode),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise HostError(f"could not create tmux session: {detail or 'tmux failed'}")
        created_session = True
    owns_session = created_session or (existing.owns_session if existing is not None else False)

    log = ttyd_log_path()
    log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = HostState(
        version=STATE_VERSION,
        session=session,
        bind=bind,
        port=port,
        username=credential.username if credential else username,
        password=credential.password if credential else secrets.token_urlsafe(18),
        ttyd_pid=0,
        workspace=str(workspace),
        mode=mode,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        ttyd_log=str(log),
        owns_session=owns_session,
    )
    try:
        with log.open("ab") as output:
            process = _popen(
                build_ttyd_command(ttyd, tmux, state),
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError:
        if created_session:
            _tmux_kill(tmux, session)
        raise
    time.sleep(0.4)
    if process.poll() is not None:
        tail = _log_tail(log)
        if created_session:
            _tmux_kill(tmux, session)
        raise HostError(
            f"ttyd exited during startup{': ' + tail if tail else ''}. "
            "Check whether the port is already in use."
        )
    state = HostState(**{**asdict(state), "ttyd_pid": process.pid})
    try:
        save_state(state)
    except Exception:
        _terminate_ttyd(process.pid)
        if created_session:
            _tmux_kill(tmux, session)
        raise
    return state, True


def status_host() -> tuple[HostState | None, bool, bool]:
    """Return (state, tmux_alive, ttyd_alive)."""
    state = load_state()
    if state is None:
        return None, False, False
    tmux = _which("tmux")
    return state, _tmux_session_alive(tmux, state.session), process_alive(state.ttyd_pid)


def stop_host(*, keep_session: bool = False) -> HostState | None:
    state = load_state()
    if state is None:
        return None
    _terminate_ttyd(state.ttyd_pid)
    if not keep_session and state.owns_session:
        _tmux_kill(_which("tmux"), state.session)
    # Keep the state so a later start reuses the browser credential.
    save_state(HostState(**{**asdict(state), "ttyd_pid": 0}))
    return state


def _log_tail(path: Path, lines: int = 6) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return " | ".join(content[-lines:])
