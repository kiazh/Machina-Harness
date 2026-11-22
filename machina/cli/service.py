"""Foreground API server lifecycle commands.

Detached starts record their own PID and health endpoint under the Machina
home directory. Service management trusts that metadata only while the process
and ``/health`` agree; database-client leases are used to detect foreground
``serve`` instances, which service stop must never kill. This gives native
PostgreSQL and managed-container installs one safe lifecycle path without a
second, independently stale PID authority.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
import typer

from machina.cli import output
from machina.cli.output import console
from machina.db import lifecycle

app = typer.Typer(help="Manage the background Machina HTTP service.")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
_START_TIMEOUT_SECONDS = 20.0


def _home() -> Path:
    raw = os.environ.get("MACHINA_HOME", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
    return Path.home() / ".machina"


def _trusted_executable(name: str) -> str | None:
    path = shutil.which(name)
    if not path:
        return None
    resolved = Path(path).resolve()
    return str(resolved) if resolved.is_absolute() else None


def _process_start_marker(pid: int) -> str | None:
    """Stable process birth marker used to reject a recycled PID."""
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                creation = wintypes.FILETIME()
                exited = wintypes.FILETIME()
                kernel = wintypes.FILETIME()
                user = wintypes.FILETIME()
                if not kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exited),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return None
                return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat.rsplit(") ", 1)[1].split()
        return fields[19]
    except (OSError, IndexError):
        return None


def _metadata_path() -> Path:
    return _home() / "run" / "service.json"


def _read_metadata() -> dict[str, Any]:
    path = _metadata_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_metadata(data: dict[str, Any]) -> None:
    path = _metadata_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as error:
        output.muted(f"Could not write service metadata: {error}")


def _clear_metadata() -> None:
    with suppress(OSError):
        _metadata_path().unlink(missing_ok=True)


def _serve_clients() -> list[dict[str, Any]]:
    return [item for item in lifecycle.live_clients() if item.get("role") == "serve"]


def _metadata_pid() -> int:
    try:
        return int(_read_metadata().get("pid") or 0)
    except (TypeError, ValueError):
        return 0


def _metadata_start_matches(metadata: dict[str, Any], pid: int) -> bool:
    expected = str(metadata.get("start_marker") or "")
    if not expected:
        return True
    current = _process_start_marker(pid)
    return current is not None and current == expected


def _serve_pid(item: dict[str, Any]) -> int:
    return int(item.get("child_pid") or item.get("pid") or 0)


def _health_url(host: str, port: int) -> str:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host  # nosec B104 - probe alias, not a bind
    return f"http://{probe_host}:{port}/health"


def _health(host: str, port: int) -> str:
    try:
        response = httpx.get(_health_url(host, port), timeout=1.0)
    except httpx.HTTPError as error:
        return f"unreachable ({type(error).__name__})"
    if response.status_code == 200:
        try:
            version = response.json().get("version", "")
        except ValueError:
            version = ""
        return f"healthy{' v' + version if version else ''}"
    return f"HTTP {response.status_code}"


def _wait_until_ready(process: subprocess.Popen[Any], host: str, port: int) -> bool:
    deadline = time.monotonic() + _START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if _health(host, port).startswith("healthy"):
            return True
        time.sleep(0.2)
    return process.poll() is None


def _wait_until_stopped(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _health(host, port).startswith("healthy"):
            return True
        time.sleep(0.2)
    return not _health(host, port).startswith("healthy")


def _log_tail(path: Path, lines: int = 12) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _spawn(host: str, port: int) -> subprocess.Popen[Any]:
    log_path = _home() / "run" / "service.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    command = [
        str(Path(sys.executable).resolve()),
        "-m",
        "machina",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "cwd": os.getcwd(),
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    finally:
        log.close()
    _write_metadata(
        {
            "pid": process.pid,
            "host": host,
            "port": port,
            "log": str(log_path),
            "started_at": time.time(),
            "start_marker": _process_start_marker(process.pid),
        }
    )
    return process


def _start(host: str, port: int, *, quiet: bool = False) -> bool:
    clients = _serve_clients()
    if clients:
        if not quiet:
            output.muted(
                "A serve process is already running (PIDs "
                + ", ".join(str(_serve_pid(item)) for item in clients)
                + "); service start will not create a second one."
            )
        return False
    metadata_pid = _metadata_pid()
    if metadata_pid > 0 and lifecycle._alive(metadata_pid):
        metadata = _read_metadata()
        if not _metadata_start_matches(metadata, metadata_pid):
            _clear_metadata()
            if not quiet:
                output.error(
                    "Recorded service PID was recycled by another process; "
                    "stale metadata was cleared."
                )
            return False
        meta_host = str(metadata.get("host") or host)
        try:
            meta_port = int(metadata.get("port") or port)
        except (TypeError, ValueError):
            meta_port = port
        if _health(meta_host, meta_port).startswith("healthy"):
            if not quiet:
                output.muted(f"Machina service is already running: {metadata_pid}")
            return False
        if not quiet:
            output.error(
                "A service process is recorded but is not healthy. "
                "Run `machina service stop --force` before starting again."
            )
        return False
    if _health(host, port).startswith("healthy"):
        if not quiet:
            output.error(
                "A server is already listening on the requested address but is "
                "not managed by `machina service`; refusing to start a duplicate."
            )
        return False
    process = _spawn(host, port)
    ready = _wait_until_ready(process, host, port)
    if process.poll() is not None:
        output.error(f"Machina service exited with code {process.returncode}.")
        tail = _log_tail(Path(_read_metadata().get("log", "")))
        if tail:
            console.print(tail, markup=False, highlight=False)
        _clear_metadata()
        return False
    state = "ready" if ready else "still starting"
    if not quiet:
        console.print(f"[green]Machina service {state}[/green]")
        console.print(f"  PID: {process.pid}")
        console.print(f"  URL: http://{host}:{port}")
        console.print(f"  Log: {_read_metadata().get('log', '')}")
    return True


def _terminate(pid: int, *, force: bool = False) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        taskkill = _trusted_executable("taskkill")
        if taskkill is None:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
            return
        command = [taskkill, "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        subprocess.run(command, capture_output=True, check=False)
        return
    with suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def _wait_gone(pids: set[int], timeout: float = 10.0) -> set[int]:
    deadline = time.monotonic() + timeout
    remaining = set(pids)
    while remaining and time.monotonic() < deadline:
        remaining = {pid for pid in remaining if lifecycle._alive(pid)}
        if remaining:
            time.sleep(0.15)
    return remaining


def _stop(*, quiet: bool = False, force: bool = False) -> bool:
    metadata = _read_metadata()
    try:
        metadata_pid = int(metadata.get("pid") or 0)
    except (TypeError, ValueError):
        metadata_pid = 0
    check_host = str(metadata.get("host") or DEFAULT_HOST)
    try:
        check_port = int(metadata.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        check_port = DEFAULT_PORT
    if metadata_pid <= 0:
        if not quiet:
            output.muted("No managed Machina service is running.")
        return False
    if not lifecycle._alive(metadata_pid):
        _clear_metadata()
        if not quiet:
            output.muted("No managed Machina service is running.")
        return False
    if not _metadata_start_matches(metadata, metadata_pid):
        _clear_metadata()
        if not quiet:
            output.error(
                "Recorded service PID no longer matches the process that was "
                "started; refusing to terminate an unrelated process."
            )
        return False
    if not force and not _health(check_host, check_port).startswith("healthy"):
        if not quiet:
            output.error(
                "Recorded service process is not healthy; refusing to kill "
                "an unverified PID. Use --force only if you are sure."
            )
        return False
    pids = {metadata_pid}
    for pid in pids:
        _terminate(pid)
    remaining = _wait_gone(pids)
    for pid in remaining:
        _terminate(pid, force=True)
    remaining = _wait_gone(remaining, timeout=3.0)
    stopped = _wait_until_stopped(check_host, check_port)
    if remaining and not quiet:
        output.error(f"Service process(es) did not exit: {sorted(remaining)}")
    elif not stopped and not quiet:
        output.error("Service process exited but its health endpoint still responds.")
    elif not quiet:
        console.print("[green]Machina service stopped.[/green]")
    _clear_metadata()
    return not remaining and stopped


@app.command("start")
def service_start(
    host: str = typer.Option(DEFAULT_HOST, "--host", "-h", help="Bind host"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Bind port"),
) -> None:
    """Start the API server as a detached background process."""
    if not _start(host, port):
        raise typer.Exit(1)


@app.command("status")
def service_status(
    host: str | None = typer.Option(None, "--host", "-h", help="Health-check host"),
    port: int | None = typer.Option(None, "--port", "-p", help="Health-check port"),
) -> None:
    """Show the background service, its PIDs, and its HTTP health."""
    metadata = _read_metadata()
    actual_host = host or str(metadata.get("host") or DEFAULT_HOST)
    try:
        actual_port = int(port or metadata.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        actual_port = DEFAULT_PORT
    metadata_pid = _metadata_pid()
    if metadata_pid > 0 and lifecycle._alive(metadata_pid):
        if not _metadata_start_matches(metadata, metadata_pid):
            _clear_metadata()
            output.error(
                "Recorded service PID was recycled by another process; stale metadata was cleared."
            )
            raise typer.Exit(1)
        health = _health(actual_host, actual_port)
        if not health.startswith("healthy"):
            output.error(
                f"Recorded service PID {metadata_pid} is alive but {health}; "
                "refusing to report it as running."
            )
            raise typer.Exit(1)
        console.print("[green]Machina service is running[/green]")
        console.print(f"  PID: {metadata_pid}")
        console.print(f"  Health: {health}")
        if metadata.get("log"):
            console.print(f"  Log: {metadata['log']}")
        return
    foreground = _serve_clients()
    if foreground:
        output.error(
            "A foreground `machina serve` process is running (PIDs "
            + ", ".join(str(_serve_pid(item)) for item in foreground)
            + "); `machina service` manages only detached instances."
        )
        raise typer.Exit(1)
    if _health(actual_host, actual_port).startswith("healthy"):
        output.error(
            "An unmanaged server is listening on the requested address; "
            "`machina service` manages only detached instances."
        )
        raise typer.Exit(1)
    output.muted("Machina service is not running.")
    _clear_metadata()
    raise typer.Exit(1)


@app.command("stop")
def service_stop(
    force: bool = typer.Option(
        False,
        "--force",
        help="Terminate a recorded PID even when its health endpoint is unreachable",
    ),
) -> None:
    """Stop only the background API service, leaving the TUI running."""
    if not _stop(force=force):
        raise typer.Exit(1)


@app.command("restart")
def service_restart(
    host: str | None = typer.Option(None, "--host", "-h", help="Bind host"),
    port: int | None = typer.Option(None, "--port", "-p", help="Bind port"),
) -> None:
    """Stop the service if present, then start it detached."""
    metadata = _read_metadata()
    _stop(quiet=True)
    actual_host = host or str(metadata.get("host") or DEFAULT_HOST)
    try:
        actual_port = int(port or metadata.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        actual_port = DEFAULT_PORT
    if not _start(actual_host, actual_port):
        raise typer.Exit(1)
