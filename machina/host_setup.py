"""One-shot setup and lifecycle for the hosted Machina shell.

The setup can run from the Windows Machina client or from the WSL guest. On
Windows it installs/repairs WSL2, the Machina runtime, ttyd, Tailscale,
OpenSSH restricted to the tailnet, and a logon entry. On Linux/macOS it uses
native tmux. The gateway exposes this module through `host.*` RPC methods so the
TUI `/host` command never needs platform-specific code.
"""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from machina import host as local_host

__all__ = [
    "HostSetupError",
    "HostSetupRestartRequired",
    "HostStatus",
    "SetupOptions",
    "add_key",
    "cancel_setup",
    "client_instructions",
    "credentials",
    "open_url",
    "pending_setup",
    "plan_setup",
    "restart",
    "resume_setup",
    "setup",
    "setup_plan",
    "setup_status",
    "start",
    "start_setup",
    "status",
    "stop",
]

DEFAULT_WSL_DISTRO = "Ubuntu"
_DEFAULT_PORT = 7681
_DEFAULT_SESSION = "machina"
_TAILSCALE_EXE = r"C:\Program Files\Tailscale\tailscale.exe"


class HostSetupError(RuntimeError):
    """A host setup or lifecycle action failed with an operator-safe message."""


class HostSetupRestartRequired(HostSetupError):
    """A Windows feature needs a reboot before setup can verify itself."""


@dataclass(frozen=True)
class SetupOptions:
    workspace: str = ""
    mode: str = "ask"
    access: str = "both"
    client: str = "macos"
    public_key: str = ""
    port: int = 7681
    session: str = "machina"
    allow_full: bool = False


@dataclass
class _SetupOperation:
    id: str
    options: SetupOptions
    state: str = "running"
    step: str = "starting"
    message: str = "Starting host setup…"
    url: str = ""
    error: str = ""
    restart_required: bool = False
    log: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_operations: dict[str, _SetupOperation] = {}
_operations_lock = threading.Lock()


@dataclass(frozen=True)
class HostStatus:
    platform: str
    runtime: str
    distro: str
    configured: bool
    running: bool
    session: str
    workspace: str
    execution_mode: str
    bind: str
    port: int
    browser_url: str
    tailnet_url: str | None
    tailnet_name: str | None
    tailscale_connected: bool
    sshd_running: bool
    ssh_command: str | None
    runtime_ready: bool
    browser_ready: bool
    ssh_ready: bool
    missing: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["pending_setup"] = pending_setup() is not None
        return payload


def _run(
    command: list[str], *, timeout: float = 20.0, check: bool = False
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HostSetupError(f"command failed: {command[0]}: {error}") from None


def _is_windows() -> bool:
    return os.name == "nt" or platform.system().lower() == "windows"


def _is_wsl() -> bool:
    if _is_windows():
        return False
    try:
        version = Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return "microsoft" in version or "wsl" in version


def _wsl_distros() -> list[str]:
    wsl = shutil.which("wsl") or shutil.which("wsl.exe")
    if not wsl:
        return []
    completed = _run([wsl, "-l", "-q"], timeout=15)
    if completed.returncode != 0:
        return []
    return [
        line.replace("\x00", "").strip()
        for line in completed.stdout.splitlines()
        if line.replace("\x00", "").strip()
    ]


def _select_distro(preferred: str = DEFAULT_WSL_DISTRO) -> str:
    distros = _wsl_distros()
    usable = [candidate for candidate in distros if "docker" not in candidate.lower()]
    if preferred in usable:
        return preferred
    if usable:
        return usable[0]
    return preferred


def _wsl(distro: str, script: str, *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    wsl = shutil.which("wsl") or shutil.which("wsl.exe")
    if not wsl:
        raise HostSetupError("WSL is not available on this Windows host.")
    return _run([wsl, "-d", distro, "--", "bash", "-lc", script], timeout=timeout)


def _wsl_root(
    distro: str, script: str, *, timeout: float = 600.0
) -> subprocess.CompletedProcess[str]:
    wsl = shutil.which("wsl") or shutil.which("wsl.exe")
    if not wsl:
        raise HostSetupError("WSL is not available on this Windows host.")
    return _run(
        [wsl, "-d", distro, "-u", "root", "--", "bash", "-lc", script], timeout=timeout
    )


def _ensure_wsl_packages(distro: str) -> None:
    """Install curl and tmux as WSL root, without needing a sudo password."""
    checked = _wsl_root(
        distro,
        "command -v curl >/dev/null 2>&1 && command -v tmux >/dev/null 2>&1",
        timeout=30,
    )
    if checked.returncode == 0:
        return
    completed = _wsl_root(
        distro, "apt-get update -y >/dev/null && apt-get install -y curl tmux", timeout=600
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise HostSetupError(
            "Could not install curl and tmux inside WSL." + (f" {detail}" if detail else "")
        )


def _wsl_user(distro: str) -> str:
    completed = _wsl(distro, "whoami", timeout=15)
    user = completed.stdout.strip()
    if completed.returncode != 0 or not user:
        raise HostSetupError(f"could not determine the WSL user in {distro}.")
    return user


@lru_cache(maxsize=4)
def _wsl_home(distro: str) -> str:
    completed = _wsl(distro, 'printf "%s" "$HOME"', timeout=15)
    home = completed.stdout.strip()
    if completed.returncode == 0 and home:
        return home
    return f"/home/{_wsl_user(distro)}"


def _read_state(distro: str, user: str) -> dict[str, Any] | None:
    del user
    path = f"{_wsl_home(distro)}/.machina/host.json"
    completed = _wsl(distro, f"cat {shlex.quote(path)} 2>/dev/null", timeout=15)
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        data = json.loads(completed.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _wsl_runtime_ready(distro: str, user: str) -> tuple[bool, bool]:
    """Return (machina_runtime, ttyd) readiness in the WSL distribution."""
    del user
    home = _wsl_home(distro)
    venv = f"{home}/.machina-host/venv/bin/machina"
    ttyd = f"{home}/.local/bin/ttyd"
    completed = _wsl(
        distro,
        f"test -x {shlex.quote(venv)} && echo runtime; test -x {shlex.quote(ttyd)} && echo ttyd",
        timeout=15,
    )
    lines = set(completed.stdout.split())
    return "runtime" in lines, "ttyd" in lines


def _wsl_session_alive(distro: str, session: str, pid: int) -> bool:
    script = f"tmux has-session -t {shlex.quote(session)} >/dev/null 2>&1 && kill -0 {int(pid)} >/dev/null 2>&1"
    completed = _wsl(distro, script, timeout=15)
    return completed.returncode == 0


def _tailscale_exe() -> str | None:
    if _is_windows():
        if Path(_TAILSCALE_EXE).is_file():
            return _TAILSCALE_EXE
    if _is_wsl() and Path("/mnt/c/Program Files/Tailscale/tailscale.exe").is_file():
        return "/mnt/c/Program Files/Tailscale/tailscale.exe"
    return shutil.which("tailscale")


def _tailscale_status() -> tuple[bool, str | None, str | None]:
    """Return (connected, dns_name, serve_url)."""
    executable = _tailscale_exe()
    if not executable:
        return False, None, None
    try:
        completed = _run([executable, "status", "--json"], timeout=15)
    except HostSetupError:
        return False, None, None
    if completed.returncode != 0:
        return False, None, None
    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        return False, None, None
    if not isinstance(payload, dict):
        return False, None, None
    self_info = payload.get("Self") if isinstance(payload.get("Self"), dict) else {}
    dns_name = str(self_info.get("DNSName") or "").strip().rstrip(".")
    backend = str(payload.get("BackendState") or "")
    connected = backend in {"Running", "NeedsMachineAuth"} and bool(dns_name)
    serve_url: str | None = None
    try:
        serve = _run([executable, "serve", "status"], timeout=15)
    except HostSetupError:
        serve = None
    if serve is not None and serve.returncode == 0:
        for line in serve.stdout.splitlines():
            line = line.strip()
            if line.startswith("https://"):
                serve_url = line.split()[0].rstrip("/")
                break
    return connected, dns_name or None, serve_url


def _sshd_running() -> bool:
    if _is_windows() or _is_wsl():
        powershell = "powershell.exe"
        if _is_wsl():
            powershell = (
                shutil.which("powershell.exe")
                or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            )
            if not Path(powershell).is_file():
                return False
        try:
            completed = _run(
                [powershell, "-NoProfile", "-Command", "(Get-Service sshd).Status"],
                timeout=15,
            )
        except HostSetupError:
            return False
        return completed.returncode == 0 and completed.stdout.strip().lower() == "running"
    if shutil.which("systemctl"):
        try:
            completed = _run(["systemctl", "is-active", "sshd"], timeout=10)
        except HostSetupError:
            return False
        return completed.returncode == 0 and completed.stdout.strip() == "active"
    return False


@lru_cache(maxsize=1)
def _windows_user() -> str:
    if _is_windows():
        return os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    if _is_wsl():
        try:
            completed = _run(["cmd.exe", "/c", "echo %USERNAME%"], timeout=10)
            name = completed.stdout.strip()
            if completed.returncode == 0 and name:
                return name
        except HostSetupError:
            pass
    return os.environ.get("USER") or "user"


def _host_name() -> str:
    return os.environ.get("COMPUTERNAME") or platform.node() or "host"


def _ssh_command(status: HostStatus) -> str | None:
    if not status.sshd_running:
        return None
    target = status.tailnet_name or _host_name()
    if status.runtime == "wsl":
        return (
            f"ssh -i ~/.ssh/id_ed25519 {_windows_user()}@{target} -t "
            f'"wsl -d {status.distro} -- tmux attach -t {status.session}"'
        )
    return f"ssh {os.environ.get('USER') or 'user'}@{target} -t 'tmux attach -t {status.session}'"


def _status_native() -> HostStatus:
    state, tmux_alive, ttyd_alive = local_host.status_host()
    connected, dns_name, serve_url = _tailscale_status()
    sshd = _sshd_running()
    session = state.session if state else _DEFAULT_SESSION
    workspace = state.workspace if state else str(Path.cwd())
    mode = state.mode if state else local_host.DEFAULT_MODE
    bind = state.bind if state else local_host.DEFAULT_BIND
    port = state.port if state else _DEFAULT_PORT
    running = bool(state and tmux_alive and ttyd_alive)
    local_url = f"http://127.0.0.1:{port}"
    status = HostStatus(
        platform=platform.system().lower() or "posix",
        runtime="native",
        distro="",
        configured=state is not None,
        running=running,
        session=session,
        workspace=workspace,
        execution_mode=mode,
        bind=bind,
        port=port,
        browser_url=serve_url or local_url,
        tailnet_url=serve_url,
        tailnet_name=dns_name,
        tailscale_connected=connected,
        sshd_running=sshd,
        ssh_command=None,
        runtime_ready=state is not None,
        browser_ready=running,
        ssh_ready=sshd and running,
        missing=[],
    )
    missing: list[str] = []
    if state is None:
        missing.append("host runtime")
    if not connected:
        missing.append("Tailscale")
    if not sshd:
        missing.append("SSH server")
    return HostStatus(**{**asdict(status), "ssh_command": _ssh_command(status), "missing": missing})


def _status_wsl_guest() -> HostStatus:
    """Status from inside the WSL distribution that owns the host shell."""
    state, tmux_alive, ttyd_alive = local_host.status_host()
    connected, dns_name, serve_url = _tailscale_status()
    sshd = _sshd_running()
    session = state.session if state else _DEFAULT_SESSION
    workspace = state.workspace if state else str(Path.cwd())
    mode = state.mode if state else local_host.DEFAULT_MODE
    bind = state.bind if state else local_host.DEFAULT_BIND
    port = state.port if state else _DEFAULT_PORT
    running = bool(state and tmux_alive and ttyd_alive)
    local_url = f"http://127.0.0.1:{port}"
    status = HostStatus(
        platform="windows",
        runtime="wsl",
        distro=os.environ.get("WSL_DISTRO_NAME") or DEFAULT_WSL_DISTRO,
        configured=state is not None,
        running=running,
        session=session,
        workspace=workspace,
        execution_mode=mode,
        bind=bind,
        port=port,
        browser_url=serve_url or local_url,
        tailnet_url=serve_url,
        tailnet_name=dns_name,
        tailscale_connected=connected,
        sshd_running=sshd,
        ssh_command=None,
        runtime_ready=state is not None,
        browser_ready=running,
        ssh_ready=sshd and running,
        missing=[],
    )
    missing: list[str] = []
    if state is None:
        missing.append("host runtime")
    if not connected:
        missing.append("Tailscale")
    if not sshd:
        missing.append("SSH server")
    return HostStatus(**{**asdict(status), "ssh_command": _ssh_command(status), "missing": missing})


def status() -> HostStatus:
    """Inspect the current host without changing anything."""
    if _is_windows():
        distro = _select_distro()
        try:
            user = _wsl_user(distro)
        except HostSetupError:
            return HostStatus(
                platform="windows",
                runtime="wsl",
                distro=distro,
                configured=False,
                running=False,
                session=_DEFAULT_SESSION,
                workspace=str(Path.home()),
                execution_mode=local_host.DEFAULT_MODE,
                bind=local_host.DEFAULT_BIND,
                port=_DEFAULT_PORT,
                browser_url=f"http://127.0.0.1:{_DEFAULT_PORT}",
                tailnet_url=None,
                tailnet_name=None,
                tailscale_connected=False,
                sshd_running=False,
                ssh_command=None,
                runtime_ready=False,
                browser_ready=False,
                ssh_ready=False,
                missing=["WSL runtime", "ttyd", "Tailscale", "SSH server"],
            )
        state = _read_state(distro, user)
        runtime_ready, ttyd_ready = _wsl_runtime_ready(distro, user)
        running = bool(
            state
            and ttyd_ready
            and _wsl_session_alive(
                distro,
                str(state.get("session") or _DEFAULT_SESSION),
                int(state.get("ttyd_pid") or 0),
            )
        )
        connected, dns_name, serve_url = _tailscale_status()
        sshd = _sshd_running()
        session = str(state.get("session") or _DEFAULT_SESSION) if state else _DEFAULT_SESSION
        workspace = str(state.get("workspace") or "") if state else ""
        mode = (
            str(state.get("mode") or local_host.DEFAULT_MODE) if state else local_host.DEFAULT_MODE
        )
        bind = (
            str(state.get("bind") or local_host.DEFAULT_BIND) if state else local_host.DEFAULT_BIND
        )
        port = int(state.get("port") or _DEFAULT_PORT) if state else _DEFAULT_PORT
        local_url = f"http://127.0.0.1:{port}"
        status = HostStatus(
            platform="windows",
            runtime="wsl",
            distro=distro,
            configured=state is not None,
            running=running,
            session=session,
            workspace=workspace,
            execution_mode=mode,
            bind=bind,
            port=port,
            browser_url=serve_url or local_url,
            tailnet_url=serve_url,
            tailnet_name=dns_name,
            tailscale_connected=connected,
            sshd_running=sshd,
            ssh_command=None,
            runtime_ready=runtime_ready and ttyd_ready,
            browser_ready=running,
            ssh_ready=sshd and running,
            missing=[],
        )
        missing: list[str] = []
        if not runtime_ready:
            missing.append("WSL Machina runtime")
        if not ttyd_ready:
            missing.append("ttyd")
        if not connected:
            missing.append("Tailscale")
        if not sshd:
            missing.append("SSH server")
        if state is None:
            missing.append("host state")
        return HostStatus(
            **{**asdict(status), "ssh_command": _ssh_command(status), "missing": missing}
        )
    if _is_wsl():
        return _status_wsl_guest()
    return _status_native()


def credentials() -> dict[str, str]:
    """Return the browser credential for an explicit /host action."""
    if _is_windows():
        distro = _select_distro()
        try:
            user = _wsl_user(distro)
        except HostSetupError:
            raise HostSetupError("WSL is not ready on this host.") from None
        state = _read_state(distro, user)
    else:
        state = (
            json.loads(local_host.state_path().read_text(encoding="utf-8"))
            if local_host.state_path().is_file()
            else None
        )
    if not state:
        raise HostSetupError("Machina host is not configured yet.")
    username = str(state.get("username") or "")
    password = str(state.get("password") or "")
    if not username or not password:
        raise HostSetupError("The host credential is unavailable.")
    return {"username": username, "password": password}


def _run_script(distro: str, script: str, *, timeout: float = 60.0) -> None:
    if _is_wsl():
        completed = _run(["bash", "-lc", script], timeout=timeout)
    else:
        completed = _wsl(distro, script, timeout=timeout)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise HostSetupError(detail or f"host command failed in {distro}.")


def start() -> HostStatus:
    """Start an already-installed host."""
    current = status()
    if current.running:
        return current
    if current.runtime == "wsl":
        _run_script(
            current.distro,
            "$HOME/.machina-host/start.sh",
            timeout=120,
        )
    else:
        local_host.start_host(
            workspace=Path(current.workspace or Path.cwd()),
            mode=current.execution_mode,
            session=current.session,
        )
    return status()


def stop() -> HostStatus:
    """Stop the host shell and browser bridge."""
    current = status()
    if not current.running:
        return current
    if current.runtime == "wsl":
        _run_script(
            current.distro,
            "$HOME/.machina-host/stop.sh",
            timeout=60,
        )
    else:
        local_host.stop_host()
    return status()


def restart() -> HostStatus:
    stop()
    return start()


def client_instructions(client: str, access: str = "both") -> str:
    """Plain instructions for one client platform and one access mode."""
    current = status()
    client = client.strip().lower()
    access = access.strip().lower()
    if access not in {"both", "browser", "ssh"}:
        access = "both"
    lines = [
        f"Host: {current.platform} ({current.runtime})",
        f"Status: {'running' if current.running else 'stopped'}",
        "",
    ]
    if access == "browser":
        mac_step = "Open the browser URL below."
        windows_step = "Open the browser URL below."
        linux_step = "Open the browser URL below."
    elif access == "ssh":
        mac_step = "Run the SSH command below in Terminal."
        windows_step = "Run the SSH command below in PowerShell."
        linux_step = "Run the SSH command below in your terminal."
    else:
        mac_step = "Open the browser URL below, or run the SSH command in Terminal."
        windows_step = "Open the browser URL below, or run the SSH command in PowerShell."
        linux_step = "Open the browser URL below, or run the SSH command in your terminal."
    if client in {"macos", "mac"}:
        lines.extend(
            [
                "On the Mac:",
                "1. Install Tailscale from https://tailscale.com/download/mac and sign in",
                "   with the same account as the host.",
                f"2. {mac_step}",
                "",
            ]
        )
    elif client == "windows":
        lines.extend(
            [
                "On Windows:",
                "1. Install Tailscale from https://tailscale.com/download/windows and sign in",
                "   with the same account as the host.",
                f"2. {windows_step}",
                "",
            ]
        )
    elif client == "linux":
        lines.extend(
            [
                "On Linux:",
                "1. Install Tailscale: curl -fsSL https://tailscale.com/install.sh | sh",
                "2. Sign in: sudo tailscale up",
                f"3. {linux_step}",
                "",
            ]
        )
    if access in {"both", "browser"} and current.browser_url:
        lines.append(f"Browser: {current.browser_url}")
        lines.append("Use the username and password from `/host credentials`.")
        lines.append("")
    if access in {"both", "ssh"} and current.ssh_command:
        lines.append("SSH:")
        lines.append(current.ssh_command)
        lines.append("")
        lines.append("If this device has no key yet, create one with:")
        lines.append('  ssh-keygen -t ed25519 -C "machina-client"')
        lines.append("Then run `/host add-key` on the host and paste the public key.")
        lines.append("")
    missing = list(current.missing)
    if access == "browser":
        missing = [item for item in missing if item != "SSH server"]
    if access == "ssh":
        missing = [item for item in missing if item not in {"browser URL", "Tailscale Serve"}]
    if not missing:
        lines.append("Everything needed is configured.")
    else:
        lines.append("Not configured: " + ", ".join(missing))
    return "\n".join(lines)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _wsl_path(path: Path) -> str:
    """Map a Windows path to its WSL mount path; pass POSIX paths through."""
    text = str(path)
    if len(text) >= 3 and text[1] == ":":
        drive = text[0].lower()
        rest = text[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"
    return text.replace("\\", "/")


def _env_file_wsl() -> str:
    try:
        from machina.security.env_file import find_env_file

        return _wsl_path(find_env_file())
    except Exception:
        return _wsl_path(_repo_root() / ".env")


def _setup_windows(
    distro: str,
    workspace: str,
    mode: str,
    port: int = _DEFAULT_PORT,
    session: str = _DEFAULT_SESSION,
    allow_full: bool = False,
) -> None:
    repo = shlex.quote(_wsl_path(_repo_root()))
    workspace_wsl = shlex.quote(_wsl_path(Path(workspace).expanduser()))
    env_file = shlex.quote(_env_file_wsl())
    allow_flag = " --allow-full" if allow_full and mode == "full" else ""
    script = f"""
set -euo pipefail
mkdir -p "$HOME/.local/bin" "$HOME/.machina-host"
if ! command -v curl >/dev/null 2>&1 || ! command -v tmux >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null; then
    sudo -n apt-get update -y >/dev/null 2>&1 || true
    command -v curl >/dev/null 2>&1 || sudo -n apt-get install -y curl >/dev/null 2>&1 || true
    command -v tmux >/dev/null 2>&1 || sudo -n apt-get install -y tmux >/dev/null 2>&1 || true
  fi
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required inside WSL. Run: sudo apt-get update && sudo apt-get install -y curl" >&2
  exit 1
fi
if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required inside WSL. Run: sudo apt-get update && sudo apt-get install -y tmux" >&2
  exit 1
fi
if [ ! -x "$HOME/.local/bin/ttyd" ]; then
  case "$(uname -m)" in
    x86_64|amd64) TTYD_ARCH=ttyd.x86_64 ;;
    aarch64|arm64) TTYD_ARCH=ttyd.aarch64 ;;
    *) echo "Unsupported architecture for ttyd: $(uname -m)" >&2; exit 1 ;;
  esac
  curl -fsSL -o "$HOME/.local/bin/ttyd" "https://github.com/tsl0922/ttyd/releases/download/1.7.7/$TTYD_ARCH"
  chmod +x "$HOME/.local/bin/ttyd"
fi
if [ ! -x "$HOME/.machina-host/venv/bin/machina" ]; then
  UV="$HOME/.local/bin/uv"
  if [ ! -x "$UV" ]; then
    if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"; else
      curl -LsSf https://astral.sh/uv/install.sh | sh
      UV="$HOME/.local/bin/uv"
    fi
  fi
  "$UV" venv "$HOME/.machina-host/venv" --python 3.12
  "$UV" pip install --python "$HOME/.machina-host/venv/bin/python" -e {repo}
fi
cat > "$HOME/.machina-host/start.sh" <<'START'
#!/usr/bin/env bash
set -euo pipefail
export MACHINA_ENV_FILE={env_file}
export MACHINA_DB_MANAGE=0
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
exec "$HOME/.machina-host/venv/bin/machina" host start --workspace {workspace_wsl} --mode {shlex.quote(mode)} --port {int(port)} --session {shlex.quote(session)}{allow_flag} "$@"
START
cat > "$HOME/.machina-host/status.sh" <<'STATUS'
#!/usr/bin/env bash
exec "$HOME/.machina-host/venv/bin/machina" host status "$@"
STATUS
cat > "$HOME/.machina-host/stop.sh" <<'STOP'
#!/usr/bin/env bash
exec "$HOME/.machina-host/venv/bin/machina" host stop "$@"
STOP
chmod +x "$HOME/.machina-host/start.sh" "$HOME/.machina-host/status.sh" "$HOME/.machina-host/stop.sh"
"""
    _run_script(distro, script, timeout=600)
    _run_script(distro, "$HOME/.machina-host/start.sh", timeout=180)


def _powershell_exe() -> str | None:
    if _is_windows():
        return "powershell.exe"
    if _is_wsl():
        return (
            shutil.which("powershell.exe")
            or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        )
    return None


def _windows_temp_dir() -> Path:
    if _is_windows():
        return Path(tempfile.gettempdir())
    return Path("/mnt/c/Users") / _windows_user() / "AppData/Local/Temp"


def _ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _powershell_path(path: Path) -> str:
    """Translate a WSL /mnt/c path into the Windows path PowerShell expects."""
    text = str(path)
    if text.startswith("/mnt/") and len(text) > 7:
        drive = text[5].upper()
        return f"{drive}:\\" + text[7:].replace("/", "\\")
    return text


def _run_elevated_powershell(payload: str, *, timeout: float = 300.0) -> None:
    """Run one PowerShell payload elevated through the Windows UAC prompt."""
    powershell = _powershell_exe()
    if not powershell or (_is_wsl() and not Path(powershell).is_file()):
        raise HostSetupError("Windows PowerShell is unavailable for this setup step.")
    tempdir = _windows_temp_dir()
    tempdir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(6)
    payload_path = tempdir / f"machina-host-{token}.ps1"
    log_path = tempdir / f"machina-host-{token}.log"
    launcher_path = tempdir / f"machina-host-{token}-launch.ps1"
    wrapped = f"""$ErrorActionPreference = 'Stop'
try {{
{payload}
}} catch {{
  $_ | Out-String | Set-Content -Path {_ps_quote(_powershell_path(log_path))}
  exit 1
}}
"""
    payload_path.write_text(wrapped, encoding="utf-8")
    launcher_path.write_text(
        f"""$ErrorActionPreference = 'Stop'
$p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList @(
  '-NoProfile','-ExecutionPolicy','Bypass','-File',{_ps_quote(_powershell_path(payload_path))}
)
exit $p.ExitCode
""",
        encoding="utf-8",
    )
    try:
        completed = _run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                _powershell_path(launcher_path),
            ],
            timeout=timeout,
        )
        if completed.returncode != 0:
            detail = ""
            try:
                detail = log_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                detail = (completed.stderr or completed.stdout or "").strip()
            raise HostSetupError(detail or "The Windows administrator step failed.")
    except subprocess.TimeoutExpired:
        raise HostSetupError("The Windows administrator prompt timed out.") from None
    finally:
        for path in (payload_path, launcher_path, log_path):
            try:
                path.unlink()
            except OSError:
                pass


def _open_url(url: str) -> bool:
    if not url.startswith(("https://", "http://")):
        return False
    try:
        if _is_windows():
            return bool(webbrowser.open(url))
        powershell = _powershell_exe()
        if powershell:
            completed = _run(
                [powershell, "-NoProfile", "-Command", f"Start-Process {_ps_quote(url)}"],
                timeout=15,
            )
            return completed.returncode == 0
        return bool(webbrowser.open(url))
    except Exception:
        return False


def _capture(command: list[str], timeout: float) -> tuple[int, str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
        return process.returncode or 0, output or ""
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
        return process.returncode or -1, output or ""


def _download(url: str, destination: Path, *, timeout: float = 120.0) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Machina-Host-Setup"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        destination.write_bytes(response.read())


def _is_windows_admin() -> bool:
    if not (_is_windows() or _is_wsl()):
        return False
    whoami = "whoami.exe" if _is_windows() else "/mnt/c/Windows/System32/whoami.exe"
    try:
        completed = _run([whoami, "/groups"], timeout=15)
    except HostSetupError:
        return False
    return "S-1-5-32-544" in completed.stdout


def _tailscale_ready() -> bool:
    return _tailscale_status()[0]


def _tailscale_service_running() -> bool:
    if not (_is_windows() or _is_wsl()):
        return True
    powershell = _powershell_exe()
    if not powershell:
        return False
    try:
        completed = _run(
            [powershell, "-NoProfile", "-Command", "(Get-Service Tailscale -ErrorAction SilentlyContinue).Status"],
            timeout=15,
        )
    except HostSetupError:
        return False
    return completed.returncode == 0 and completed.stdout.strip().lower() == "running"


def _ensure_tailscale_service(op: _SetupOperation) -> None:
    if _tailscale_service_running():
        return
    _step(op, "tailscale", "Starting the Tailscale service…")
    _run_elevated_powershell(
        "Start-Service -Name Tailscale -ErrorAction Stop", timeout=120
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _tailscale_service_running():
            return
        if op.cancelled.is_set():
            raise HostSetupError("Tailscale service start was cancelled.")
        time.sleep(1)
    raise HostSetupError("The Tailscale service did not start.")


def _install_tailscale(op: _SetupOperation) -> None:
    if _tailscale_exe():
        return
    if not (_is_windows() or _is_wsl()):
        raise HostSetupError(
            "Tailscale is not installed. Install it from https://tailscale.com/download, "
            "then run host setup again."
        )
    _step(op, "tailscale", "Downloading the Tailscale installer…")
    installer = _windows_temp_dir() / "tailscale-setup-latest.exe"
    try:
        installer.parent.mkdir(parents=True, exist_ok=True)
        _download("https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe", installer)
    except Exception as error:
        try:
            installer.unlink()
        except OSError:
            pass
        raise HostSetupError(f"Could not download the Tailscale installer: {error}") from None
    _step(op, "tailscale", "Approve the Windows administrator prompt to install Tailscale…")
    try:
        _run_elevated_powershell(
            f"""Start-Process -FilePath {_ps_quote(_powershell_path(installer))} -ArgumentList `
  '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait
Start-Service -Name Tailscale -ErrorAction SilentlyContinue
""",
            timeout=300,
        )
    finally:
        try:
            installer.unlink()
        except OSError:
            pass
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not _tailscale_exe():
        if op.cancelled.is_set():
            raise HostSetupError("Tailscale installation was cancelled.")
        time.sleep(1)
    if not _tailscale_exe():
        raise HostSetupError("Tailscale was installed but its command was not found.")


def _ensure_tailscale(op: _SetupOperation) -> None:
    _step(op, "tailscale", "Checking Tailscale…")
    if _tailscale_ready():
        return
    executable = _tailscale_exe()
    if not executable:
        _install_tailscale(op)
        executable = _tailscale_exe()
    if not executable:
        raise HostSetupError("Tailscale is not installed or its command is unavailable.")
    _ensure_tailscale_service(op)
    _step(op, "tailscale", "Opening the Tailscale sign-in page…")
    _code, output = _capture([executable, "up"], timeout=20)
    match = re.search(r"https://login\.tailscale\.com/[^\s]+", output)
    if match:
        op.url = match.group(0)
        _open_url(op.url)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if op.cancelled.is_set():
            raise HostSetupError("Tailscale sign-in was cancelled.")
        connected, _dns, _url = _tailscale_status()
        if connected:
            _step(op, "tailscale", "Tailscale is connected.")
            return
        time.sleep(1)
    raise HostSetupError(
        "Tailscale sign-in did not complete. Open https://login.tailscale.com/ and sign in, "
        "then run host setup again."
    )


def _ensure_serve(op: _SetupOperation, port: int) -> None:
    _step(op, "serve", "Checking Tailscale Serve…")
    _connected, _dns, serve_url = _tailscale_status()
    if serve_url:
        return
    executable = _tailscale_exe()
    if not executable:
        raise HostSetupError("Tailscale Serve needs Tailscale installed and signed in.")
    _step(op, "serve", "Opening the Tailscale Serve setup page…")
    _code, output = _capture([executable, "serve", "--bg", str(port)], timeout=30)
    match = re.search(r"https://login\.tailscale\.com/[^\s)]+", output)
    if match:
        op.url = match.group(0)
        _open_url(op.url)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if op.cancelled.is_set():
            raise HostSetupError("Tailscale Serve setup was cancelled.")
        _connected, _dns, serve_url = _tailscale_status()
        if serve_url:
            _step(op, "serve", "Tailscale Serve is active.")
            return
        time.sleep(1)
    raise HostSetupError(
        "Tailscale Serve was not enabled. Open the Tailscale Serve page and enable it, "
        "then run host setup again."
    )


def _ssh_firewall_ready() -> bool:
    powershell = _powershell_exe()
    if not powershell:
        return False
    script = (
        "$sshd = (Get-Service sshd -ErrorAction SilentlyContinue).Status; "
        "$broad = (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' "
        "-ErrorAction SilentlyContinue).Enabled; "
        "$other = @(Get-NetFirewallRule -DisplayName 'OpenSSH' -ErrorAction SilentlyContinue "
        "| Where-Object { $_.Enabled -eq 'True' }).Count; "
        "$tail = (Get-NetFirewallRule -Name 'sshd-tailscale' "
        "-ErrorAction SilentlyContinue).Enabled; "
        "$ts = @(Get-NetFirewallRule -DisplayName 'Tailscale-In*' "
        "-ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' }).Count; "
        "if ($sshd -eq 'Running' -and $broad -ne 'True' -and $other -eq 0 "
        "-and ($tail -eq 'True' -or $ts -gt 0)) "
        "{ 'ready' } else { 'not-ready' }"
    )
    try:
        completed = _run([powershell, "-NoProfile", "-Command", script], timeout=20)
    except HostSetupError:
        return False
    return completed.returncode == 0 and "ready" in completed.stdout.split()


def _ensure_ssh(op: _SetupOperation) -> None:
    _step(op, "ssh", "Checking the SSH server…")
    if not (_is_windows() or _is_wsl()):
        if _sshd_running():
            return
        raise HostSetupError(
            "SSH is not running. Enable openssh-server with your package manager, "
            "then run host setup again."
        )
    if _ssh_firewall_ready():
        return
    _step(op, "ssh", "Approve the Windows administrator prompt to enable OpenSSH…")
    _run_elevated_powershell(
        """$cap = Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
if ($cap.State -ne 'Installed') {
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
}
Set-Service -Name sshd -StartupType Automatic
Start-Service -Name sshd
Remove-NetFirewallRule -Name 'sshd-tailscale' -ErrorAction SilentlyContinue
New-NetFirewallRule -Name 'sshd-tailscale' -DisplayName 'OpenSSH Server (Tailscale)' `
  -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 `
  -RemoteAddress 100.64.0.0/10 -Profile Any | Out-Null
Set-NetFirewallRule -Name 'sshd-tailscale' -Enabled True -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName 'OpenSSH' -ErrorAction SilentlyContinue |
  Set-NetFirewallRule -Enabled False -ErrorAction SilentlyContinue
Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Enabled False -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName 'OpenSSH SSH Server (sshd)' -ErrorAction SilentlyContinue |
  Set-NetFirewallRule -Enabled False -ErrorAction SilentlyContinue
Set-NetFirewallRule -Name 'sshd' -Enabled False -ErrorAction SilentlyContinue
""",
        timeout=300,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if op.cancelled.is_set():
            raise HostSetupError("SSH setup was cancelled.")
        if _sshd_running():
            _step(op, "ssh", "Windows OpenSSH Server is running.")
            return
        time.sleep(1)
    raise HostSetupRestartRequired(
        "Windows enabled OpenSSH Server but it is not running yet. Restart Windows, then run /host setup again."
    )


def _install_ssh_key(op: _SetupOperation, public_key: str) -> None:
    key = public_key.strip()
    if not key:
        return
    if "\n" in key or "\r" in key or len(key) > 4096:
        raise HostSetupError("The SSH public key must be one line.")
    if not re.match(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+) [A-Za-z0-9+/=]+(?: .*)?$", key):
        raise HostSetupError("That does not look like an SSH public key.")
    _step(op, "key", "Installing the client SSH key…")
    if _is_windows() or _is_wsl():
        key_file = _windows_temp_dir() / f"machina-host-{secrets.token_hex(4)}.pub"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key, encoding="utf-8")
        try:
            if _is_windows_admin():
                _run_elevated_powershell(
                    f"""$path = 'C:\\ProgramData\\ssh\\administrators_authorized_keys'
New-Item -ItemType Directory -Force -Path (Split-Path $path) | Out-Null
if (-not (Test-Path $path)) {{ New-Item -ItemType File -Path $path | Out-Null }}
$key = (Get-Content -Raw -Path {_ps_quote(_powershell_path(key_file))}).Trim()
$added = $false
if (-not (Select-String -Path $path -SimpleMatch $key -Quiet)) {{
  Add-Content -Path $path -Value $key
  $added = $true
}}
icacls $path /inheritance:r | Out-Null
icacls $path /grant '*S-1-5-32-544:F' | Out-Null
icacls $path /grant '*S-1-5-18:F' | Out-Null
if ($added) {{ Restart-Service -Name sshd -ErrorAction SilentlyContinue }}
""",
                    timeout=180,
                )
            else:
                target = Path("/mnt/c/Users") / _windows_user() / ".ssh/authorized_keys"
                if _is_windows():
                    target = Path.home() / ".ssh/authorized_keys"
                target.parent.mkdir(parents=True, exist_ok=True)
                existing = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
                if key not in existing:
                    with target.open("a", encoding="utf-8") as handle:
                        if existing and not existing.endswith("\n"):
                            handle.write("\n")
                        handle.write(key + "\n")
        finally:
            try:
                key_file.unlink()
            except OSError:
                pass
    else:
        target = Path.home() / ".ssh/authorized_keys"
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        if key not in existing:
            with target.open("a", encoding="utf-8") as handle:
                if existing and not existing.endswith("\n"):
                    handle.write("\n")
                handle.write(key + "\n")
            target.chmod(0o600)


def _startup_path() -> Path | None:
    if _is_windows():
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        base = Path(appdata)
    elif _is_wsl():
        appdata = ""
        try:
            completed = _run(["cmd.exe", "/c", "echo %APPDATA%"], timeout=10)
            candidate = completed.stdout.strip()
            if completed.returncode == 0 and candidate:
                appdata = _wsl_path(Path(candidate))
        except HostSetupError:
            appdata = ""
        base = Path(appdata) if appdata else (
            Path("/mnt/c/Users") / _windows_user() / "AppData/Roaming"
        )
    else:
        return None
    return base / "Microsoft/Windows/Start Menu/Programs/Startup/Machina Host.vbs"


def _configure_autostart(op: _SetupOperation) -> None:
    target = _startup_path()
    if target is None:
        return
    distro = _select_distro() if _is_windows() else (os.environ.get("WSL_DISTRO_NAME") or DEFAULT_WSL_DISTRO)
    if _is_windows():
        home = _wsl_home(distro)
    else:
        home = os.environ.get("HOME") or f"/home/{os.environ.get('USER') or 'user'}"
    command = f'wsl.exe -d "{distro}" -- "{home}/.machina-host/start.sh"'
    escaped = command.replace('"', '""')
    content = (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.Run "{escaped}", 0, False\r\n'
    )
    try:
        if target.is_file() and target.read_text(encoding="utf-8", errors="replace") == content:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _step(op, "autostart", "Configured the host to start at Windows logon.")
    except OSError as error:
        raise HostSetupError(f"Could not create the startup entry: {error}") from None


def _autostart_ready() -> bool:
    target = _startup_path()
    if not target or not target.is_file():
        return False
    try:
        return "machina-host/start.sh" in target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _shared_home() -> Path:
    """Return the host user's home as seen by both Windows and WSL.

    The setup may be started from either side, but the resume marker has to
    survive a Windows reboot and be visible to the WSL runtime that starts at
    logon. Both paths point at the same file on disk.
    """
    if _is_windows():
        return Path(os.environ.get("USERPROFILE") or Path.home())
    if _is_wsl():
        return Path("/mnt/c/Users") / _windows_user()
    return Path.home()


def pending_setup_path() -> Path:
    return _shared_home() / ".machina" / "host-setup.json"


def _save_pending(options: SetupOptions, reason: str) -> None:
    path = pending_setup_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "reason": reason,
                    "options": asdict(options),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    except OSError:
        # The setup itself already reported the restart; losing the marker only
        # costs the automatic resume prompt.
        pass


def _clear_pending() -> None:
    try:
        pending_setup_path().unlink()
    except OSError:
        pass


def pending_setup() -> dict[str, Any] | None:
    """Return the setup waiting for a Windows restart, if any."""
    path = pending_setup_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("options"), dict):
        return None
    return payload


def resume_setup() -> str:
    """Start the saved restart-interrupted setup again and return its operation id."""
    pending = pending_setup()
    if pending is None:
        raise HostSetupError("No host setup is waiting to resume.")
    try:
        options = SetupOptions(**pending["options"])
    except (TypeError, ValueError):
        raise HostSetupError("The saved host setup is not valid. Run /host setup again.") from None
    return start_setup(options)


def add_key(public_key: str) -> HostStatus:
    """Install one client SSH public key without rerunning the whole setup."""
    if not public_key.strip():
        raise HostSetupError("Paste an SSH public key first.")
    operation = _SetupOperation(id=secrets.token_hex(8), options=SetupOptions())
    _install_ssh_key(operation, public_key)
    return status()



def _step(op: _SetupOperation, step: str, message: str, url: str = "") -> None:
    with op.lock:
        op.step = step
        op.message = message
        op.url = url
        op.log.append(f"{step}: {message}")
        op.log = op.log[-40:]


def _cancel_requested(op: _SetupOperation) -> bool:
    return op.cancelled.is_set()


def _ensure_wsl(op: _SetupOperation) -> None:
    """Install WSL when the Windows host has no usable distribution yet."""
    if any("docker" not in distro.lower() for distro in _wsl_distros()):
        return
    _step(op, "wsl", "Approve the Windows administrator prompt to install WSL…")
    _run_elevated_powershell(
        """wsl.exe --install -d Ubuntu --no-launch
if ($LASTEXITCODE -ne 0) { throw "wsl --install failed with exit code $LASTEXITCODE" }
""",
        timeout=900,
    )
    raise HostSetupRestartRequired(
        "Windows installed WSL. Restart Windows, open Ubuntu once to finish creating your "
        "Linux user, then run /host and choose Resume setup."
    )


def _execute_setup(op: _SetupOperation) -> None:
    options = op.options
    try:
        if options.mode not in local_host.VALID_MODES:
            raise HostSetupError(f"mode must be one of: {', '.join(sorted(local_host.VALID_MODES))}.")
        if options.mode == "full" and not options.allow_full:
            raise HostSetupError("Full host mode is refused for remote access.")
        if options.access not in {"both", "browser", "ssh"}:
            raise HostSetupError("access must be both, browser, or ssh.")
        if not re.match(r"^[A-Za-z0-9_-]{1,64}$", options.session):
            raise HostSetupError(
                "session must contain only letters, numbers, dashes, and underscores."
            )
        if not 1 <= int(options.port) <= 65535:
            raise HostSetupError("port must be between 1 and 65535.")
        target = options.workspace.strip() or str(Path.home())
        target_path = Path(target).expanduser()
        if _is_windows():
            target_path = Path(_powershell_path(target_path))
        if not target_path.is_dir():
            raise HostSetupError(f"workspace is not a directory: {target}")
        desired_workspace = _wsl_path(target_path) if _is_windows() else str(target_path)

        _step(op, "plan", "Checking this computer…")
        if _cancel_requested(op):
            raise HostSetupError("Setup was cancelled.")
        current = status()
        if current.running:
            changes: list[str] = []
            if int(current.port) != int(options.port):
                changes.append("port")
            if current.session != options.session:
                changes.append("session")
            if current.execution_mode != options.mode:
                changes.append("mode")
            if current.workspace and desired_workspace and current.workspace != desired_workspace:
                changes.append("workspace")
            if changes:
                _step(op, "restart", "Restarting the host to apply " + ", ".join(changes) + "…")
                stop()
                current = status()

        if _is_windows():
            _ensure_wsl(op)
            _step(op, "runtime", "Installing the WSL Machina runtime and ttyd…")
            distro = _select_distro()
            _wsl_user(distro)
            _ensure_wsl_packages(distro)
            _setup_windows(
                distro,
                str(target_path),
                options.mode,
                options.port,
                options.session,
                options.allow_full,
            )
        elif _is_wsl():
            _step(op, "runtime", "Refreshing the host runtime and start scripts…")
            distro = os.environ.get("WSL_DISTRO_NAME") or DEFAULT_WSL_DISTRO
            _setup_windows(
                distro,
                str(target_path),
                options.mode,
                options.port,
                options.session,
                options.allow_full,
            )
        else:
            if not shutil.which("tmux"):
                raise HostSetupError("tmux is required. Install it with your package manager.")
            if not shutil.which("ttyd"):
                raise HostSetupError("ttyd is required for the browser terminal.")
            if not current.running:
                local_host.start_host(
                    workspace=target_path,
                    mode=options.mode,
                    session=options.session,
                )

        if _cancel_requested(op):
            raise HostSetupError("Setup was cancelled.")
        _ensure_tailscale(op)

        if _cancel_requested(op):
            raise HostSetupError("Setup was cancelled.")
        _step(op, "start", "Starting the persistent shell and browser bridge…")
        current = start()
        if not current.running:
            raise HostSetupError("The host started but is not running; check the host log.")

        # Make the browser path and logon startup durable before touching SSH,
        # because enabling OpenSSH can require a Windows restart.
        _configure_autostart(op)
        if options.access in {"both", "browser"}:
            _ensure_serve(op, options.port)
        if options.access in {"both", "ssh"}:
            _ensure_ssh(op)
            if options.public_key.strip():
                _install_ssh_key(op, options.public_key)

        _step(op, "verify", "Verifying access…")
        final = status()
        problems: list[str] = []
        if not final.running:
            problems.append("host process")
        if not final.tailscale_connected:
            problems.append("Tailscale")
        if options.access in {"both", "browser"} and not final.tailnet_url:
            problems.append("browser URL")
        if options.access in {"both", "ssh"} and not final.sshd_running:
            problems.append("SSH server")
        if problems:
            raise HostSetupError(
                "Setup finished but these pieces are still missing: " + ", ".join(problems)
            )
        _clear_pending()
        with op.lock:
            op.result = {
                "status": final.public(),
                "instructions": client_instructions(options.client, options.access),
            }
            op.state = "done"
            op.step = "done"
            op.message = "Machina host is ready."
            op.url = final.tailnet_url or ""
    except HostSetupRestartRequired as error:
        _save_pending(options, str(error))
        with op.lock:
            op.state = "restart_required"
            op.restart_required = True
            op.error = str(error)
            op.message = str(error)
    except HostSetupError as error:
        _clear_pending()
        with op.lock:
            op.state = "cancelled" if op.cancelled.is_set() else "error"
            op.error = str(error)
            op.message = str(error)
    except Exception as error:  # a setup bug must not strand the overlay
        _clear_pending()
        with op.lock:
            op.state = "error"
            op.error = f"{type(error).__name__}: {error}"
            op.message = "Host setup failed."


def _operation_view(op: _SetupOperation) -> dict[str, Any]:
    with op.lock:
        return {
            "id": op.id,
            "state": op.state,
            "step": op.step,
            "message": op.message,
            "url": op.url,
            "error": op.error,
            "restart_required": op.restart_required,
            "done": op.state in {"done", "error", "cancelled", "restart_required"},
            "result": op.result,
            "log": list(op.log),
        }


def start_setup(options: SetupOptions) -> str:
    """Start one idempotent setup operation and return its operation id."""
    if options.mode == "full" and not options.allow_full:
        raise HostSetupError("Full host mode is refused for remote access.")
    if options.access not in {"both", "browser", "ssh"}:
        raise HostSetupError("access must be both, browser, or ssh.")
    with _operations_lock:
        for operation in _operations.values():
            if operation.state == "running":
                return operation.id
        operation = _SetupOperation(id=secrets.token_hex(8), options=options)
        _clear_pending()
        _operations[operation.id] = operation
        # Keep only a bounded history of settled operations.
        if len(_operations) > 20:
            for key in [key for key, value in _operations.items() if value.state != "running"][:5]:
                _operations.pop(key, None)
    threading.Thread(
        target=_execute_setup, args=(operation,), name="machina-host-setup", daemon=True
    ).start()
    return operation.id


def setup_status(operation_id: str) -> dict[str, Any]:
    with _operations_lock:
        operation = _operations.get(operation_id)
    if operation is None:
        raise HostSetupError("That host setup operation is no longer available.")
    return _operation_view(operation)


def cancel_setup(operation_id: str) -> dict[str, Any]:
    with _operations_lock:
        operation = _operations.get(operation_id)
    if operation is None:
        raise HostSetupError("That host setup operation is no longer available.")
    operation.cancelled.set()
    with operation.lock:
        operation.message = "Cancelling host setup…"
    return _operation_view(operation)


def open_url(url: str) -> bool:
    """Open an installer/login link through the host browser."""
    return _open_url(url.strip())


def plan_setup(options: SetupOptions = SetupOptions()) -> dict[str, Any]:
    """Return the missing setup work without changing the host."""
    current = status()
    if _is_windows() or _is_wsl():
        ssh_ready = _ssh_firewall_ready()
    else:
        ssh_ready = current.sshd_running
    steps: list[dict[str, str]] = []
    if not current.runtime_ready:
        steps.append(
            {
                "id": "runtime",
                "label": "Install the host runtime and browser bridge",
                "detail": "Creates the Machina runtime and ttyd for the persistent shell.",
            }
        )
    if not current.tailscale_connected:
        steps.append(
            {
                "id": "tailscale",
                "label": "Install and sign in to Tailscale",
                "detail": "Required for access from another Wi-Fi.",
            }
        )
    if options.access in {"both", "ssh"} and not ssh_ready:
        steps.append(
            {
                "id": "ssh",
                "label": "Enable the SSH server and restrict it to Tailscale",
                "detail": "Required for the terminal path.",
            }
        )
    if options.access in {"both", "ssh"} and options.public_key.strip():
        steps.append(
            {
                "id": "key",
                "label": "Install the client SSH public key",
                "detail": "Adds the key to the host's authorized keys.",
            }
        )
    if not current.running:
        steps.append(
            {
                "id": "start",
                "label": "Start the persistent tmux shell and browser bridge",
                "detail": "Runs the host start script on the host.",
            }
        )
    if options.access in {"both", "browser"} and not current.tailnet_url:
        steps.append(
            {
                "id": "serve",
                "label": "Enable Tailscale Serve for the browser",
                "detail": "Creates the private HTTPS URL for the browser terminal.",
            }
        )
    if (_is_windows() or _is_wsl()) and not _autostart_ready():
        steps.append(
            {
                "id": "autostart",
                "label": "Start the host at Windows logon",
                "detail": "Creates a hidden Startup entry for the persistent shell.",
            }
        )
    steps.append(
        {
            "id": "verify",
            "label": "Verify the requested access paths",
            "detail": "Confirms the host, Tailscale, and the selected access path are reachable.",
        }
    )
    return {"status": current.public(), "steps": steps, "pending": pending_setup()}


def setup_plan() -> dict[str, Any]:
    """Compatibility wrapper for callers that inspect the default plan."""
    return plan_setup()


def setup(
    *,
    workspace: str = "",
    mode: str = local_host.DEFAULT_MODE,
    access: str = "both",
    client: str = "macos",
    public_key: str = "",
    port: int = _DEFAULT_PORT,
    session: str = _DEFAULT_SESSION,
    allow_full: bool = False,
) -> HostStatus:
    """Synchronous setup for CLI/tests; `/host` uses start_setup + polling."""
    options = SetupOptions(
        workspace=workspace,
        mode=mode,
        access=access,
        client=client,
        public_key=public_key,
        port=port,
        session=session,
        allow_full=allow_full,
    )
    operation = _SetupOperation(id=secrets.token_hex(8), options=options)
    _execute_setup(operation)
    with operation.lock:
        if operation.state == "restart_required":
            raise HostSetupRestartRequired(operation.message)
        if operation.state == "error":
            raise HostSetupError(operation.error or operation.message)
    return status()
