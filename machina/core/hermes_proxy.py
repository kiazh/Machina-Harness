"""Detect and manage the local Hermes OpenAI-compatible proxy.

Hermes ships a loopback proxy (``hermes proxy start``) that forwards
OpenAI-compatible requests to an OAuth-authenticated upstream (Nous Portal or
xAI). Machina does not bundle Hermes; it detects the installed CLI on any
platform, starts the proxy only when an upstream is already signed in, and
exposes the proxy's ``/v1/models`` catalog as the ``hermes`` provider.

The lifecycle is fail-open and bounded: detection and status never raise into
a turn, a missing/unauthorized Hermes is reported as an actionable message,
and Machina only stops a proxy it started itself.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from machina.core.config import config

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8645
START_TIMEOUT_SECONDS = 8.0
STATUS_TIMEOUT_SECONDS = 10.0
MODELS_TTL_SECONDS = 60.0
INSTALL_TIMEOUT_SECONDS = 900.0
LOGIN_WATCH_SECONDS = 300.0
PROVIDER_LABEL = "Hermes proxy"

INSTALL_SH_URL = "https://hermes-agent.nousresearch.com/install.sh"
INSTALL_PS1_URL = "https://hermes-agent.nousresearch.com/install.ps1"

# Re-entrant: start() warms the catalog, and a transient loopback failure in
# that warm-up may re-enter start() on the same thread.
_LOCK = threading.RLock()
_login_watch_started = False
_process: subprocess.Popen[Any] | None = None
_process_port: int | None = None
_cache_at = 0.0
_cache: list[dict[str, Any]] | None = None
_last_error: str | None = None


def _config_text(key: str, default: str = "") -> str:
    try:
        return str(config.get(key) or default).strip()
    except Exception:
        return default


def _config_bool(key: str, default: bool) -> bool:
    try:
        return bool(config.get(key))
    except Exception:
        return default


def _config_int(key: str, default: int) -> int:
    try:
        return int(config.get(key))
    except (TypeError, ValueError):
        return default


def port() -> int:
    """Loopback port the proxy should use (configurable, default 8645)."""
    value = _config_int("hermes_proxy_port", DEFAULT_PORT)
    return value if 1 <= value <= 65535 else DEFAULT_PORT


def base_url() -> str:
    return f"http://127.0.0.1:{port()}/v1"


def find_binary() -> str | None:
    """Locate the Hermes CLI using PATH/PATHEXT on every supported OS."""
    for name in ("hermes", "hermes.exe", "hermes.cmd", "hermes.bat"):
        found = shutil.which(name)
        if found:
            return found
    # Common manual locations that are not always on PATH, especially when
    # the TUI is launched by a desktop shortcut. Windows installers (and
    # pipx) commonly write hermes.exe, so include the extension variants.
    home = Path.home()
    names = ["hermes"]
    if os.name == "nt":
        names += ["hermes.exe", "hermes.cmd", "hermes.bat"]
    candidates = (
        [home / ".local" / "bin" / name for name in names]
        + [home / ".hermes" / "bin" / name for name in names]
        + [home / "bin" / name for name in names]
    )
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", "")) if os.environ.get("LOCALAPPDATA") else None
        if local is not None:
            # The native installer lives under %LOCALAPPDATA%\hermes and puts
            # its shims in bin/ (and .hermes/bin/ in some releases).
            candidates += (
                [local / "hermes" / "bin" / name for name in names]
                + [local / "hermes" / ".hermes" / "bin" / name for name in names]
                + [local / "hermes" / name for name in names]
                + [local / "Programs" / "hermes" / name for name in names]
            )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def installed() -> bool:
    return find_binary() is not None


def _status_text(binary: str) -> str:
    try:
        result = subprocess.run(
            [binary, "proxy", "status"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=STATUS_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"could not query `hermes proxy status`: {error}") from error
    return f"{result.stdout}\n{result.stderr}"


def ready_providers() -> list[str]:
    """Upstreams Hermes reports as signed in (``nous`` and/or ``xai``)."""
    binary = find_binary()
    if binary is None:
        return []
    try:
        text = _status_text(binary).lower()
    except RuntimeError:
        return []
    ready: list[str] = []
    signed_out = (
        "not logged in",
        "not signed in",
        "signed out",
        "logged out",
        "no credentials",
        "no token",
        "unauthenticated",
        "expired",
    )
    for provider in ("nous", "xai"):
        # Status rows name the provider and its state. The documented
        # signed-out marker is "not logged in"; a row without any negative
        # marker is treated as ready so a wording change upstream ("ready",
        # "authenticated", "✓") does not silently disable autostart.
        for line in text.splitlines():
            if provider not in line:
                continue
            if any(marker in line for marker in signed_out):
                continue
            ready.append(provider)
            break
    return ready


def _choose_provider(ready: list[str]) -> str:
    configured = _config_text("hermes_proxy_provider", "auto").lower()
    if configured in {"nous", "xai"}:
        if configured in ready:
            return configured
        if not ready:
            return configured
    return ready[0] if ready else "nous"


def reaches(timeout: float = 1.5) -> bool:
    """True when the proxy answers /v1/models on its loopback port."""
    try:
        response = httpx.get(
            f"{base_url()}/models",
            headers={"Authorization": "Bearer machina"},
            timeout=timeout,
        )
        return response.status_code == 200
    except Exception:
        return False


def _log_path() -> Path:
    home = _config_text("machina_home")
    root = Path(home).expanduser() if home else Path.home() / ".machina"
    return root / "logs" / "hermes-proxy.log"


def _terminate(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            process.terminate()
        except OSError:
            pass


def start(*, wait: bool = True) -> tuple[bool, str]:
    """Start the proxy when Hermes is installed and an upstream is signed in.

    Returns ``(started, message)``; ``started`` is True when the proxy was
    already reachable or Machina launched it successfully. The message is
    user-actionable and never contains credentials.
    """
    global _process, _process_port, _last_error

    if reaches():
        _warm_catalog()
        return True, f"Hermes proxy already running at {base_url()}."
    binary = find_binary()
    if binary is None:
        _last_error = (
            "Hermes is not installed. Install the Hermes CLI, then run "
            "`hermes portal` (Nous) or `hermes auth` (xAI) to sign in."
        )
        return False, _last_error
    ready = ready_providers()
    if not ready:
        _last_error = (
            "Hermes is installed but no proxy upstream is signed in. Run "
            "`hermes portal` (Nous) or `hermes auth` (xAI), then retry."
        )
        return False, _last_error
    provider = _choose_provider(ready)
    log_path = _log_path()
    with _LOCK:
        # Startup detection and a provider factory call can race. Re-check
        # inside the lock and adopt a live process Machina already owns
        # instead of killing and respawning it.
        if reaches():
            _warm_catalog()
            return True, f"Hermes proxy already running at {base_url()}."
        if _process is not None and _process.poll() is None and _process_port == port():
            process = _process
        else:
            # A live child on a stale port (config changed) must be replaced,
            # not adopted: waiting for the new port would always time out.
            _terminate(_process)
            try:
                log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                log = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - passed to child
            except OSError as error:
                _last_error = f"Could not open the Hermes proxy log: {error}"
                return False, _last_error
            argv = [
                binary,
                "proxy",
                "start",
                "--provider",
                provider,
                "--host",
                "127.0.0.1",
                "--port",
                str(port()),
            ]
            try:
                process = subprocess.Popen(
                    argv,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=os.name != "nt",
                    creationflags=(
                        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    )
                    if os.name == "nt"
                    else 0,
                    close_fds=True,
                )
            except OSError as error:
                _last_error = f"Could not start the Hermes proxy: {error}"
                return False, _last_error
            finally:
                # The child owns its copy; keeping the parent descriptor open
                # would leak one handle per proxy restart.
                log.close()
            _process = process
            _process_port = port()
            _last_error = None
    if wait:
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if reaches():
                _last_error = None
                return True, f"Hermes proxy started ({provider}) at {base_url()}."
            if process.poll() is not None:
                _last_error = f"Hermes proxy exited during startup. See {log_path} for details."
                return False, _last_error
            time.sleep(0.2)
        _terminate(process)
        with _LOCK:
            if _process is process:
                _process = None
                _process_port = None
        _last_error = f"Hermes proxy did not become ready. See {log_path} for details."
        return False, _last_error
    return True, f"Hermes proxy starting ({provider}) at {base_url()}."


def last_error() -> str | None:
    """Most recent actionable failure, or None after a successful start."""
    return _last_error


def stop() -> tuple[bool, str]:
    """Stop only the proxy Machina started; external proxies are untouched."""
    global _process, _process_port, _last_error
    with _LOCK:
        if _process is None:
            return False, "No Machina-owned Hermes proxy is running."
        _terminate(_process)
        _process = None
        _process_port = None
        _last_error = None
    return True, "Hermes proxy stopped."


def owned_running() -> bool:
    """True when Machina's own proxy child is still alive."""
    return _process is not None and _process.poll() is None


def status() -> dict[str, Any]:
    binary = find_binary()
    ready = ready_providers() if binary else []
    return {
        "installed": binary is not None,
        "binary": binary,
        "running": reaches(),
        "owned": owned_running(),
        "port": port(),
        "base_url": base_url(),
        "provider": _choose_provider(ready) if ready else "",
        "readyProviders": ready,
        "error": _last_error,
    }


def _parse_models(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        owner = str(item.get("owned_by") or "").strip()
        # Gateway catalog shape: the UI, routing, and context-window code all
        # key on ``id``. The CLI prints the same rows.
        rows.append(
            {
                "id": model_id,
                "displayName": model_id,
                "description": f"{PROVIDER_LABEL}" + (f" · {owner}" if owner else ""),
                "contextWindow": None,
                "efforts": [],
                # Nous free-tier ids carry the suffix; routing uses this to
                # avoid selecting a paid model the account cannot call.
                "free": model_id.endswith(":free"),
                "source": "live",
            }
        )
    return rows


def fetch_models(*, force: bool = False) -> list[dict[str, Any]]:
    """Live model catalog from the local Hermes proxy (TTL cached)."""
    global _cache, _cache_at
    if not force and _cache is not None and time.monotonic() - _cache_at < MODELS_TTL_SECONDS:
        return [dict(row) for row in _cache]
    if not reaches():
        ok, message = start(wait=True)
        if not ok:
            raise RuntimeError(message)
    response = httpx.get(
        f"{base_url()}/models",
        headers={"Authorization": "Bearer machina"},
        timeout=5.0,
    )
    response.raise_for_status()
    rows = _parse_models(response.json())
    _cache = rows
    _cache_at = time.monotonic()
    return [dict(row) for row in rows]


def _warm_catalog() -> None:
    """Populate the model cache so routing/pickers see it immediately."""
    try:
        fetch_models(force=True)
    except Exception:
        pass


def cached_models() -> list[dict[str, Any]] | None:
    if _cache is None:
        return None
    return [dict(row) for row in _cache]


def fallback_models() -> list[dict[str, Any]]:
    return []


def default_model() -> str:
    try:
        rows = fetch_models()
    except Exception:
        return "auto"
    return str(rows[0].get("id") or rows[0].get("model") or "auto") if rows else "auto"


def autostart_if_ready() -> tuple[bool, str]:
    """Launch on Machina startup only when detection and sign-in both pass."""
    if not _config_bool("hermes_proxy_autostart", True):
        return False, "Hermes proxy autostart is disabled."
    if not installed():
        return False, "Hermes is not installed."
    return start(wait=True)


def migrate_legacy_provider() -> bool:
    """Fold the pre-built-in ``hermes-proxy`` named provider into ``hermes``.

    Older builds exposed the loopback proxy as a user-added named provider and
    persisted its model catalog. The built-in ``hermes`` route now owns that
    endpoint, so keeping both creates two identical provider groups in
    ``/models`` and leaves the saved route on stale cached models. Remove the
    alias entry and move the saved route/model to the canonical provider when
    the proxy catalog is available. Returns True when a legacy entry was
    removed.
    """
    from machina.security import providers as store
    from machina.security.providers import is_loopback_base_url

    legacy_name = "hermes-proxy"
    entry = store.get_provider_entry(legacy_name)
    if entry is None:
        return False
    base = str(entry.get("base_url") or "").strip().rstrip("/").lower()
    if not base or not is_loopback_base_url(base):
        return False
    if not (base.endswith("/v1") or base == base_url().rstrip("/").lower()):
        return False
    report = store.remove_provider(legacy_name)
    if report is None:
        return False
    try:
        from machina.core.config import config, persist_provider_fallback

        current = str(config.get("provider") or "").strip().lower()
        if current == legacy_name:
            rows = cached_models() or []
            ids = {str(row.get("id") or "") for row in rows if row.get("id")}
            model = str(config.get("model") or "").strip()
            if ids and model not in ids:
                # Preserve the requested family when the old catalog used a
                # free-tier suffix that the current gateway omits.
                base = model.removesuffix(":free")
                if base in ids:
                    model = base
                elif f"{model}:free" in ids:
                    model = f"{model}:free"
                else:
                    model = str(rows[0].get("id") or "").strip()
            persist_provider_fallback("hermes", model or None)
    except Exception as error:
        logger.warning("Hermes provider migration could not update config: %s", error)
    logger.info("Removed legacy Hermes named provider; using built-in hermes")
    return True


def _install_argv() -> list[str]:
    """Official Hermes installer for the current OS (fixed URLs only)."""
    if os.name == "nt":
        return [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& ([scriptblock]::Create((irm {INSTALL_PS1_URL}))) -NonInteractive",
        ]
    if shutil.which("curl"):
        return ["/bin/sh", "-c", f"curl -fsSL {INSTALL_SH_URL} | bash -s -- --non-interactive"]
    if shutil.which("wget"):
        return ["/bin/sh", "-c", f"wget -qO- {INSTALL_SH_URL} | bash -s -- --non-interactive"]
    return []


def _tail(text: str, limit: int = 600) -> str:
    compact = " ".join(str(text or "").split())
    return compact[-limit:] if len(compact) > limit else compact


def _append_install_log(text: str) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass


def install(*, wait: bool = True) -> tuple[bool, str]:
    """Install the official Hermes CLI when it is not already detected.

    The installer is the vendor's documented one-liner, run without a shell
    interpolation of user input. ``wait=False`` starts it in the background
    and reports through the proxy log; the gateway uses that path so Setup
    returns immediately.
    """
    if installed():
        return True, "Hermes is already installed."
    argv = _install_argv()
    if not argv:
        return (
            False,
            "This system has neither curl nor wget, so the official Hermes "
            "installer cannot run. Install curl and click Setup again.",
        )
    if not wait:
        try:
            log = open(_log_path(), "a", encoding="utf-8")  # noqa: SIM115 - child owns it
            try:
                subprocess.Popen(
                    argv,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=os.name != "nt",
                    creationflags=(
                        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    )
                    if os.name == "nt"
                    else 0,
                    close_fds=True,
                )
            finally:
                log.close()
        except OSError as error:
            return False, f"Could not start the Hermes installer: {error}"
        return True, f"Installing Hermes in the background. See {_log_path()}."
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=INSTALL_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"Could not run the Hermes installer: {error}"
    _append_install_log((result.stdout or "") + (result.stderr or ""))
    if result.returncode != 0:
        detail = _tail(result.stderr or result.stdout or "")
        return False, f"Hermes installer failed (exit {result.returncode}). {detail}".strip()
    binary = find_binary()
    if binary is None:
        return False, (
            "Hermes installed, but its launcher is not on PATH for this process yet. "
            "Sign out and back in (or restart the terminal) and run Setup again."
        )
    return True, f"Hermes installed at {binary}."


def _login_argv(provider: str) -> list[str]:
    binary = find_binary()
    if binary is None:
        return []
    # `auth add` performs only the OAuth consent (no extra model-pick
    # prompts); the proxy catalog already exposes the upstream's models.
    return [binary, "auth", "add", provider, "--type", "oauth"]


def _command_text(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _open_login_terminal(argv: list[str]) -> tuple[bool, str]:
    """Open a visible terminal running the Hermes OAuth login.

    OAuth login is interactive by Hermes' own design, so this is the one
    unavoidable human handoff. Setup opens it for the user; it is never a
    command the user has to copy.
    """
    if not argv:
        return False, "Hermes is not installed."
    if sys.platform == "darwin":
        try:
            directory = Path(tempfile.mkdtemp(prefix="machina-hermes-login-"))
            script = directory / "login.command"
            script.write_text(
                "#!/bin/sh\n"
                f"{shlex.join(argv)}\n"
                "printf '\\nYou can close this window after the browser confirms sign-in.\\n'\n",
                encoding="utf-8",
            )
            script.chmod(0o700)
            opened = subprocess.run(
                ["open", "-a", "Terminal", str(script)],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if opened.returncode != 0:
                detail = _tail(opened.stderr or opened.stdout or "")
                return False, f"Could not open Terminal. {detail}".strip()
            return True, f"Sign-in window opened ({script})."
        except (OSError, subprocess.SubprocessError) as error:
            return False, f"Could not open Terminal: {error}"
    if os.name == "nt":
        try:
            cmdline = subprocess.list2cmdline(argv)
            opened = subprocess.run(
                ["cmd.exe", "/c", "start", "", "cmd.exe", "/k", cmdline],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                ),
            )
            if opened.returncode != 0:
                detail = _tail(opened.stderr or opened.stdout or "")
                return False, f"Could not open a console window. {detail}".strip()
            return True, "Sign-in window opened."
        except (OSError, subprocess.SubprocessError) as error:
            return False, f"Could not open a console window: {error}"
    direct = (
        ("x-terminal-emulator", ["-e"]),
        ("gnome-terminal", ["--"]),
        ("konsole", ["-e"]),
        ("xterm", ["-e"]),
    )
    for name, prefix in direct:
        executable = shutil.which(name)
        if not executable:
            continue
        try:
            subprocess.Popen(
                [executable, *prefix, *argv],
                start_new_session=True,
            )
            return True, f"Sign-in window opened with {name}."
        except OSError:
            continue
    xfce = shutil.which("xfce4-terminal")
    if xfce:
        try:
            subprocess.Popen([xfce, "-e", _command_text(argv)], start_new_session=True)
            return True, "Sign-in window opened with xfce4-terminal."
        except OSError:
            pass
    return False, f"Could not open a terminal. Run: {_command_text(argv)}"


def _watch_login() -> None:
    """Poll until the OAuth login completes, then start the proxy."""
    global _login_watch_started
    with _LOCK:
        if _login_watch_started:
            return
        _login_watch_started = True
    try:
        deadline = time.monotonic() + LOGIN_WATCH_SECONDS
        while time.monotonic() < deadline:
            if ready_providers():
                start(wait=True)
                return
            time.sleep(5)
    finally:
        with _LOCK:
            _login_watch_started = False


def _watch_install(provider: str) -> None:
    """Wait for a background install, then open the login window."""
    deadline = time.monotonic() + INSTALL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if installed():
            break
        time.sleep(2)
    if not installed():
        _append_install_log("Hermes install did not finish; launcher not found.\n")
        return
    if ready_providers():
        start(wait=True)
        return
    opened, message = _open_login_terminal(_login_argv(provider))
    _append_install_log(f"{message}\n")
    if opened:
        _watch_login()


def setup(
    provider: str | None = None,
    *,
    interactive: bool | None = None,
    open_window: bool = True,
) -> tuple[bool, str]:
    """One-click path: install, sign in, then start the proxy.

    ``interactive=True`` runs the OAuth flow in the current terminal (CLI).
    ``interactive=False, open_window=True`` installs in the background and
    opens a terminal window for the OAuth consent (gateway/TUI).
    ``open_window=False`` never opens a window; the returned message carries
    the one command to run. The proxy starts automatically once
    ``hermes proxy status`` turns ready.
    """
    if interactive is None:
        interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if not installed():
        if interactive or not open_window:
            ok, message = install(wait=True)
            if not ok:
                return False, message
        else:
            ok, message = install(wait=False)
            if not ok:
                return False, message
            target = _requested_provider(provider)
            threading.Thread(
                target=_watch_install,
                args=(target,),
                name="hermes-install-watch",
                daemon=True,
            ).start()
            return True, (
                "Installing Hermes. A sign-in window opens when the installer "
                f"finishes; progress is in {_log_path()}."
            )
    ready = ready_providers()
    if ready:
        return start(wait=True)
    target = _requested_provider(provider)
    argv = _login_argv(target)
    if not argv:
        return False, "Hermes is installed but its launcher could not be resolved."
    if not open_window:
        return False, (
            f"Hermes is installed but no upstream is signed in. Run: {_command_text(argv)}"
        )
    if interactive:
        try:
            result = subprocess.run(argv, timeout=LOGIN_WATCH_SECONDS)
        except (OSError, subprocess.SubprocessError) as error:
            return False, f"Could not start the Hermes sign-in: {error}"
        if ready_providers():
            return start(wait=True)
        if result.returncode != 0:
            return False, f"Hermes sign-in exited with status {result.returncode}."
        return False, "Hermes sign-in did not report a ready upstream; run Setup again."
    opened, message = _open_login_terminal(argv)
    if opened:
        threading.Thread(target=_watch_login, name="hermes-login-watch", daemon=True).start()
        return True, (
            f"{message} Finish the browser sign-in there; Machina starts the "
            "proxy automatically when it completes."
        )
    return False, message


def _requested_provider(provider: str | None) -> str:
    candidate = (provider or _config_text("hermes_proxy_provider", "auto")).strip().lower()
    return candidate if candidate in {"nous", "xai"} else "nous"


def validate_base_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def schedule_autostart() -> asyncio.Task[tuple[bool, str]] | None:
    """Schedule detection/launch off the gateway initialization path."""
    if os.environ.get("MACHINA_GATEWAY_NO_SCHEDULER"):
        return None
    if not _config_bool("hermes_proxy_autostart", True):
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(asyncio.to_thread(autostart_if_ready))
    task.add_done_callback(_log_task)
    return task


def _log_task(task: asyncio.Task[tuple[bool, str]]) -> None:
    try:
        ok, message = task.result()
    except (asyncio.CancelledError, Exception):
        return
    if ok:
        logger.info("Hermes proxy auto-start: %s", message)
    else:
        logger.debug("Hermes proxy auto-start skipped: %s", message)
