"""Start, stop, and inspect the user-installed Kiro local gateway.

Machina deliberately does not bundle or auto-install the third-party
OpenAI-compatible translator. This module only launches a checkout the user
has configured (``MACHINA_KIRO_GATEWAY_DIR`` / ``MACHINA_KIRO_GATEWAY_COMMAND``)
or a detected conventional location such as ``~/kiro-gateway``.

Every path is fail-open and bounded: startup runs off the request path, a
missing launcher or login is reported as status instead of raised into the
gateway, and ``/models`` never waits on this module. The process is managed
only when Machina started it; an externally started gateway is never killed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from machina.core.config import config
from machina.core.provider import kiro_proxy

__all__ = [
    "KiroGatewayError",
    "autostart_if_ready",
    "detect_launch",
    "gateway_reachable",
    "login_database_path",
    "login_detected",
    "profile_arn",
    "schedule_autostart",
    "start",
    "status",
    "stop",
]

logger = logging.getLogger(__name__)

START_TIMEOUT_SECONDS = 8.0
STOP_GRACE_SECONDS = 3.0
POLL_SECONDS = 0.2

#: Conventional checkout names. Detection never scans recursively; it only
#: checks these bounded locations. Users with another layout set the config.
_COMMON_GATEWAY_DIRS = (
    "~/kiro-gateway",
    "~/src/kiro-gateway",
    "~/code/kiro-gateway",
    "~/projects/kiro-gateway",
    "~/dev/kiro-gateway",
    "~/Documents/kiro-gateway",
    "~/.kiro-gateway",
)

_SAFE_STATE_LOCK = threading.Lock()
_SPAWN_LOCK = threading.Lock()
_process: subprocess.Popen[Any] | None = None
_last_error: str | None = None
_logged: set[str] = set()


class KiroGatewayError(RuntimeError):
    """A user-actionable gateway launch/stop failure."""


@dataclass(frozen=True)
class LaunchSpec:
    argv: tuple[str, ...]
    cwd: Path | None
    source: str


def _state_dir() -> Path:
    try:
        home = str(config.get("machina_home") or "").strip()
    except Exception:
        home = ""
    return Path(home).expanduser() if home else Path.home() / ".machina"


def log_path() -> Path:
    """Gateway stdout/stderr and lifecycle log (never contains credentials)."""
    return _state_dir() / "logs" / "kiro-gateway.log"


def _log(message: str) -> None:
    try:
        path = log_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass
    logger.debug("kiro gateway: %s", message)


def _log_once(key: str, message: str) -> None:
    if key in _logged:
        return
    _logged.add(key)
    _log(message)


def _config_text(key: str) -> str:
    try:
        return str(config.get(key) or "").strip()
    except Exception:
        return ""


def _autostart_enabled() -> bool:
    try:
        return bool(config.get("kiro_autostart"))
    except Exception:
        return False


def _configured_dir() -> Path | None:
    raw = _config_text("kiro_gateway_dir")
    if raw:
        return Path(raw).expanduser()
    for candidate in _COMMON_GATEWAY_DIRS:
        path = Path(candidate).expanduser()
        if path.is_dir():
            return path
    return None


def _python_for(directory: Path) -> str:
    candidates = (
        directory / ".venv" / "Scripts" / "python.exe",
        directory / ".venv" / "bin" / "python",
        directory / "venv" / "Scripts" / "python.exe",
        directory / "venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    if getattr(sys, "frozen", False):
        return shutil.which("python") or shutil.which("python3") or ""
    return sys.executable or shutil.which("python") or shutil.which("python3") or ""


def _split_command(command: str) -> list[str]:
    """Parse one command string without a shell.

    Windows paths contain backslashes, so the POSIX shlex mode would eat
    them. The non-POSIX mode keeps the surrounding quotes; strip those after
    tokenization. On POSIX, normal shell-like quoting applies.
    """
    if command.lstrip().startswith("["):
        parsed = json.loads(command)
        if not isinstance(parsed, list) or not parsed or not all(
            isinstance(part, str) and part for part in parsed
        ):
            raise ValueError("gateway command JSON must be a non-empty string array")
        return list(parsed)
    tokens = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        tokens = [
            token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'" else token
            for token in tokens
        ]
    return tokens


def detect_launch() -> tuple[LaunchSpec | None, str | None]:
    """Resolve the configured/detected launcher; never raises.

    Returns ``(spec, None)`` when a launcher is available, else
    ``(None, actionable_error)``.
    """
    command = _config_text("kiro_gateway_command")
    directory = _configured_dir()
    if command:
        try:
            argv = _split_command(command)
        except (ValueError, json.JSONDecodeError) as exc:
            return None, f"MACHINA_KIRO_GATEWAY_COMMAND is invalid: {exc}"
        if not argv:
            return None, "MACHINA_KIRO_GATEWAY_COMMAND is empty."
        cwd = directory
        if cwd is not None and not cwd.is_dir():
            return None, f"MACHINA_KIRO_GATEWAY_DIR does not exist: {cwd}"
        if cwd is None:
            for part in argv:
                candidate = Path(part).expanduser()
                if candidate.suffix.lower() == ".py" and candidate.is_file():
                    cwd = candidate.parent
                    break
        return LaunchSpec(tuple(argv), cwd, "configured"), None

    if directory is None:
        return (
            None,
            "No Kiro gateway launcher found. Clone jwadow/kiro-gateway to "
            "~/kiro-gateway or set MACHINA_KIRO_GATEWAY_DIR to the checkout.",
        )
    if not directory.is_dir():
        return None, f"MACHINA_KIRO_GATEWAY_DIR does not exist: {directory}"
    entry = directory / "main.py"
    if not entry.is_file():
        return None, f"No main.py found in MACHINA_KIRO_GATEWAY_DIR: {directory}"
    python = _python_for(directory)
    if not python:
        return None, "No Python interpreter found for the Kiro gateway checkout."
    return LaunchSpec((python, str(entry)), directory, "detected"), None


def _login_candidates() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("KIRO_CLI_DB_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(Path(local_app_data) / "Kiro-Cli" / "data.sqlite3")
    app_data = os.environ.get("APPDATA", "").strip()
    if app_data:
        candidates.append(Path(app_data) / "Kiro-Cli" / "data.sqlite3")
    home = Path.home()
    candidates.extend(
        (
            home / ".local" / "share" / "kiro-cli" / "data.sqlite3",
            home / "Library" / "Application Support" / "Kiro-Cli" / "data.sqlite3",
            home / ".kiro" / "data.sqlite3",
        )
    )
    return candidates


def login_database_path() -> Path | None:
    """Locate the Kiro CLI credential database without reading its contents."""
    seen: set[str] = set()
    for candidate in _login_candidates():
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def _kiro_ide_profile_paths() -> tuple[Path, ...]:
    """Bounded locations where Kiro IDE stores the active profile ARN."""
    relative = Path("Kiro") / "User" / "globalStorage" / "kiro.kiroagent" / "profile.json"
    paths: list[Path] = []
    app_data = os.environ.get("APPDATA", "").strip()
    if app_data:
        paths.append(Path(app_data) / relative)
    home = Path.home()
    paths.append(home / "Library" / "Application Support" / relative)
    paths.append(home / ".config" / relative)
    return tuple(paths)


def profile_arn() -> str:
    """Return the CodeWhisperer profile ARN needed by Builder ID accounts.

    ``kiro-cli`` stores AWS SSO OIDC tokens but not a profile ARN for free
    Builder ID accounts. Kiro IDE does store one locally; the latest gateway
    runtime requires it on chat requests. This reads only that public ARN and
    never returns or logs credentials.
    """
    explicit = os.environ.get("PROFILE_ARN", "").strip()
    if explicit:
        return explicit
    for path in _kiro_ide_profile_paths():
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        arn = str(payload.get("arn") or "").strip()
        if arn.startswith("arn:aws:codewhisperer:"):
            return arn
    return ""


def _checkout_profile_arn(cwd: Path | None) -> str:
    """Read an explicit ``PROFILE_ARN`` from the checkout's ``.env``.

    The gateway's dotenv loader does not override process environment. Do not
    inject an auto-detected IDE ARN when the checkout already declares one,
    which may deliberately point at a different account.
    """
    if cwd is None:
        return ""
    try:
        lines = (cwd / ".env").read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "PROFILE_ARN":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.strip()
    return ""


def login_detected() -> bool:
    """Cheap login signal (file presence only; never runs ``whoami``)."""
    return login_database_path() is not None


def gateway_reachable(*, timeout: float = kiro_proxy.PROBE_TIMEOUT_SECONDS) -> bool:
    return kiro_proxy.gateway_reachable(timeout=timeout)


def _prune_exited() -> tuple[subprocess.Popen[Any] | None, str | None]:
    global _process, _last_error
    with _SAFE_STATE_LOCK:
        if _process is not None:
            code = _process.poll()
            if code is not None:
                _process = None
                if _last_error is None:
                    _last_error = (
                        f"Kiro gateway process exited with code {code}. See {log_path()}."
                    )
        return _process, _last_error


def _set_error(message: str | None) -> None:
    global _last_error
    with _SAFE_STATE_LOCK:
        _last_error = message


def _stopped_detail(
    *,
    cli_installed: bool,
    login: bool,
    launch: LaunchSpec | None,
    launch_error: str | None,
) -> str:
    if login and launch is None:
        return launch_error or "No Kiro gateway launcher found."
    if not login and cli_installed:
        return "Kiro CLI found, but its login database was not detected. Run kiro-cli login."
    if not login:
        return (
            "Kiro CLI and its login database were not detected. "
            "Install the official CLI and run kiro-cli login."
        )
    if launch is None:
        return launch_error or "No Kiro gateway launcher found."
    if not _autostart_enabled():
        return "Kiro CLI login database detected. Start the local gateway with /auth kiro start."
    return (
        "Kiro CLI login database detected. Start the local gateway with "
        "/auth kiro start (auto-start is on)."
    )


def status() -> dict[str, Any]:
    """Return public gateway status; bounded and safe for every UI path."""
    proc, last_error = _prune_exited()
    reachable = gateway_reachable()
    if reachable and last_error is not None:
        _set_error(None)
        last_error = None

    launch, launch_error = detect_launch()
    login = login_detected()
    cli_installed = bool(kiro_proxy.cli_path())
    managed = proc is not None

    if reachable:
        state = "running"
        detail = (
            "Local gateway is running (started by Machina)."
            if managed
            else "Local gateway is running (started outside Machina)."
        )
    elif managed:
        state = "starting"
        detail = "Gateway process started; waiting for its loopback listener."
    elif last_error:
        state = "error"
        detail = last_error
    else:
        state = "stopped"
        detail = _stopped_detail(
            cli_installed=cli_installed,
            login=login,
            launch=launch,
            launch_error=launch_error,
        )

    return {
        "cliInstalled": cli_installed,
        "loginDetected": login,
        "reachable": reachable,
        "managed": managed,
        "state": state,
        "baseUrl": kiro_proxy.base_url(),
        "autostart": _autostart_enabled(),
        "launchConfigured": launch is not None,
        "detail": detail,
        "logPath": str(log_path()),
    }


def _spawn(spec: LaunchSpec) -> subprocess.Popen[Any]:
    path = log_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path, "ab") as handle:
        handle.write(
            (
                f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} starting "
                f"{' '.join(spec.argv)} (cwd={spec.cwd}, source={spec.source})\n"
            ).encode("utf-8", errors="replace")
        )
        handle.flush()
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": handle,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        # The community gateway prints emoji/box-drawing banners. With stdout
        # redirected to a file, Windows otherwise picks cp1252 and the child
        # dies on UnicodeEncodeError before it can bind the port.
        child_env = os.environ.copy()
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        child_env.setdefault("PYTHONUTF8", "1")
        child_env.setdefault("PYTHONUNBUFFERED", "1")
        if not child_env.get("PROFILE_ARN") and not _checkout_profile_arn(spec.cwd):
            arn = profile_arn()
            if arn:
                child_env["PROFILE_ARN"] = arn
        kwargs["env"] = child_env
        if spec.cwd is not None:
            kwargs["cwd"] = str(spec.cwd)
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(list(spec.argv), **kwargs)


def _terminate(proc: subprocess.Popen[Any]) -> bool:
    if proc.poll() is not None:
        return True
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        pass
    try:
        proc.wait(timeout=STOP_GRACE_SECONDS)
        return True
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=2.0)
        return True
    except subprocess.TimeoutExpired:
        return False


def start(*, wait: float = START_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Start the configured gateway and wait up to *wait* seconds for TCP.

    Returns ``{"started", "ready", "status"}``. A missing/invalid launcher or
    a spawn OSError raises :class:`KiroGatewayError` for the RPC layer; an
    early child exit is returned as an honest ``ready=False`` status.
    """
    global _process
    wait = max(0.0, min(float(wait), 30.0))
    with _SPAWN_LOCK:
        _prune_exited()
        if gateway_reachable():
            kiro_proxy.clear_catalog_cache()
            return {"started": False, "ready": True, "status": status()}

        with _SAFE_STATE_LOCK:
            proc = _process
        if proc is None:
            spec, launch_error = detect_launch()
            if spec is None:
                message = launch_error or "No Kiro gateway launcher found."
                _set_error(message)
                raise KiroGatewayError(message)
            try:
                proc = _spawn(spec)
            except OSError as exc:
                message = f"Could not start the Kiro gateway: {exc}"
                _set_error(message)
                raise KiroGatewayError(message) from exc
            with _SAFE_STATE_LOCK:
                _process = proc
                _last_error = None
            _log(f"started pid {proc.pid} ({spec.source})")

        deadline = time.monotonic() + wait
        while True:
            if gateway_reachable():
                kiro_proxy.clear_catalog_cache()
                _set_error(None)
                _log("listener is ready")
                return {"started": True, "ready": True, "status": status()}
            code = proc.poll()
            if code is not None:
                message = (
                    f"Kiro gateway process exited with code {code}. See {log_path()}."
                )
                with _SAFE_STATE_LOCK:
                    if _process is proc:
                        _process = None
                    _last_error = message
                _log(message)
                return {"started": True, "ready": False, "status": status()}
            if time.monotonic() >= deadline:
                _log(f"gateway did not listen within {wait:.1f}s")
                return {"started": True, "ready": False, "status": status()}
            time.sleep(POLL_SECONDS)


def stop() -> dict[str, Any]:
    """Stop only a gateway process this Machina process started."""
    global _process
    with _SPAWN_LOCK:
        proc, _last = _prune_exited()
        if proc is None:
            kiro_proxy.clear_catalog_cache()
            return {"stopped": False, "status": status()}
        _log(f"stopping pid {proc.pid}")
        stopped = _terminate(proc)
        with _SAFE_STATE_LOCK:
            if _process is proc:
                _process = None
            _last_error = None if stopped else f"Could not stop Kiro gateway pid {proc.pid}."
        kiro_proxy.clear_catalog_cache()
        _log("stopped" if stopped else "stop failed")
        return {"stopped": stopped, "status": status()}


def autostart_if_ready() -> dict[str, Any] | None:
    """Best-effort background auto-start; never raises into the caller."""
    if not _autostart_enabled():
        return None
    try:
        if kiro_proxy.gateway_reachable():
            return None
        if not kiro_proxy._is_loopback_url(kiro_proxy.base_url()):
            return None
        spec, launch_error = detect_launch()
        if spec is None:
            _log_once("autostart-no-launcher", launch_error or "no gateway launcher")
            return None
        if not login_detected():
            _log_once("autostart-no-login", "no Kiro CLI login database detected")
            return None
        _log("auto-start requested")
        return start(wait=START_TIMEOUT_SECONDS)
    except Exception as exc:
        _log(f"auto-start failed ({type(exc).__name__}: {exc})")
        return None


def schedule_autostart() -> asyncio.Task[dict[str, Any] | None] | None:
    """Schedule auto-start off the gateway initialization path."""
    if os.environ.get("MACHINA_GATEWAY_NO_SCHEDULER"):
        return None
    if not _autostart_enabled():
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(asyncio.to_thread(autostart_if_ready))
    task.add_done_callback(_log_task)
    return task


def _log_task(task: asyncio.Task[dict[str, Any] | None]) -> None:
    try:
        result = task.result()
    except (asyncio.CancelledError, Exception):
        return
    if result and result.get("ready"):
        logger.info("Kiro gateway auto-started")
    elif result:
        logger.warning("Kiro gateway auto-start did not become ready")


def _reset_for_tests() -> None:
    """Test helper: stop a managed child and clear the module cache."""
    global _process, _last_error
    with _SPAWN_LOCK:
        with _SAFE_STATE_LOCK:
            proc = _process
            _process = None
            _last_error = None
        if proc is not None and proc.poll() is None:
            _terminate(proc)
        kiro_proxy.clear_catalog_cache()
        _logged.clear()
