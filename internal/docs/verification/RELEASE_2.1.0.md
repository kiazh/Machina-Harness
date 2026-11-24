# Machina 2.1.0

The Hermes-proxy and Kiro-launcher follow-up to 2.0.1.

## Added

- **One-click Setup.** `/auth hermes` and `machina auth hermes setup` install
  the official Hermes CLI when missing, open the OAuth consent window, then
  start the proxy as soon as an upstream is signed in. `/auth kiro setup`
  clones the official gateway checkout, creates its venv, installs its
  requirements, seeds `.env`, and starts it in the background.
- **Automatic Hermes detection.** On every launch Machina looks for the Hermes
  CLI on PATH (including PATHEXT and Windows installer locations) and in the
  conventional `~/.local/bin`, `~/.hermes/bin`, `~/bin`, and
  `%LOCALAPPDATA%` locations. When `hermes proxy status` reports a signed-in
  upstream, Machina starts `hermes proxy start --provider <nous|xai> --host
  127.0.0.1 --port <port>` with output in `~/.machina/logs/hermes-proxy.log`.
- **`hermes` provider.** The proxy's `/v1/models` catalog is exposed in
  `machina providers models hermes`, the gateway model picker, and
  connected-model routing. The provider is keyless (the proxy attaches the
  OAuth credential) and defaults to `auto`.
- **Lifecycle controls.** `MACHINA_HERMES_PROXY_AUTOSTART` (default true),
  `MACHINA_HERMES_PROXY_PORT` (default 8645), and
  `MACHINA_HERMES_PROXY_PROVIDER` (`auto|nous|xai`). Machina stops only the
  proxy it started.
- **Broader Kiro launcher detection.** Checkouts in `~/Downloads` and
  `~/Desktop` (including `kiro-gateway-main` ZIP extracts) and next to the
  current directory are detected, and `main.py`, `server.py`, `app.py`,
  `run.py`, `kiro_gateway/main.py`, or `src/main.py` are accepted. A missing
  launcher names every accepted entrypoint and points at
  `MACHINA_KIRO_GATEWAY_COMMAND`.

## Fixed

- `/models` no longer fails with `Cannot read properties of undefined
  (reading 'replace')`. Hermes catalog rows now use the shared gateway shape
  (`id`/`displayName`/...), and the picker coerces fields and drops empty rows
  so an older/mismatched provider payload cannot crash rendering.
- `/auth` paints its menu immediately. Kiro/Hermes status probes (a TCP/CLI
  check and a `hermes proxy status` subprocess) run only after choosing a row
  or for the explicit `/auth status` card.
- Hermes start is serialized so startup detection and a provider-factory call
  cannot kill and respawn each other; a live Machina-owned child is adopted.
- The parent closes the proxy log descriptor after `Popen`, so repeated
  restarts no longer leak a handle.
- Hermes readiness reports the exact sign-in command instead of a generic
  unavailable provider.
- `HERMES_PROXY_API_KEY` is registered as an optional secret and resolved
  through the shared environment/`*_FILE`/backend chain.

## Verification

- Repository smoke suite: `706 passed, 18 skipped`.
- UI suite: `1,262 passed, 6 skipped`; typecheck and lint clean.
- Ruff, format, and the external architecture/provider ratchets clean.
- External suite: `6,548 passed, 13 skipped, 2 xfailed` against a real
  PostgreSQL 16 + pgvector instance with Python 3.11. The public-URL SSRF
  test now stubs resolution, so blocked/hanging DNS can no longer flake it.
- CI remains the authoritative matrix.

## Compatibility

- No database migration.
- New configuration keys are optional and default to the previous behavior
  when Hermes is absent.
- `hermes` is a new built-in provider name and is reserved in the user
  provider store.
