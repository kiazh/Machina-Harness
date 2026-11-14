# Kiro account through a local proxy

Machina's `kiro` provider uses your **Kiro account login**, not a Kiro API
key. A local OpenAI-compatible gateway reads the credentials that
`kiro-cli login` stored on this machine, authenticates to Kiro upstream, and
exposes `/v1/models` and `/v1/chat/completions` on loopback. Machina talks to
that gateway.

This is a **model-provider path, not MCP**. The gateway only carries model
requests; Machina's harness still owns the session, tools, approvals, and
tool execution. From Machina's perspective Kiro is another
OpenAI-compatible model endpoint.

> **Status: community interoperability, not an AWS-supported integration.**
> Kiro's terms prohibit third-party automation harnesses that route requests
> outside its native interfaces. A working proxy does not grant permission;
> review the risk before using it. Prefer the official Kiro CLI/ACP path when
> it meets your needs. Machina does not implement the Kiro login and does not
> bundle or auto-install the proxy.

## 1. Sign in to Kiro

Install the official Kiro CLI and sign in:

```powershell
kiro-cli login
# On a headless/remote machine:
# kiro-cli login --use-device-flow
```

The gateway reads the account tokens from the CLI's local database. Confirm
that the database exists without printing its contents:

```powershell
# Windows:
Test-Path "$env:LOCALAPPDATA\Kiro-Cli\data.sqlite3"
# macOS/Linux:
# test -f "$HOME/.local/share/kiro-cli/data.sqlite3"
```

Kiro IDE login alone is not the same as a Kiro CLI login. If the database is
absent, run `kiro-cli login` first.

## 2. Run the OpenAI-compatible gateway

Use [`jwadow/kiro-gateway`](https://github.com/jwadow/kiro-gateway). The Node
`kiro-claude-proxy` exposes Anthropic `/v1/messages`, which is not this
provider's transport.

```powershell
git clone https://github.com/jwadow/kiro-gateway.git
cd kiro-gateway
git rev-parse HEAD   # record the reviewed revision
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set the gateway's `.env` to a loopback-only configuration. `PROXY_API_KEY` is
only a password for local clients. It is **not** a Kiro or AWS API key; the
upstream account token comes from the CLI database.

```dotenv
PROXY_API_KEY=dummy-local-only
KIRO_CLI_DB_FILE=C:/Users/you/AppData/Local/Kiro-Cli/data.sqlite3
# Builder ID / runtime profile ARN. On Windows it is in:
# %APPDATA%\Kiro\User\globalStorage\kiro.kiroagent\profile.json
PROFILE_ARN=arn:aws:codewhisperer:us-east-1:YOUR_ACCOUNT:profile/YOUR_PROFILE
SERVER_HOST=127.0.0.1
SERVER_PORT=8000
ACCOUNT_SYSTEM=false
# Keep the gateway from racing kiro-cli for token write-back.
SQLITE_READONLY=true
DEBUG_MODE=off
FAKE_REASONING=false
AUTO_TRIM_PAYLOAD=false
```

Use the actual database path on your machine. Keep `SERVER_HOST=127.0.0.1` so
the gateway is not exposed to the network. Do not copy Kiro tokens between
machines.

Start it in a separate terminal:

```powershell
python main.py
```

Verify before involving Machina:

```powershell
curl.exe --fail http://127.0.0.1:8000/health
curl.exe --fail http://127.0.0.1:8000/v1/models -H "Authorization: Bearer dummy-local-only"
```

### Start and stop it from Machina

If the checkout is at `~/kiro-gateway` (or another conventional location), or
you set `MACHINA_KIRO_GATEWAY_DIR`, Machina can manage the process for you:

```text
/auth kiro status     # is the CLI login present? is the listener up?
/auth kiro start      # launch the configured checkout and activate Kiro
/auth kiro stop       # stop only a child Machina started
/auth kiro setup      # setup checklist
```

Auto-start is enabled by default. On gateway initialization Machina checks for
a Kiro CLI login database and a detectable launcher, then starts the checkout
in a background thread. This is never on the model-picker path and never blocks
`/models`. An externally started gateway is detected and left running; Machina
only stops a child it owns. Process output and lifecycle events go to
`~/.machina/logs/kiro-gateway.log`. On Windows, Machina launches the child with
`PYTHONIOENCODING=utf-8`; without it the gateway's emoji startup banner can
crash under the legacy console code page before it binds the port.

Auto-start does **not** install or clone the gateway. To disable it:

```dotenv
MACHINA_KIRO_AUTOSTART=false
```

### Usage and fallback

```text
/auth kiro usage      # plan, credits used/limit, remaining, reset date
```

The local gateway's admin endpoint reads Kiro's `GetUsageLimits` through the
same authenticated account session; Machina never duplicates token handling.
A gateway build without `/admin/accounts/usage` reports usage as unavailable;
fallback still works.

Kiro's provider response does not expose prompt-cache read/write counters.
`machina usage` therefore labels Kiro's prompt cache as `unsupported`; a zero
cache-read count means "not reported", not "cache miss". The durable ledger
still records zero rather than inventing a hit rate. (Upstream work is tracked in `jwadow/kiro-gateway`
PR #160; the checkout used here has the equivalent read-only route.)
When credits are exhausted, Machina can move the current chat to a fallback
route. Set it explicitly:

```dotenv
MACHINA_KIRO_FALLBACK=chatgpt:gpt-6.1-sol
```

If unset, Machina auto-picks a signed-in subscription (ChatGPT, Grok, Copilot)
or a configured direct key, and reports when none is available. A quota error
switches the chat session and asks you to resend; the Kiro gateway and account
credentials stay untouched.

Kiro's footer context counter uses the gateway's native
`contextUsagePercentage`/`contextTokens` values, so the token figure matches
Kiro's context accounting instead of the cumulative session total. Other
providers keep their existing cumulative counter.

## 3. Point Machina at the gateway

No Kiro API key is required. Machina sends the documented
`dummy-local-only` local proxy password automatically. The gateway still
needs the CLI database path and `PROFILE_ARN` from step 2.

```dotenv
MACHINA_PROVIDER=kiro
MACHINA_MODEL=auto
# Optional: only if you changed the gateway's PROXY_API_KEY.
# KIRO_API_KEY=your-local-proxy-password
# Optional: only if the gateway is not on 127.0.0.1:8000.
# KIRO_BASE_URL=http://127.0.0.1:8000/v1
# Optional lifecycle (see "Start and stop it from Machina"):
# MACHINA_KIRO_GATEWAY_DIR=C:/Users/you/kiro-gateway
# MACHINA_KIRO_AUTOSTART=true  # set false to manage the process yourself
# Advanced: explicit launcher instead of <dir>/main.py.
# MACHINA_KIRO_GATEWAY_COMMAND='C:/Users/you/kiro-gateway/.venv/Scripts/python.exe C:/Users/you/kiro-gateway/main.py'
```

Then start Machina and run `/models` to load the full Kiro catalog:

```powershell
machina
# inside the TUI:
/models
```

The picker is deliberately fail-fast. A live local gateway answers in
milliseconds and its catalog is cached for 60 seconds; a stopped gateway is
rejected by a bounded TCP preflight instead of holding the concurrently fetched
model menu. The bundled 22-model CLI catalog is always shown, with live gateway
metadata overlaid and unknown future aliases appended. Only `/models catalog`
forces discovery: it bypasses the cache and runs
`kiro-cli chat --list-models --format json`, whose authenticated catalog is
authoritative. Normal picker calls never invoke `kiro-cli`.

The provider is also available through `/provider kiro` and the desktop
Providers page. `/keys` can set the optional `KIRO_API_KEY` and
`KIRO_BASE_URL` values. The offline rows are the full 22-model CLI catalog:

`auto`, `claude-opus-5.5`, `claude-sonnet-5.5`, `claude-haiku-5.5`,
`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `kimi-k3`,
`claude-opus-5`, `claude-sonnet-5`, `claude-opus-4.8`,
`claude-opus-4.7`, `claude-opus-4.6`, `claude-sonnet-4.6`,
`claude-opus-4.5`, `claude-sonnet-4.5`, `claude-haiku-4.5`,
`deepseek-3.2`, `minimax-m2.5`, `minimax-m2.1`, `glm-5`,
`qwen3-coder-next`.

Model availability still depends on the Kiro account tier. Opus aliases and
some newer models may require a paid plan, and a listed pass-through ID is
not proof of entitlement. If a model fails, try `auto` or
`claude-sonnet-4.5`.

## 4. Troubleshooting

| Symptom | Check |
|---|---|
| `Kiro proxy unreachable at http://127.0.0.1:8000/v1` | Run `/auth kiro status`; start with `/auth kiro start` or `python main.py`, and confirm `/health` and the port. |
| `/auth kiro start` says no launcher | Clone the checkout to `~/kiro-gateway` or set `MACHINA_KIRO_GATEWAY_DIR`; auto-start never downloads it. |
| Auto-start did not run | Confirm `kiro-cli login` created the credential database, a checkout is detectable, and `MACHINA_KIRO_AUTOSTART` is not `false`; check `~/.machina/logs/kiro-gateway.log`. |
| `401` from the proxy | `KIRO_API_KEY` must equal the gateway's `PROXY_API_KEY`. The default is `dummy-local-only`. |
| `profileArn is required for this request` | Builder ID accounts have no ARN in the CLI database. Machina injects the ARN from `%APPDATA%\Kiro\User\globalStorage\kiro.kiroagent\profile.json` when it launches the gateway. For an external gateway, set `PROFILE_ARN` in its `.env` and restart it. |
| Upstream `401`/`403`, or model discovery fails after login | The Kiro CLI login expired or the account lacks that model. Run `kiro-cli login` again and check `/v1/models`. |
| Docker deployment cannot reach the proxy | A container's `127.0.0.1` is the container. Set `KIRO_BASE_URL=http://host.docker.internal:8000/v1` (and bind the gateway only to the host loopback/firewall as appropriate). |
| Model list is empty | The gateway is up but the account has no entitled models, or `/v1/models` is failing. Check the gateway log; do not paste tokens into issues. |

The gateway's local password protects loopback access only. Do not expose the
gateway on `0.0.0.0` without adding authentication and network controls.
