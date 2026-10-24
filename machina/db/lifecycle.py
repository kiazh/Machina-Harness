"""Lifecycle for the installer-owned Docker PostgreSQL container.

The TUI, ``machina serve``, ``machina init``, and ``machina doctor`` are
clients of the same database. Each takes a small lease before starting and
releases it on shutdown. When the last live client exits, the managed ``machina-db``
container is stopped; the next client starts it again. If the container does
not exist, a client starts Docker and creates it, persists a generated
``DATABASE_URL``, and applies the schema. Native/external PostgreSQL is never
started, created, or stopped.

Set ``MACHINA_DB_MANAGE=0`` to opt out. ``auto`` (default) only manages a
loopback ``machina`` database backed by the ``machina-db`` container.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
_CONTAINER = "machina-db"
_STALE_SECONDS = 24 * 60 * 60
_DEFAULT_DB = "postgresql://postgres:postgres@127.0.0.1:5432/machina"


@dataclass(frozen=True)
class DbLease:
    path: Path
    container: str
    role: str = "tui"


def _home() -> Path:
    override = os.environ.get("MACHINA_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".machina"


def _state_dir() -> Path:
    override = os.environ.get("MACHINA_DB_STATE_DIR", "").strip()
    return Path(override).expanduser() if override else _home() / "run" / "db-clients"


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if value:
        return value
    try:
        from machina.core.config import config

        value = str(config.get("database_url") or "").strip()
    except Exception:
        value = ""
    return value or _DEFAULT_DB


def _managed_container() -> str | None:
    mode = os.environ.get("MACHINA_DB_MANAGE", "auto").strip().lower()
    if mode in ("0", "false", "no", "off"):
        return None
    if not shutil.which("docker"):
        return None
    try:
        parsed = urlparse(_database_url())
    except ValueError:
        return None
    if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        return None
    if (parsed.path or "").lstrip("/") not in {"machina", ""}:
        return None
    if parsed.port not in {None, 5432}:
        return None
    container = os.environ.get("MACHINA_DB_CONTAINER", _CONTAINER).strip() or _CONTAINER
    return container


def _docker_ready() -> bool:
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _start_docker_daemon() -> bool:
    """Best-effort start for Docker Desktop / the local Docker service."""
    if _docker_ready():
        return True
    launched = False
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Docker"
            / "Docker"
            / "Docker Desktop.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Docker" / "Docker Desktop.exe",
        ]
        for path in candidates:
            if path.is_file():
                try:
                    subprocess.Popen(
                        [str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                except OSError:
                    continue
                launched = True
                break
    elif sys.platform == "darwin":
        try:
            launched = (
                subprocess.run(
                    ["open", "-a", "Docker"], capture_output=True, timeout=10, check=False
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            launched = False
    else:
        with suppress(OSError, subprocess.TimeoutExpired):
            launched = (
                subprocess.run(
                    ["systemctl", "start", "docker"], capture_output=True, timeout=15, check=False
                ).returncode
                == 0
            )
    if not launched:
        return False
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if _docker_ready():
            return True
        time.sleep(1)
    return False


def _create_container(container: str) -> bool:
    password = secrets.token_hex(16)
    url = f"postgresql://postgres:{password}@127.0.0.1:5432/machina"
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container,
                "--restart",
                "unless-stopped",
                "-e",
                "POSTGRES_USER=postgres",
                "-e",
                f"POSTGRES_PASSWORD={password}",
                "-e",
                "POSTGRES_DB=machina",
                "-p",
                "127.0.0.1:5432:5432",
                "pgvector/pgvector:pg16",
            ],
            capture_output=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    for _ in range(60):
        ready = None
        with suppress(OSError, subprocess.TimeoutExpired):
            ready = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", "postgres", "-d", "machina"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        if ready is not None and ready.returncode == 0:
            break
        time.sleep(1)
    else:
        logger.warning("database container did not become ready")
        return False
    if not _initialize_schema(url):
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["docker", "rm", "-f", container],
                capture_output=True,
                timeout=20,
                check=False,
            )
        return False
    try:
        from machina.security.env_file import find_env_file, set_env_values

        set_env_values({"DATABASE_URL": url}, find_env_file())
    except Exception:
        # Do not advertise credentials that were not persisted: the next
        # start would use the default URL and fail.
        logger.warning("could not persist the generated database URL; removing container")
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["docker", "rm", "-f", container],
                capture_output=True,
                timeout=20,
                check=False,
            )
        return False
    os.environ["DATABASE_URL"] = url
    try:
        from machina.core.config import config

        config.set("database_url", url)
    except Exception:
        logger.debug("could not refresh in-process database URL")
    return True


def _initialize_schema(url: str) -> bool:
    """Create the schema for a freshly provisioned container."""
    try:
        import asyncio

        from machina.db.connection import db

        async def _run() -> None:
            db.dsn = url
            await db.connect()
            try:
                await db.initialize_schema()
            finally:
                await db.close()

        asyncio.run(_run())
        return True
    except Exception as error:
        logger.warning("automatic database schema initialization failed: %s", type(error).__name__)
        return False


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        return getattr(error, "errno", None) != errno.ESRCH
    return True


def live_clients() -> list[dict[str, object]]:
    """Live client leases (pid, role, path), pruning dead/stale files."""
    directory = _state_dir()
    live: list[dict[str, object]] = []
    now = time.time()
    if not directory.is_dir():
        return live
    for path in directory.glob("*.json"):
        try:
            if now - path.stat().st_mtime > _STALE_SECONDS:
                path.unlink(missing_ok=True)
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = int(data.get("pid") or 0)
            role = str(data.get("role") or "tui")
            child_pid = int(data.get("child_pid") or 0)
        except (OSError, ValueError, TypeError):
            path.unlink(missing_ok=True)
            continue
        if _alive(pid):
            live.append({"path": path, "pid": pid, "role": role, "child_pid": child_pid})
        else:
            path.unlink(missing_ok=True)
    return live


def _live_leases(directory: Path) -> list[Path]:
    return [Path(str(item["path"])) for item in live_clients()]


def acquire(role: str = "tui") -> DbLease | None:
    """Start/create the managed container if needed and take a client lease."""
    container = _managed_container()
    if container is None:
        return None
    if not _start_docker_daemon():
        return None
    try:
        probe = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if probe.returncode != 0:
            if not _create_container(container):
                return None
        elif probe.stdout.strip() != "running":
            started = subprocess.run(
                ["docker", "start", container],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if started.returncode != 0:
                return None
        directory = _state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        lease_path = directory / f"{os.getpid()}-{secrets.token_hex(6)}.json"
        lease_path.write_text(
            json.dumps({"pid": os.getpid(), "role": role, "created_at": time.time()}),
            encoding="utf-8",
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        logger.warning("database lifecycle lease failed: %s", type(error).__name__)
        return None
    return DbLease(lease_path, container, role)


def set_child_pid(lease: DbLease | None, pid: int) -> None:
    """Record the UI child PID so a global stop can signal it, not its parent."""
    if lease is None or pid <= 0:
        return
    try:
        data = json.loads(lease.path.read_text(encoding="utf-8"))
        data["child_pid"] = pid
        lease.path.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError):
        logger.debug("could not record child pid for database lease")


def release(lease: DbLease | None) -> None:
    """Drop one lease; stop the container when no other live client remains."""
    if lease is None:
        return
    try:
        lease.path.unlink(missing_ok=True)
        if _live_leases(lease.path.parent):
            return
        subprocess.run(
            ["docker", "stop", "--time", "10", lease.container],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        logger.warning("database lifecycle stop failed: %s", type(error).__name__)


def stop_if_idle() -> bool:
    """Stop the managed container only when no live Machina client remains."""
    container = _managed_container()
    if container is None or live_clients():
        return False
    try:
        probe = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if probe.returncode != 0 or probe.stdout.strip() != "running":
            return False
        result = subprocess.run(
            ["docker", "stop", "--time", "10", container],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
