# Hermes upstream through its local proxy

Hermes ships a loopback OpenAI-compatible server (`hermes proxy start`) that
forwards requests to an OAuth-authenticated upstream: **Nous Portal** (`nous`)
or **xAI Grok** (`xai`). Machina detects the installed Hermes CLI on macOS,
Linux, and Windows, starts that proxy on launch when an upstream is already
signed in, and exposes its `/v1/models` catalog as the `hermes` provider.

Machina does not bundle Hermes, perform its login, or manage a proxy it did
not start. Older installations that saved the loopback proxy as a user-added
`hermes-proxy` provider are folded into the built-in `hermes` route on startup,
so `/models` shows one current Hermes group rather than a stale cached catalog.

## 1. One click: Setup

In the TUI:

```text
/login hermes
```

Choose **Set up Hermes (install + sign in + start)**. That one action:

1. Detects Hermes; if it is missing, runs the official installer for this OS
   (`install.sh` on macOS/Linux, `install.ps1` on Windows).
2. Opens the Hermes sign-in window when an upstream is not authorized.
3. Waits for `hermes proxy status` to report ready, then starts the proxy.

From a terminal, the same one action is:

```bash
machina auth hermes setup
```

The only human step is the unavoidable third-party OAuth consent in the
browser window that Setup opens. No clone, venv, `requirements.txt`, `.env`,
or follow-up command is required.

Check state at any time:

```bash
machina auth hermes status
```

## 2. Automatic startup

After setup, Machina checks for `hermes`/`hermes.exe` on PATH and in the
native install locations on every launch. When an upstream is signed in it
runs:

```bash
hermes proxy start --provider <nous|xai> --host 127.0.0.1 --port 8645
```

and writes stdout/stderr to `~/.machina/logs/hermes-proxy.log`. If Hermes is
missing or no upstream is signed in, startup stays green and the provider
offers the same Setup action; no turn is failed.

Use it in the TUI:

```text
/provider hermes
/models
```

or on the CLI:

```bash
MACHINA_PROVIDER=hermes machina chat "hello"
machina providers models hermes --refresh
```

## 3. Configuration

All keys are optional.

```bash
# Keep the proxy user-managed instead of autostarting it.
MACHINA_HERMES_PROXY_AUTOSTART=false

# Change the loopback port when 8645 is occupied.
MACHINA_HERMES_PROXY_PORT=8645

# Pin an upstream instead of choosing the first ready one.
MACHINA_HERMES_PROXY_PROVIDER=auto   # auto | nous | xai

# Only needed when you front the proxy with your own token check.
# HERMES_PROXY_API_KEY=
```

The proxy accepts any bearer token by design; it attaches the OAuth
credentials itself, so Machina sends a local placeholder unless
`HERMES_PROXY_API_KEY` is set.

## 4. Troubleshooting

| Symptom | Check |
|---|---|
| `Hermes is not installed` | Run `/login hermes` and choose Setup (or `machina auth hermes setup`). It runs the official installer automatically; desktop shortcuts do not need `~/.local/bin` on PATH because Machina checks that location directly. |
| `no proxy upstream is signed in` | Run `hermes portal` (Nous) or `hermes auth` (xAI), then `hermes proxy status`. Restart Machina or refresh models after signing in. |
| Proxy starts but models are empty | Run `machina providers models hermes --refresh`; check `~/.machina/logs/hermes-proxy.log`. |
| Port already in use | Another proxy is listening on 8645. Set `MACHINA_HERMES_PROXY_PORT` to a free loopback port. |
| Machina did not stop the proxy | Expected when the proxy was started outside Machina; Machina only stops a process it launched. Use `hermes proxy` controls for external instances. |
| Windows: window flashes at startup | Machina launches the proxy hidden (`CREATE_NO_WINDOW`) and in its own process group; if the CLI is a `.cmd` shim, ensure the installing user's PATH includes it. |
