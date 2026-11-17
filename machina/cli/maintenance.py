"""Setup, health, service and host-maintenance commands for the Machina CLI."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer
from rich.markup import escape

from machina import __version__, services
from machina import interop as interop_mod
from machina.cli import output
from machina.cli.common import (
    _db_connected,
    _normalize_mode,
    _run,
    _sync_env_shadow,
)
from machina.cli.output import console
from machina.core.config import SECRET_KEYS, config, is_configured_secret, validate_value
from machina.core.provider.specs import provider_names
from machina.db.connection import db


def status():
    """Show Machina status: database, sessions, tools and API key."""

    async def _status():
        try:
            try:
                await db.connect()
                version = await db.fetchval("SELECT version()")
            except Exception as e:
                output.status_line("PostgreSQL", f"connection failed ({e})", "error")
                raise typer.Exit(1) from None
            output.status_line("PostgreSQL", f"connected ({version.split(',')[0]})", "success")
            output.status_line("Sessions", str(await db.fetchval("SELECT COUNT(*) FROM sessions")))
            output.status_line(
                "Context chunks", str(await db.fetchval("SELECT COUNT(*) FROM context_chunks"))
            )

            from machina.tools.base import registry

            tools = registry.list_tools()
            output.status_line("Tools", f"{len(tools)} registered")
            for t in tools:
                console.print(f"    [dim]•[/dim] {t}")

            if is_configured_secret(config.get("openrouter_api_key")):
                output.status_line("OpenRouter API key", "set", "success")
            else:
                output.status_line("OpenRouter API key", "not set", "warning")
            # Real capability reporting (never healthy-by-flag alone).
            try:
                summary = await services.status_summary()
                # Shared subset comes from services.format_status_lines (one
                # source of truth with the MCP shim); this surface keeps its
                # Rich labels and its own extras (direct DB/registry reads).
                shared = dict(services.format_status_lines(summary))
                output.status_line(
                    "Mode", f"{shared['Mode']} | Backend: {shared['Backend']}", "success"
                )
                if shared["Workspace"]:
                    output.status_line("Workspace", shared["Workspace"])
                output.status_line("Chat provider", shared["Chat provider"])
                output.status_line("Memory retrieval", shared["Memory retrieval"])
                output.status_line("Memory extraction", shared["Memory extraction"])
                output.status_line("Document RAG", shared["Document RAG"])
                output.status_line("Reranker", shared["Reranker"])
                output.status_line("Automatic compaction", shared["Automatic compaction"])
                output.status_line("Sandbox", shared["Sandbox"])
                output.status_line("Jobs awaiting approval", shared["Jobs awaiting approval"])
                output.status_line("Research training", str(summary.get("researchTraining", "")))
            except Exception as e:
                output.status_line("Capabilities", f"unavailable ({e})", "warning")
            console.print()
            output.muted("Run `machina` to open the interactive UI.")
        finally:
            await db.close()

    _run(_status())


def set_mode(
    mode: str = typer.Argument(None, help="ask|workspace|sandbox|full (empty shows current)"),
    revoke: bool = typer.Option(False, "--revoke", help="Revoke grants and return to ask mode"),
):
    """Show or set the execution mode."""

    async def _mode():
        # Show/set of the persistent default needs no DB; revocation does.
        if revoke:
            async with _db_connected():
                from machina.permissions.broker import permission_broker

                count = await permission_broker.revoke("*")
                config.set("execution_mode", "ask", persist=True)
                _sync_env_shadow("execution_mode", "ask")
                output.status_line("Mode", f"ask ({count} grants revoked)", "success")
            return
        if not mode:
            output.status_line("Mode", str(config.get("execution_mode")), "success")
            console.print("ask: routine tools proceed; risky actions ask (default)")
            console.print("workspace: project operation; crossing boundaries asks")
            console.print("sandbox: isolated container | full: explicit FULL HOST grant")
            console.print("`machina mode X` sets the persistent default for new sessions.")
            return
        normalized = _normalize_mode(mode, option="mode")
        if normalized == "full":
            confirm = typer.confirm(
                "Set FULL HOST as the persistent default for new sessions? "
                "Each session still activates explicitly; "
                "credentials/elevation/destructive ops always ask.",
                default=False,
            )
            if not confirm:
                output.muted("Full mode not activated.")
                return
        # Persistent default (LP-13): per-session activation happens in the
        # TUI (`/mode full`) and auto-grants only under this default.
        # MACHINA_EXECUTION_MODE shadows the file (Config.get checks the
        # environment first), so sync it like the gateway syncs
        # MACHINA_MODEL/MACHINA_PROVIDER on config.set.
        try:
            config.set("execution_mode", normalized, persist=True)
        except ValueError as error:
            output.error(str(error))
            raise typer.Exit(1) from None
        _sync_env_shadow("execution_mode", normalized)
        output.status_line("Mode", f"{normalized} (saved)", "success")
        if normalized == "full":
            console.print(
                "[yellow]FULL HOST default saved. Sessions activate explicitly. "
                "Revoke everywhere with `machina mode --revoke`.[/yellow]"
            )

    _run(_mode())


def _doctor_interop_line(key: str, result: object) -> str:
    """Read-only doctor line: installed/version/running/registered + fix hint."""
    label = getattr(result, "label", key)
    if not getattr(result, "installed", False):
        return f"  {label}: not installed (install `{key}` to enable Machina tools there)"
    version = getattr(result, "version", None)
    parts = [f"installed ({version})" if version else "installed"]
    parts.append("running" if getattr(result, "running", False) else "not running")
    if getattr(result, "registered", False):
        scopes = "+".join(getattr(result, "scopes", []) or [])
        parts.append(f"registered ({scopes})" if scopes else "registered")
        return f"  {label}: " + ", ".join(parts)
    parts.append("not registered")
    return (
        f"  {label}: "
        + ", ".join(parts)
        + f" — fix: `machina harnesses enable {key} --scope global`"
    )


def doctor():
    """Check Machina dependencies and configuration."""
    import importlib.util
    import sys

    from machina.ui_launcher import MIN_NODE_VERSION, _node_version, ui_dir

    console.print("[bold]Machina Doctor[/bold]\n")

    def check(label: str, ok: bool, detail: str = "", hint: str = "") -> None:
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {escape(label)}: {mark} {escape(detail)}".rstrip())
        if not ok and hint:
            console.print(f"      {escape(hint)}", style="dim")

    check(
        "Python", sys.version_info >= (3, 11), sys.version.split()[0], "Python 3.11+ is required."
    )
    for dep in ("typer", "rich", "asyncpg", "httpx", "msgpack", "yaml", "tiktoken", "dotenv"):
        check(dep, importlib.util.find_spec(dep) is not None, "", "Run: pip install -e .")

    node = shutil.which("node")
    node_version = _node_version(node) if node else None
    wanted = ".".join(map(str, MIN_NODE_VERSION))
    check(
        "Node.js",
        node_version is not None and node_version >= MIN_NODE_VERSION,
        ".".join(map(str, node_version)) if node_version else "not found",
        f"Node.js {wanted}+ is required for the interactive UI.",
    )
    ui_deps = ui_dir() / "node_modules" / "@earendil-works" / "pi-tui"
    check(
        "UI dependencies",
        ui_deps.exists(),
        "",
        f'Run: npm install --ignore-scripts --prefix "{ui_dir()}"',
    )

    def _key_configured(value: object) -> bool:
        # Placeholders copied from .env.example must not count as configured —
        # otherwise doctor reports ✓ and every provider call fails with 401.
        return is_configured_secret(value)

    check(
        "OPENROUTER_API_KEY",
        _key_configured(config.get("openrouter_api_key")),
        "",
        "Run `machina setup` or set it in .env.",
    )
    check(
        "DATABASE_URL",
        bool(config.get("database_url")),
        "",
        "Run `machina setup` or set it in .env.",
    )

    async def _check_db():
        try:
            await db.connect()
            version = await db.fetchval("SELECT version()")
            check("PostgreSQL", True, f"({version.split(',')[0]})")
        except Exception as e:
            check("PostgreSQL", False, f"({e})", "Is PostgreSQL running and DATABASE_URL correct?")
        finally:
            await db.close()

    from machina.db import lifecycle

    lease = lifecycle.acquire("doctor")
    try:
        _run(_check_db())
    finally:
        lifecycle.release(lease)

    console.print()
    console.print("[bold]External agent harnesses[/bold]")
    try:
        detection = interop_mod.detect()
        for key, result in detection.items():
            console.print(_doctor_interop_line(key, result), markup=False, highlight=False)
    except Exception as error:
        console.print(f"  [yellow]Interop detection unavailable: {escape(str(error))}[/yellow]")

    console.print()
    output.muted("New install? Run `machina setup` before opening the UI with `machina`.")


def init(
    db_url: str | None = typer.Option(None, "--db-url", help="PostgreSQL connection URL"),
):
    """Initialize Machina — create the database schema."""

    from machina.db import lifecycle

    # `machina init` is the installer's provisioning hook. Starting (or
    # creating) the managed container here means a fresh checkout and the
    # curl/irm installers do not depend on a separately-started database.
    lease = lifecycle.acquire("init") if db_url is None else None

    async def _init():
        if db_url:
            db.dsn = db_url
        console.print("[bold]Initializing Machina...[/bold]")
        console.print(f"  Database: {escape(db.dsn.split('@')[-1])}")
        try:
            with output.spinner("Creating schema..."):
                import asyncpg

                try:
                    await db.connect()
                except asyncpg.InvalidCatalogNameError:
                    # The server is reachable but the target database has not
                    # been created yet. This is the common native-PostgreSQL
                    # case on fresh Windows installs.
                    output.muted(f"Database {db.dsn.split('/')[-1]} is missing; creating it...")
                    from machina.db.provision import ensure_database

                    await ensure_database(db.dsn)
                    await db.connect()
                await db.initialize_schema()
            console.print("  Schema: [green]✓ created[/green]")
        except Exception as e:
            message = escape(str(e))
            if "vector" in str(e).lower():
                message += " — install pgvector or use the pgvector Docker image"
            console.print(f"  Schema: [red]✗ failed[/red] ({message})")
            raise typer.Exit(1) from None
        finally:
            await db.close()
        console.print()
        output.success("Machina initialized!")
        output.muted("Run `machina` to open the interactive UI.")

    try:
        _run(_init())
    finally:
        lifecycle.release(lease)


def _interop_state_lines(report) -> list[str]:
    """Final-message lines describing the setup interop step's outcome."""
    detected = report.detected
    labels = {key: result.label for key, result in detected.items()}
    lines: list[str] = []
    if report.enabled:
        names = ", ".join(labels.get(key, key) for key in report.enabled)
        lines.append(
            f"Interop: registered `machina` with {names} — see `machina harnesses status`."
        )
    if report.failed:
        names = ", ".join(labels.get(key, key) for key in report.failed)
        lines.append(
            f"Interop: registration did not verify for {names} — see `machina harnesses status`."
        )
    if report.skipped:
        names = ", ".join(labels.get(key, key) for key in report.skipped)
        hint = f"machina harnesses enable {report.skipped[0]} --scope global"
        lines.append(f"Interop: {names} installed but not registered — enable with `{hint}`.")
    if not lines:
        registered = [result.label for result in detected.values() if result.registered]
        if registered:
            lines.append(f"Interop: {', '.join(registered)} already registered.")
        else:
            lines.append("Interop: no external harnesses detected (OpenCode, Codex, Hermes).")
    return lines


def _setup_interop(mode: str, *, non_interactive: bool) -> list[str]:
    """Interop step for `machina setup`: detect, optionally register.

    Best-effort by design: the .env work above is already done, so a harness
    problem is reported and swallowed — never a failed setup. Returns the
    lines for the final message block.
    """
    console.print()
    console.print("[bold]External agent harnesses[/bold]")
    if mode == "none":
        console.print("  [dim]Skipped (--interop none).[/dim]")
        return ["Interop: skipped (--interop none)."]
    try:
        if mode == "all":
            only = list(interop_mod.PLATFORMS)
            interactive = False
        else:
            only = None
            interactive = (not non_interactive) and interop_mod.stdin_is_tty()
        report = interop_mod.enable_detected(
            scope="global",
            only=only,
            interactive=interactive,
            out=lambda line: console.print(line, markup=False, highlight=False),
        )
    except Exception as error:  # pragma: no cover - interop is best-effort
        console.print(f"  [yellow]Interop setup skipped: {escape(str(error))}[/yellow]")
        return ["Interop: setup skipped (run `machina harnesses setup` later)."]
    return _interop_state_lines(report)


def setup(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing .env file"),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Create a blank .env without prompting"
    ),
    interop_mode: str = typer.Option(
        "auto",
        "--interop",
        envvar="MACHINA_INTEROP",
        help="External harness MCP registration: auto|all|none (env: MACHINA_INTEROP)",
    ),
):
    """First-launch setup — create the .env file and configure API keys.

    Creates the git-ignored ``.env`` next to the project (or at
    ``$MACHINA_ENV_FILE``) with one entry per model family you use, plus the
    default provider and model. Existing values are kept when you press
    Enter; ``--force`` starts over. Finally detects external agent harnesses
    (OpenCode, Codex, Hermes) and offers to register Machina's MCP server
    with them (``--interop auto|all|none``).
    """
    import secrets as _secrets

    from machina.security.env_file import (
        PROVIDER_KEYS,
        _write_env_lines,
        find_env_file,
        read_env_values,
        set_env_values,
    )

    mode = (interop_mode or "auto").strip().lower()
    if mode not in ("auto", "all", "none"):
        console.print(
            f"[red]Invalid --interop {escape(str(interop_mode))!r}. "
            "Use auto|all|none (env: MACHINA_INTEROP).[/red]"
        )
        raise typer.Exit(1)

    try:
        target = find_env_file()
    except (OSError, ValueError) as error:
        # A bad $MACHINA_ENV_FILE override is a typed error, never a traceback.
        output.error(f"Cannot resolve the .env location: {error}")
        raise typer.Exit(1) from None
    overwriting = target.is_file() and force
    if overwriting:
        console.print(f"[bold]Overwriting existing .env:[/bold] {escape(str(target))}")
        existing: dict[str, str] = {}
    elif target.is_file():
        console.print(f"[bold]Found existing .env:[/bold] {escape(str(target))}")
        console.print("Press Enter to keep each current value (use --force to start over).")
        existing = read_env_values(target)
    else:
        console.print(f"[bold]Creating new .env:[/bold] {escape(str(target))}")
        existing = {}

    prompts: list[tuple[str, str, str, bool]] = [
        (
            "DATABASE_URL",
            "PostgreSQL connection string",
            "postgresql://postgres:postgres@localhost:5432/machina",
            False,
        ),
        # Kiro is account-based: its local proxy uses a documented dummy
        # loopback password by default, so setup must not ask for a Kiro key.
        *[(key, desc, "", True) for key, desc in PROVIDER_KEYS if key != "KIRO_API_KEY"],
        ("MACHINA_API_KEY", "HTTP API key (blank = auto-generate)", "", True),
        ("MACHINA_PROVENANCE_KEY", "Memory provenance key (blank = auto-generate)", "", True),
    ]

    updates: dict[str, str] = {}
    if not non_interactive:
        # Default provider + model (non-secret .env values, applied to every
        # turn until /provider or /model overrides them for the session).
        # provider_names() covers built-ins plus any user-added named
        # providers (machina providers add ...), so setup accepts them too.
        known_providers = tuple(provider_names())
        try:
            current_provider = existing.get("MACHINA_PROVIDER", "").strip() or "openrouter"
            while True:
                provider_answer = (
                    typer.prompt(
                        "MACHINA_PROVIDER (default LLM provider)",
                        default=current_provider,
                        hide_input=False,
                        show_default=True,
                    )
                    .strip()
                    .lower()
                ) or current_provider
                if provider_answer in known_providers:
                    break
                console.print(
                    f"[red]Unknown provider. Choose one of: {', '.join(known_providers)}[/red]"
                )
            if provider_answer != existing.get("MACHINA_PROVIDER", "").strip():
                updates["MACHINA_PROVIDER"] = provider_answer
                console.print(
                    "[dim]Tip: set MACHINA_MODEL to a model id served by that provider.[/dim]"
                )
            current_model = existing.get("MACHINA_MODEL", "").strip() or "openrouter/free"
            model_answer = typer.prompt(
                "MACHINA_MODEL (default model id)",
                default=current_model,
                hide_input=False,
                show_default=True,
            ).strip()
            if model_answer and model_answer != existing.get("MACHINA_MODEL", "").strip():
                updates["MACHINA_MODEL"] = model_answer
        except (typer.Abort, KeyboardInterrupt):
            console.print("\n[dim]Setup cancelled.[/dim]")
            raise typer.Exit(1) from None
        for key, desc, default, hidden in prompts:
            current = existing.get(key, "")
            shown_default = default or ("" if hidden else current)
            if hidden and current.strip():
                prompt_text = f"{key} ({desc}) [currently set — Enter to keep]"
            elif hidden:
                prompt_text = f"{key} ({desc}) [Enter to skip]"
            elif current.strip():
                prompt_text = f"{key} [Enter to keep]"
                shown_default = current
            else:
                prompt_text = f"{key} ({desc})"
            try:
                answer = typer.prompt(
                    prompt_text,
                    default=shown_default,
                    hide_input=hidden,
                    show_default=bool(shown_default) and not hidden,
                )
            except (typer.Abort, KeyboardInterrupt):
                console.print("\n[dim]Setup cancelled.[/dim]")
                raise typer.Exit(1) from None
            answer = (answer or "").strip()
            if hidden and not answer and current.strip():
                continue  # keep existing secret
            if not hidden and not answer and current.strip():
                continue
            if key in ("MACHINA_API_KEY", "MACHINA_PROVENANCE_KEY") and not answer:
                answer = _secrets.token_hex(32)
                console.print(f"  Generated random {key}.")
            if answer != current:
                updates[key] = answer
    else:
        # Blank file: seed connection defaults, leave provider keys for the /keys menu.
        updates = {
            "DATABASE_URL": existing.get(
                "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/machina"
            )
        }
        if "DATABASE_URL" in existing:
            updates = {}

    if overwriting:
        # F10: --force must REPLACE the file, not merge. Build a fresh seed
        # (comments/docs from .env.example, or a header) and write it
        # atomically at 0600 via the shared env_file writer before applying
        # the new values. Previously the seed branch was skipped for an
        # existing file and set_env_values merged, so old keys/model/provider
        # and secrets survived a start-over.
        try:
            example = target.parent / ".env.example"
            seed_text = (
                example.read_text(encoding="utf-8")
                if example.is_file()
                else "# Machina environment (git-ignored)\n"
            )
            _write_env_lines(target, seed_text.splitlines())
        except (OSError, ValueError) as error:
            output.error(f"Could not overwrite {target}: {type(error).__name__}.")
            raise typer.Exit(1) from None
    elif not target.is_file():
        # Seed from .env.example so comments/docs carry over, then apply values.
        try:
            example = target.parent / ".env.example"
            if example.is_file():
                target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                target.write_text("# Machina environment (git-ignored)\n", encoding="utf-8")
        except OSError as error:
            output.error(f"Could not create {target}: {type(error).__name__}.")
            raise typer.Exit(1) from None
        # Secret file hygiene (wave-3 hotfix): the seed must be 0600 before
        # any values land; without this an updates={} path leaves it 0644.
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    if updates:
        try:
            set_env_values(updates, target)
        except (OSError, ValueError) as error:
            output.error(f"Could not write {target}: {type(error).__name__}.")
            raise typer.Exit(1) from None
        changed = ", ".join(sorted(updates))
        console.print(f"\n[green]✓ Wrote {len(updates)} value(s) to .env:[/green] {changed}")
    elif overwriting:
        console.print(f"\n[green]✓ Replaced {escape(str(target))} with a fresh .env.[/green]")
    else:
        console.print("\n[dim]No changes written.[/dim]")

    # Show key status without ever printing values.
    stored = read_env_values(target)
    for key, _desc in PROVIDER_KEYS:
        mark = "[green]set[/green]" if stored.get(key, "").strip() else "[dim]not set[/dim]"
        console.print(f"  {key}: {mark}")

    # Best-effort interop step: register Machina's MCP server where chosen.
    interop_lines = _setup_interop(mode, non_interactive=non_interactive)

    console.print()
    output.muted("Next: run `machina doctor`, then `machina init` to create the database schema.")
    for line in interop_lines:
        output.muted(line)


def version():
    """Show Machina version."""
    console.print(f"[bold cyan]Machina[/bold cyan] v{__version__}")


def opencode_proxy_command(
    host: str = typer.Option("127.0.0.1", "--host", help="Loopback bind host"),
    port: int = typer.Option(8765, "--port", "-p", help="Loopback bind port"),
):
    """Run the text-only OpenAI-compatible OpenCode session proxy."""
    import uvicorn

    from machina.core.provider.opencode_proxy import create_app

    if host not in {"127.0.0.1", "localhost", "::1"}:
        console.print("[red]opencode-proxy is loopback-only[/red]")
        raise typer.Exit(1)
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
):
    """Start the Machina HTTP API server."""
    import uvicorn

    from machina.update import auto_update

    auto_update()
    console.print("[bold]Machina API[/bold]")
    console.print(f"  Host: {escape(host)}")
    console.print(f"  Port: {port}")
    console.print(f"  Reload: {'enabled' if reload else 'disabled'}")
    console.print()

    url = f"http://{host}:{port}"

    from machina.db.lifecycle import acquire as acquire_db_lease
    from machina.db.lifecycle import release as release_db_lease

    db_lease = acquire_db_lease("serve")
    try:
        try:
            with output.spinner("Starting server..."):
                from machina.api.app import create_app

                create_app()
        except Exception as e:
            output.error(f"Failed to start server: {e}")
            raise typer.Exit(1) from None

        console.print(f"[green]Starting server at {escape(url)}[/green]")
        console.print("[dim]Press Ctrl+C to stop[/dim]")
        console.print()

        try:
            uvicorn.run(
                "machina.api.app:create_app",
                host=host,
                port=port,
                reload=reload,
                factory=True,
            )
        except KeyboardInterrupt:
            console.print("\n[dim]Server stopped.[/dim]")
    finally:
        release_db_lease(db_lease)


def stop_all() -> None:
    """Stop Machina clients in order, then stop the managed database.

    Order is deliberate: HTTP server first, TUI/gateway second, PostgreSQL
    last. A client releases its database lease during normal shutdown; the
    container is stopped only when no live Machina client remains.
    """
    import signal
    import subprocess
    import time
    from contextlib import suppress

    from machina import host as host_mod
    from machina.db import lifecycle

    try:
        host_state = host_mod.stop_host()
    except host_mod.HostError as error:
        console.print(
            f"[yellow]Could not stop the Machina host shell: {escape(str(error))}[/yellow]"
        )
    else:
        if host_state is not None:
            console.print(f"Stopped Machina host and tmux session {escape(host_state.session)}.")

    me = os.getpid()
    clients = [item for item in lifecycle.live_clients() if int(item["pid"]) != me]
    if not clients:
        stopped = lifecycle.stop_if_idle()
        console.print("[yellow]No running Machina clients found.[/yellow]")
        console.print("Database container stopped." if stopped else "Database was left running.")
        return

    def alive(pid: int) -> bool:
        return lifecycle._alive(pid)  # shared existence probe, no process signal

    def wait_gone(pids: set[int], timeout: float = 10.0) -> set[int]:
        deadline = time.monotonic() + timeout
        remaining = set(pids)
        while remaining and time.monotonic() < deadline:
            remaining = {pid for pid in remaining if alive(pid)}
            if remaining:
                time.sleep(0.15)
        return remaining

    def graceful(pid: int) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                capture_output=True,
                check=False,
            )
        else:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)

    def force(pid: int) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)

    # Server first, TUI/gateway second, database last.
    for role in ("serve", "tui"):
        pids = {
            int(item.get("child_pid") or item["pid"]) for item in clients if item["role"] == role
        }
        if not pids:
            continue
        console.print(f"Stopping {role} client(s): {', '.join(map(str, sorted(pids)))}")
        for pid in pids:
            graceful(pid)
        remaining = wait_gone(pids)
        for pid in remaining:
            force(pid)
        remaining = wait_gone(remaining, timeout=3.0)
        if remaining:
            console.print(f"[yellow]Client(s) did not exit: {sorted(remaining)}[/yellow]")

    # Give normal lease release a final chance, then stop only if idle.
    deadline = time.monotonic() + 5.0
    while lifecycle.live_clients() and time.monotonic() < deadline:
        time.sleep(0.15)
    stopped = lifecycle.stop_if_idle()
    console.print("Database container stopped." if stopped else "Database was left running.")


def config_show():
    """Show current configuration."""
    output.config_table(config.to_dict())


def config_set(
    key: str = typer.Argument(..., help="Config key to set"),
    value: str = typer.Argument(..., help="New value"),
    persist: bool = typer.Option(False, "--persist", "-p", help="Save to config file"),
):
    """Set a configuration value."""
    if key.lower() in SECRET_KEYS:
        console.print(f"[red]'{escape(key)}' is a secret; secrets never go into config.[/red]")
        console.print(
            "[dim]Use `machina setup` or the UI's /keys command to store it in .env.[/dim]"
        )
        raise typer.Exit(1)
    if key not in config.to_dict():
        console.print(f"[red]Unknown config key: {escape(key)}[/red]")
        console.print(f"[dim]Available keys: {', '.join(config.to_dict().keys())}[/dim]")
        raise typer.Exit(1)

    try:
        config.set(key, value, persist=persist)
    except ValueError as error:
        output.error(str(error))
        raise typer.Exit(1) from None
    console.print(f"[green]Set {escape(key)} = {escape(str(value))}[/green]")
    if persist:
        console.print("[dim]Config saved to file.[/dim]")
        if key in ("model", "provider"):
            # MACHINA_MODEL/MACHINA_PROVIDER shadow the file (Config.get
            # checks the environment first) — sync them like the gateway.
            # F09: sync the *validated value just assigned*, never config.get,
            # which still prefers a pre-existing env shadow and would write the
            # old value back. Confirm the effective value only after the shadow
            # has been updated.
            _sync_env_shadow(key, str(validate_value(key, value)))
            console.print(f"[dim]Effective {escape(key)}: {escape(str(config.get(key)))}[/dim]")


def _uninstall_app_root() -> Path:
    """Checkout root this ``machina`` binary was installed from."""
    return Path(__file__).resolve().parent.parent.parent


def _is_source_checkout(path: Path) -> bool:
    """True when *path* is a source tree, not an install directory.

    Only a tree carrying VCS metadata or the project manifest may be called
    a checkout and deleted wholesale; a pip ``site-packages`` directory must
    never be offered as one.
    """
    try:
        return (path / ".git").exists() or (path / "pyproject.toml").is_file()
    except OSError:
        return False


def _filter_path_entries(path_value: str, remove: list[str]) -> str:
    """Return PATH with every entry in ``remove`` dropped.

    Comparison is case-insensitive with trailing separators stripped, so
    ``C:\\x\\.venv\\Scripts`` matches ``c:\\x\\.venv\\Scripts\\``.
    """
    gone = {r.rstrip("/\\").lower() for r in remove}
    return ";".join(p for p in path_value.split(";") if p.rstrip("/\\").lower() not in gone)


def _shim_points_at(shim_text: str, app_root: str) -> bool:
    """True when a ``~/.local/bin/machina`` shim execs an ``machina`` inside ``app_root``.

    Whole path segments only, case-insensitively: ``/x/machina`` never matches a
    shim pointing at a sibling checkout such as ``/x/machina-old``.
    """
    root = app_root.rstrip("/\\").lower()
    text = shim_text.lower()
    for at in range(len(text) - len(root) + 1):
        if not text.startswith(root, at):
            continue
        end = at + len(root)
        if not any(c.isalnum() or c in "-._~" for c in text[at - 1 : at] + text[end : end + 1]):
            return True
    return False


def _docker_container_exists(name: str = "machina-db") -> bool:
    """True when a docker container called *name* exists (running or stopped)."""
    if shutil.which("docker") is None:
        return False
    try:
        import subprocess

        out = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if out.returncode != 0:
        return False
    return any(line.strip() == name for line in out.stdout.splitlines())


def _remove_docker_container(name: str = "machina-db") -> tuple[bool, str]:
    """Stop and remove the docker container (its data goes with it)."""
    import subprocess

    for action in (["docker", "stop", name], ["docker", "rm", name]):
        try:
            out = subprocess.run(action, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            return False, f"docker {' '.join(action[1:])} failed ({e})"
        if out.returncode != 0 and "No such container" not in (out.stderr or ""):
            return False, f"docker {' '.join(action[1:])} failed: {(out.stderr or '').strip()}"
    return True, f"container {name} stopped and removed (its data is gone)"


def _brew_postgres_present() -> bool:
    """True when Homebrew's postgresql@16 is installed (macOS installer path)."""
    import subprocess
    import sys

    if not sys.platform.startswith("darwin") or shutil.which("brew") is None:
        return False
    try:
        out = subprocess.run(
            ["brew", "--prefix", "postgresql@16"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def _remove_brew_postgres() -> tuple[bool, str]:
    """Stop the brew service and uninstall postgresql@16 + pgvector."""
    import subprocess

    for action in (
        ["brew", "services", "stop", "postgresql@16"],
        ["brew", "uninstall", "postgresql@16", "pgvector"],
    ):
        try:
            out = subprocess.run(action, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as e:
            return False, f"{' '.join(action)} failed ({e})"
        if out.returncode != 0:
            return False, f"{' '.join(action)} failed: {(out.stderr or out.stdout or '').strip()}"
    return True, "brew postgresql@16 + pgvector uninstalled (its data is gone)"


def _valid_db_identifier(name: str) -> bool:
    """Conservative identifier check so DROP DATABASE can't escape its quoting."""
    import re

    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$", name or ""))


def _split_database_dsn(dsn: str) -> tuple[str, str] | None:
    """Split a Postgres DSN into (maintenance_dsn, dbname) for DROP DATABASE.

    Returns None when the URL doesn't parse or names a protected database.
    """
    from urllib.parse import urlparse

    try:
        parts = urlparse(dsn)
    except ValueError:
        return None
    dbname = (parts.path or "").lstrip("/")
    if not _valid_db_identifier(dbname) or dbname in ("postgres", "template0", "template1"):
        return None
    maintenance = parts._replace(path="/postgres").geturl()
    return maintenance, dbname


async def _drop_database(maintenance_dsn: str, dbname: str) -> None:
    """DROP DATABASE on the maintenance connection. Raises on failure."""
    import asyncpg

    conn = await asyncpg.connect(maintenance_dsn, timeout=15)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    finally:
        await conn.close()


def _remove_windows_user_path_entries(remove: list[str]) -> tuple[bool, str]:
    """Drop entries from the HKCU ``Path`` value. Returns (changed, detail)."""
    if os.name != "nt":
        return False, "not Windows, skipping registry edit"
    try:
        import winreg
    except ImportError as e:
        return False, f"winreg unavailable ({e})"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE
        ) as key:
            try:
                current, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return False, "no user Path value found"
            updated = _filter_path_entries(current, remove)
            if updated == current:
                return False, "no matching entries in user Path"
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, updated)
    except OSError as e:
        return False, f"registry edit failed ({e})"
    # Best-effort: tell Explorer/new processes the environment changed.
    try:
        import ctypes

        HWND_BROADCAST, WM_SETTINGCHANGE = 0xFFFF, 0x1A
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 0x0002, 5000, None
        )
    except Exception:  # broadcast is cosmetic
        pass
    return True, "removed from user Path (takes effect in new terminals)"


def uninstall():
    """Uninstall Machina — answers yes to everything and nothing is left.

    Asks one yes/no question per item (PATH registration, virtualenv, .env,
    local config, docker container, Postgres database, Homebrew postgres,
    checkout) and removes everything you confirm. The checkout question only
    appears for a real source tree (``.git`` or ``pyproject.toml``); a pip
    install directory is never deleted wholesale.
    """
    app_root = _uninstall_app_root()
    venv_dir = app_root / ".venv"
    from machina.security.env_file import find_env_file, read_env_values

    try:
        env_file = find_env_file()
    except Exception:  # fall back to the checkout .env
        env_file = app_root / ".env"
    shim = Path.home() / ".local" / "bin" / "machina"
    from machina.core.config import default_config_path

    local_config = default_config_path().parent

    # Discover what's actually there so we only ask about real things.
    has_docker = _docker_container_exists()
    has_brew_pg = _brew_postgres_present()
    maintenance_dsn: str | None = None
    dbname = ""
    if env_file.is_file():
        for key in ("DATABASE_URL",):
            dsn = read_env_values(env_file).get(key, "")
            if dsn:
                split = _split_database_dsn(dsn)
                if split is not None:
                    maintenance_dsn, dbname = split
                break

    console.print("[bold]What should go?[/bold] (yes/no)")
    remove_path = typer.confirm("Remove PATH registration (user Path + shim)?", default=True)
    remove_venv = typer.confirm(f"Delete the virtualenv ({venv_dir})?", default=False)
    remove_env_file = typer.confirm(f"Delete the env file ({env_file})?", default=False)
    remove_config = typer.confirm(f"Delete local config ({local_config})?", default=False)
    remove_docker = (
        typer.confirm(
            "Stop and remove the machina-db docker container (its data too)?",
            default=False,
        )
        if has_docker
        else False
    )
    drop_db = (
        typer.confirm(
            f'DROP the Postgres database "{dbname}" (all sessions/memories/jobs)?',
            default=False,
        )
        if maintenance_dsn
        else False
    )
    remove_postgres = (
        typer.confirm("Uninstall Homebrew postgresql@16 + pgvector entirely?", default=False)
        if has_brew_pg
        else False
    )
    # Checkout deletion is only offered for a real source tree; site-packages
    # (or any other install directory) is never a "checkout" to remove.
    delete_checkout = False
    if _is_source_checkout(app_root):
        delete_checkout = typer.confirm(f"Delete the whole checkout ({app_root})?", default=False)
    else:
        output.muted(
            f"Checkout deletion skipped: {app_root} is not a source checkout "
            "(no .git or pyproject.toml)."
        )
    if not remove_path and not any(
        (
            remove_venv,
            remove_env_file,
            remove_config,
            delete_checkout,
            remove_docker,
            drop_db,
            remove_postgres,
        )
    ):
        console.print("[dim]Nothing selected.[/dim]")
        raise typer.Exit(0)

    path_targets = [str(venv_dir / "Scripts"), str(venv_dir / "bin")]
    plan: list[str] = []
    if remove_path:
        plan.append("Remove PATH registration (user Path entry + ~/.local/bin/machina shim)")
    if remove_venv and venv_dir.is_dir():
        plan.append(f"Delete {venv_dir}")
    if remove_env_file and env_file.is_file():
        plan.append(f"Delete {env_file}")
    if remove_config and local_config.exists():
        plan.append(f"Delete {local_config}")
    if remove_docker and has_docker:
        plan.append("Stop + remove docker container machina-db (with its data)")
    if drop_db and maintenance_dsn:
        plan.append(f'DROP DATABASE "{dbname}"')
    if remove_postgres and has_brew_pg:
        plan.append("Uninstall Homebrew postgresql@16 + pgvector")
    if delete_checkout:
        plan.append(f"Delete checkout {app_root}")

    console.print("[bold]Uninstall plan:[/bold]")
    for item in plan:
        console.print(f"  • {escape(item)}")

    # 1. PATH: Windows registry + shim file (created by install.sh).
    if remove_path:
        changed, detail = _remove_windows_user_path_entries(path_targets)
        output.status_line("PATH", detail, "success" if changed else "warning")
        if shim.is_file():
            try:
                points_here = _shim_points_at(
                    shim.read_text(encoding="utf-8", errors="replace"), str(app_root)
                )
            except OSError:
                points_here = False
            if points_here:
                try:
                    shim.unlink()
                    output.status_line("Shim", f"deleted {shim}", "success")
                except OSError as e:
                    output.error(f"Could not delete shim {shim}: {e}")
            else:
                output.muted(f"Shim {shim} points elsewhere, leaving it.")
        else:
            output.muted("No ~/.local/bin/machina shim found.")
    else:
        output.muted("Keeping PATH registration.")

    # 2. Docker + database before files (drop-db needs the DSN from .env).
    if remove_docker:
        if has_docker:
            ok, detail = _remove_docker_container()
            output.status_line("Docker", detail, "success" if ok else "error")
        else:
            output.muted("No machina-db container found.")
    if drop_db:
        if maintenance_dsn:
            try:
                _run(_drop_database(maintenance_dsn, dbname))
                output.status_line("Database", f'dropped "{dbname}"', "success")
            except Exception as e:
                output.error(f'Could not drop database "{dbname}": {e}')
        else:
            output.muted("No droppable DATABASE_URL found in .env.")
    if remove_postgres:
        if has_brew_pg:
            ok, detail = _remove_brew_postgres()
            output.status_line("Postgres", detail, "success" if ok else "error")
        else:
            output.muted("No Homebrew postgresql@16 found.")

    # 3. Files.
    def _rmtree(path: Path, label: str) -> None:
        try:
            shutil.rmtree(path, ignore_errors=False)
            output.status_line(label, f"deleted {path}", "success")
        except Exception as e:
            output.error(f"Could not delete {path}: {e}")

    if remove_venv and venv_dir.is_dir():
        _rmtree(venv_dir, "Venv")
    if remove_env_file and env_file.is_file():
        try:
            env_file.unlink()
            output.status_line("Env file", f"deleted {env_file}", "success")
        except OSError as e:
            output.error(f"Could not delete {env_file}: {e}")
    if remove_config and local_config.exists():
        if local_config.is_dir():
            _rmtree(local_config, "Config")
        else:
            try:
                local_config.unlink()
                output.status_line("Config", f"deleted {local_config}", "success")
            except OSError as e:
                output.error(f"Could not delete {local_config}: {e}")

    # 4. Checkout last — the running `machina` binary lives inside it, so on
    # Windows the delete can fail with a file lock. Report leftovers + manual cmd.
    if delete_checkout:
        if not _is_source_checkout(app_root):
            output.error(
                f"Refusing to delete {app_root}: no .git or pyproject.toml (not a source checkout)."
            )
        else:
            try:
                shutil.rmtree(app_root, ignore_errors=False)
                output.status_line("Checkout", f"deleted {app_root}", "success")
            except Exception as e:  # locked running exe on Windows
                output.error(f"Could not delete checkout {app_root}: {e}")
                if os.name == "nt":
                    console.print(
                        f'[dim]Close this terminal, then run: Remove-Item -Recurse -Force "{app_root}"[/dim]'
                    )
                else:
                    console.print(f'[dim]Then run: rm -rf "{app_root}"[/dim]')

    console.print()
    output.success("Uninstall complete. Open a new terminal for the PATH change to take effect.")
