"""Foreground API server lifecycle commands.

Machina already records a database lease for every live client, including the
``serve`` role. Service management uses those leases as the source of truth
instead of keeping a second, independently stale PID file. The metadata file
only records where the detached wrapper wrote its log and which port it was
asked to bind.
"""

from __future__ import annotations

import json
import os
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
    return Path(raw).expanduser() if raw else Path.home() / ".machina"


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


def _verified_metadata_client(host: str, port: int) -> dict[str, Any] | None:
    """Use the launch metadata when no database lease exists.

    ``MACHINA_DB_MANAGE=0`` (native PostgreSQL) produces no lifecycle lease,
    so the metadata PID is the only handle. It is accepted only while the
    process exists *and* the service health endpoint answers, which prevents a
    recycled PID from being treated as a Machina server.
    """
    pid = _metadata_pid()
    if pid <= 0 or not lifecycle._alive(pid):
        return None
    if not _health(host, port).startswith("healthy"):
        return None
    return {"pid": pid, "role": "serve", "child_pid": 0, "source": "metadata"}


def _service_clients(host: str, port: int) -> list[dict[str, Any]]:
    clients = _serve_clients()
    if clients:
        return clients
    metadata = _verified_metadata_client(host, port)
    return [metadata] if metadata is not None else []


def _serve_pid(item: dict[str, Any]) -> int:
    return int(item.get("child_pid") or item.get("pid") or 0)


def _health_url(host: str, port: int) -> str:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
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
        if _serve_clients():
            return True
        if _health(host, port).startswith("healthy"):
            return True
        time.sleep(0.2)
    return process.poll() is None


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
        sys.executable,
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
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
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
        }
    )
    return process


def _start(host: str, port: int, *, quiet: bool = False) -> bool:
    clients = _serve_clients()
    if clients:
        if not quiet:
            output.muted(
                "Machina service is already running: "
                + ", ".join(str(_serve_pid(item)) for item in clients)
            )
        return False
    metadata_pid = _metadata_pid()
    if metadata_pid > 0 and lifecycle._alive(metadata_pid):
        metadata = _read_metadata()
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
        command = ["taskkill", "/PID", str(pid), "/T"]
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
    clients = _serve_clients()
    pids = {_serve_pid(item) for item in clients if _serve_pid(item) > 0}
    if not pids and metadata_pid > 0 and lifecycle._alive(metadata_pid):
        if force or _health(check_host, check_port).startswith("healthy"):
            pids = {metadata_pid}
        else:
            if not quiet:
                output.error(
                    "Recorded service process is not healthy; refusing to kill "
                    "an unverified PID. Use --force only if you are sure."
                )
            return False
    if not pids:
        if not quiet:
            output.muted("No Machina service is running.")
        _clear_metadata()
        return False
    for pid in pids:
        _terminate(pid)
    remaining = _wait_gone(pids)
    for pid in remaining:
        _terminate(pid, force=True)
    remaining = _wait_gone(remaining, timeout=3.0)
    if remaining and not quiet:
        output.error(f"Service process(es) did not exit: {sorted(remaining)}")
    elif not quiet:
        console.print("[green]Machina service stopped.[/green]")
    _clear_metadata()
    return not remaining


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
    clients = _service_clients(actual_host, actual_port)
    if not clients:
        metadata_pid = _metadata_pid()
        if metadata_pid > 0 and lifecycle._alive(metadata_pid):
            output.error(
                f"Recorded service PID {metadata_pid} is alive but its health "
                "endpoint is unreachable; refusing to report it as running."
            )
        else:
            output.muted("Machina service is not running.")
            _clear_metadata()
        raise typer.Exit(1)
    console.print("[green]Machina service is running[/green]")
    for item in clients:
        console.print(f"  PID: {_serve_pid(item)}")
        console.print(f"  Health: {_health(actual_host, actual_port)}")
    if metadata.get("log"):
        console.print(f"  Log: {metadata['log']}")


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
