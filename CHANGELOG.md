# Release notes

## 2.1.0 - 2026-11-23

**Hermes proxy detection and a more forgiving Kiro launcher.** See the [2.1.0 release notes](internal/docs/verification/RELEASE_2.1.0.md).

### Added

- One-click Hermes setup (`/auth hermes` or `machina auth hermes setup`): detects the CLI on macOS, Linux, and Windows, runs the official installer when missing, opens the OAuth consent window, then starts the proxy automatically once `hermes proxy status` reports ready.
- `/auth kiro setup` now provisions the gateway instead of printing a checklist: it clones the official checkout into the configured/default directory, creates its private venv, installs `requirements.txt`, seeds `.env`, and starts the proxy in the background.
- The `hermes` provider exposes the proxy's live `/v1/models` catalog in the CLI (`machina providers models hermes`), the gateway model picker, and connected-model routing. `MACHINA_HERMES_PROXY_AUTOSTART`, `MACHINA_HERMES_PROXY_PORT`, and `MACHINA_HERMES_PROXY_PROVIDER` control the lifecycle.
- The runtime reports Hermes readiness with the exact sign-in command when the CLI is installed but no upstream is authorized, instead of failing a turn.
- Kiro gateway detection now accepts `main.py`, `server.py`, `app.py`, `run.py`, `kiro_gateway/main.py`, and `src/main.py`, plus checkouts in `~/Downloads`/`~/Desktop` and next to the current directory. A missing launcher lists every accepted entrypoint and points at `MACHINA_KIRO_GATEWAY_COMMAND`.

### Fixed

- `/models` no longer crashes when a provider returns a differently shaped catalog row (the first Hermes rows used `model` instead of `id`). The gateway emits the shared `id`/`displayName` shape and the picker coerces/drops malformed rows defensively.
- `/auth` opens instantly: the main menu no longer waits on Kiro's TCP/CLI probe or Hermes' `hermes proxy status` subprocess. Status is fetched only after choosing an account, or by `/auth status`.
- Hermes startup is serialized, adopts a live Machina-owned proxy instead of respawning it, and closes the parent log descriptor after spawn (one handle per restart is no longer leaked).
- The Hermes proxy is stopped only when Machina started it; an externally managed proxy is left untouched.
- `HERMES_PROXY_API_KEY` is registered as an optional secret, so a token-protected proxy can be used without exposing the value in `config.yaml`.

## 2.0.1 - 2026-11-22

**Verification and diagnostics patch.** See the [2.0.1 release notes](internal/docs/verification/RELEASE_2.0.1.md).

### Fixed

- Connector ids derived from Windows executable paths now work on every host, not only on Windows.
- The host setup plan follows the inspected host platform, so Windows/WSL autostart and SSH steps are planned consistently from Linux, macOS, and tests.
- Config validation failures now raise a typed `ValidationError` (still `ValueError`-compatible), so the gateway can show the actionable message instead of a bare `ValueError`.
- Invalid `MACHINA_DB_MIN_POOL`, `MACHINA_DB_MAX_POOL`, and `MACHINA_DB_TIMEOUT` values now fail with a named, typed error.
- The external regression suite follows the 2.0 typed `Gateway.call` seam instead of importing the removed private `_RpcGateway`.

### Added

- `machina debug logs` prints the tail of a Machina log file (`gateway.log` by default), so a hidden turn traceback is one command away on Windows, macOS, and Linux.
- Untyped turn failures now point at `machina debug logs` instead of reporting only the exception class.

### Verification

- Repository smoke suite green on macOS.
- External suite updated for the typed gateway seam and run against PostgreSQL + pgvector.
- CI pin advanced to the matching DevTools commit.

## 2.0.0 - 2026-11-22

**The daily-driver major release.** See the [2.0.0 release notes](internal/docs/verification/RELEASE_2.0.0.md).

### Highlights

- OpenCode-parity workflow: snapshots/undo, opt-in formatters, project config, MCP administration, ACP, background service lifecycle, shell completions, authenticated API client, plugins, update, and rollback.
- Runtime improvements: compact prompts, on-demand tool schemas, memory graph, growth/RSI revisions and feedback, deterministic model routing, parallel delegation, steering, `ask_user`, and repair-controller safeguards.
- Provider and integration work: provider-first MCP with OAuth and exact approvals, OIDC, OpenAPI 3.2.1, A2A 1.0, CloudEvents, OTLP traces, SBOMs, and account management for ChatGPT, Grok, Copilot, and Kiro.
- UI and operations: Status tab, command reports rendered as Markdown, 73 deduplicated themes, cross-platform installers, host setup, and release-artifact hygiene.
- Auto-update now handles shallow installer clones, dirty checkouts, local commits, dependency refreshes, re-exec, and rollback.

### Breaking and compatibility changes

- The built-in `opencode_session` provider and `opencode-proxy` command were removed.
- Multi Auto is the default routing mode; explicit model/provider pins remain authoritative.
- The `onedark` alias and `catppuccin-macchiato` theme were removed; `one-dark` and the remaining Catppuccin variants stay.
- Internal evidence, audits, parity notes, and evaluation tooling moved under `internal/` and are excluded from release artifacts.

### Full change list

- Repository hygiene: moved engineering evidence, audits, parity notes, benchmark/evaluation tooling, and research fixtures under `internal/`, and excluded that tree from wheel, sdist, and Docker build contexts. No runtime behavior changed.
- Added project instruction loading from global and workspace `AGENTS.md`, platform-aware macOS/Windows keybinding fallbacks and labels, OpenAPI 3.2.1 with protected docs, opt-in CloudEvents SSE envelopes, A2A 1.0 agent-card and task bridge, read-only `machina audit`, optional OTLP/HTTP trace export, and a CycloneDX 1.7 release SBOM.
- Added optional OIDC resource-server authentication (`.[oidc]` extra) with issuer/JWKS verification, API-key fallback, and OIDC principal binding for approvals.
- Added a persistent temporal/entity memory graph with deterministic extraction, relation edges, bounded graph expansion, and the same agent/category/quarantine/date filters as flat retrieval.
- Reduced the always-on prompt: ordinary turns use a compact capability brief instead of the full feature manual, and provider tool schemas project bounded prose while preserving every parameter, type, default, enum, and range. Windows compound commands now auto-select Git Bash when installed (avoiding failed POSIX-style `;`/`ls` probes), and the system prompt tells the agent to run focused tests without output pipes and report immediately after verification. Factory agents now start with a core tool surface plus `tool_load`; the compact catalog loads missing schemas on demand. The per-turn skill query-expansion model call is now opt-in (`MACHINA_SKILL_QUERY_EXPANSION_ENABLED=0` by default), and a schema-version marker skips repeated runtime DDL after the first successful upgrade. The measured Machina exact-answer path is 2,923 prompt tokens / ~3.3s, and the code-repair path is ~18.2k prompt tokens / ~12.1s (median of five trials), with all code trials verified. Once a repair edit has passing verification evidence, the next model step is forced tool-free so the agent reports instead of rerunning tests or inspecting diffs.
- Added gVisor (`--runtime runsc`), Kata (`--runtime kata-runtime`), Kata-Firecracker, and WASI (`wasmtime`) sandbox backends, including configurable runtime names and a no-network module contract for WASI.
- Added a lazy console entry point: `--help` renders a static command table and invoking a command imports only its feature module. Added reusable evaluation gates for Promptfoo, Ragas, DeepEval, and saved metric baselines, plus keyless Sigstore signing and SLSA build provenance on tagged releases.
- Performance cleanup: `/mcp`/CLI provider imports are lazy, `machina --version` bypasses the Typer stack, status-tab slow sources refresh every 15s instead of every 3s, tool arguments/shell rows are bounded, unchanged now-playing polls no longer repaint, and transcript readers no longer fetch discarded embeddings. Also removed the duplicate `/overview` command, fixed the `/settings` alias shadow, removed dead UI helpers, and added release gates for editor backup files.
- Removed the built-in `opencode_session` provider and `opencode-proxy` command: the local OpenCode session bridge is text-only, so it is no longer exposed in the `/model` catalog. Grouped model pickers now retain a provider heading for every group that has a matching row during search, including the `Hermes proxy` category.
- Queued guidance now behaves like steering at the next safe boundary without cancelling already-running tools: the model keeps the current task context, incorporates the new instruction, and replans. Added the `ask_user` tool and `question.asked` / `question.answer` flow for inline multiple-choice questions with an optional custom answer.
- Firecrawl's default connector now uses the hosted MCP OAuth endpoint (`https://mcp.firecrawl.dev/v2/mcp-oauth`) instead of assuming a local bridge on port 3000. The menu shows the full cached catalog rather than stopping at 20 rows, and Composio/Composio-app tool slugs are parsed into familiar `Gmail · Fetch emails`, `GitHub · Create an issue`, and `Google Drive · List files` labels with markdown-free descriptions.
- Connected MCP connector IDs and cached tool names now enter the model's runtime context, and `@provider` mentions route normal prompts to `mcp_list` / `mcp_describe` / `mcp_call` instead of being treated as unavailable. Wired `mcp_call` tool rows render as `@vercel · List projects` (never the raw namespaced wire name). A command-in-flight input guard keeps `/mcp` loading from stacking slash autocomplete under the opening menu.
- Added a full-page **Status** tab: `Ctrl+N` then ←/→ instantly swaps between Chat and Status; the page shows active agents, shell jobs, recent tools/MCP calls, MCP servers, external harnesses and queued work. `/activity` opens it, and `/activity list` retains the actionable menu. Leader-key releases/repeats and Ctrl+T auto-repeat are handled without dropping the pending shortcut. The page now uses a grouped activity list with a selected-item inspector, selectable rows with expandable local logs for sessions, agents, shells, tools, MCP servers and harnesses, clickable filter tabs, and click-to-expand rows.
- Escape now requires two deliberate presses to stop a running reply (the first shows an armed hint); Ctrl+C remains the immediate stop path.
- Added `Ctrl+Shift+E` as a browser-safe file-explorer toggle; Chrome/Edge reserve `Ctrl+T` (new tab) so the page never receives it. The far-right explorer grip and `/explorer` remain available. Also optimized host SSH/firewall inspection and made all tmux targets exact so starting/stopping a host cannot prefix-match and kill another session. Installing a Windows client key now disables SSH password authentication, the SSH firewall also covers the Tailscale IPv6 range, and Cloudflare guidance requires a named Tunnel with Access instead of a public quick tunnel.
- Added the `/host` command and host gateway methods: one-shot setup/repair, live setup progress, status, browser credentials, client instructions, start/stop/restart, advanced options, client SSH key onboarding, and restart-safe resume. On Windows the setup installs or repairs a WSL2 runtime, Tailscale, OpenSSH restricted to the tailnet, a logon entry, and the `ttyd` browser bridge; on Linux/macOS it uses native tmux. Client instructions cover macOS, Windows, and Linux. Starting the host asks whether to host the current Machina session (preserving the conversation, and mirroring the terminal when already in tmux) or start a new one, and the choice can change on every start without rerunning setup. `/host` also reuses an existing tmux session, and `machina host stop` leaves a borrowed session running. See `docs/REMOTE_HOST.md`.
- Added `machina host`: a persistent tmux shell running the terminal UI with a loopback-only, token-protected `ttyd` browser bridge, plus `start`, `status`, `url`, `attach`, and `stop` commands. The gateway remains local to the host; reach the shell through SSH forwarding, Tailscale, or Cloudflare Access.
- `/mcp` now opens the centered, searchable overlay used by `/keys` instead of the bottom picker. Curated providers are headers with clean provider-relative tool labels and `ready` / `approval` / `blocked` status; no `needs_approval` wire spelling is shown. Selecting a tool prefills an explicit `@provider` request, configured authorized HTTP servers reconnect in the background at startup, and the menu reads cached catalogs without contacting providers. There is no separate Connect, Disconnect or Logout action: an unauthorized provider offers **Authorize**, and **Remove server** is the complete cleanup. `/mcp <url>` and the `mcp_connect` tool still accept a pasted link without a name, deriving the id from the URL.
- Fixed menu section headers being dropped by the app's display sanitization, which had turned grouped picker headings into selectable rows.
- Full-repository audit: fixed `machina doctor`'s launcher import, removed a deleted UI test from CI and added a path guard, made the sdist include shared contracts while excluding the review archive (8.99 MB -> 2.47 MB), tightened the Docker build context, stopped treating `0.0.0.0` as loopback, stopped now-playing/timers and released editor caches on gateway exit/replaced previews, deleted zero-caller UI helpers and duplicate generated events, and recorded the remaining architecture/performance backlog in `internal/docs/audits/2026-11-16-full-repo-audit.md`.
- Every turn now carries a generated capability index covering all surfaces, mechanisms, built-in tools, providers, and vocabularies, plus the exact callable set; the new read-only `harness_catalog` tool returns the full detailed inventory, and a local test pins it to the live registries.
- Cached update checks now report their last result on every launch (for example, "Machina is up to date. Last checked 3m ago") instead of staying silent for 15 minutes.
- Launch update status is no longer hidden by unrelated `git fetch` calls; only Machina's own check marker counts toward the 15-minute cache.
- Interactive launches now show an animated update status (checking, updating, refreshing dependencies, or up to date) before the UI opens.
- Added deterministic connected-model routing across every credentialed provider: role- and task-specific planner/supervisor/worker/utility hierarchies, single-model route copying, multi-model role selection, cost tiers, route pins, fallbacks, `machina route`/`machina routes`, and in-chat `route_explain`/`model_inventory`.
- Made **Multi Auto** the default routing mode and moved the Multi Auto / Single Model switch to `/routing mode`. Selecting it writes `orchestration_mode=multi_model`, `model_selection=auto`, and automatic explicit-parallel delegation; the current model remains the session/planner anchor. Bare `/routing` opens the same picker, `/models` keeps a visible routing-mode shortcut, and `/routing <prompt>` remains the explanation surface. Added `Ctrl+N` then `T` as an explorer fallback when a terminal captures `Ctrl+T`.
- Stopped browser-verification loops: `web_verify`/`browser_verify` now return a failure-first report under the tool cap (failed checks + counts, not every passing check), artifact pages and verification results get their full bounded budget instead of a truncated 1,000-char preview, and artifact paging gets one nudge even outside repair tasks. An empty model response with no tool calls now retries once and then fails explicitly instead of silently completing.
- Added a validated parallel delegation plan: explicit "in parallel" requests in `multi_model` mode (or `single_model` with `single_model_auto_parallel=true`) are planned once, JSON-validated (cycles, duplicate artifact ownership, task count), and executed by a dependency-aware first-wave scheduler up to `worker_count` (0=auto, 20+ supported, 64 cap). At most `max_worker_routes` worker routes are reused to preserve cache and review coherence; the selected supervisor route performs a bounded final synthesis with a deterministic fallback, and paused workers are reported as awaiting approval rather than completed.
- Added `worker_transport=native|proxy|mcp`; MCP plans run through an installed OpenCode/Codex/Hermes harness and report each task's route/status. Worker count and concurrency defaults now support eight active workers (64 cap).
- Added `opencode_session`: a text-only local bridge to OpenCode's v2 session API, including free Zen models that direct HTTP rejects, with real cache-read/write usage mapping and `machina opencode-proxy` for an OpenAI-compatible loopback facade. Tool-using work is explicitly kept on native/proxy or MCP routes.
- Added real headless-browser verification (`browser_verify`, `web_verify`) for loopback web artifacts: DOM assertions, clicks/forms, reload/localStorage, viewport changes, failed resources, console and page errors. Successful browser evidence now counts for repair/web-build verification gates.
- Added a cached live capability inventory for installed harnesses (`harness_features`): available OpenCode models/agents/auth/MCP, Codex MCP/plugins, Hermes MCP, with staleness and no secrets persisted. OpenCode's entitlement-aware model list now feeds the session bridge inventory instead of the public catalog.
- `machina usage` now reports prompt-cache capability separately from counters. Kiro is labeled `unsupported` (zero reads mean "not reported", not a miss); OpenCode session cache reads/writes are reported with uncached-input accounting.
- Extended the direct-provider HTTP timeout to five minutes so reasoning models are not aborted mid-thought, and made `harness_status` distinguish its read-only inspection effect from the session's write permission.
- Fixed the end-to-end Kiro workflow: explicit CLI provider/model choices now pin to the session, delegated children inherit the parent route when the specialist does not override it, and empty post-tool model responses retry with a fresh tool-free summary or a tool-enabled continuation instead of ending with no answer.
- When a request explicitly asks for parallel independent tracks, the default delegation policy now prefers two disjoint workers rather than collapsing the work into a serial plan; subagents are told to use workspace-relative paths.
- Raised the default completion budget to 16,384 tokens so reasoning models can finish after a tool result, and made verbose CLI output UTF-8-safe on legacy Windows consoles.
- Fixed normal build prompts being treated as read-only or entering the lean repair loop: explicit parent mutations release worker no-edit instructions, build+review requests stay coding tasks, and repair prompts that request skills/delegation keep those tools.
- Made `list_agents` search space-separated specialty terms instead of requiring one exact substring.

- Auto-update now compares `origin/main` history instead of release version numbers, so commits merged between releases fast-forward a clean checkout.

- Added a deterministic TaskRun controller for repair turns: phase tracking, turn/tool caps, repeated-failure stops, and one-shot no-edit, artifact-paging, job-polling, and finalize nudges.
- Added a cumulative repair token budget (`MACHINA_REPAIR_TOKEN_BUDGET`, default 1,000,000; `0` disables) and pressure-based stale-result condensation.
- Added a lean repair toolset (`MACHINA_REPAIR_LEAN_TOOLS`, default on) so web, memory, delegation, and skill-install tools do not bloat every repair request; set `0` to restore the full catalog.
- Counted successful direct behavior checks as verification evidence, added `machina usage --json`, and copied Harbor usage into `AgentContext`.
- Fixed Git Bash child `PATH`/preflight so Git's `grep`/`sed` tools are available, rejected local paths in `web_extract`, and skipped generated/dependency directories in content search.
- Forced LF checkout for ECC skill/agent resources so their manifest SHA-256 hashes verify on Windows as well as Linux.
- Added canonical benchmark ledger/lockbox support, clean-history benchmark isolation, and the 2026-11-12 held-out verification record.
- Reduced interactive startup latency: the no-subcommand CLI now avoids importing the full command surface, auto-update checks are reused for 15 minutes, gateway startup reads `.git/HEAD` directly instead of spawning `git`, and Python gateway boot overlaps the Node UI module load.
- Made `/models` open from warm catalogs: the picker asks for cache-only catalog rows (no live ChatGPT/named-provider discovery), warms the grouped result for repeated opens, and leaves live discovery to `/models catalog`.
- Added guided empty states for `/activity`, `/output`, `/sessions`, and `/context` so a first-run or unused view explains how to create activity instead of showing a blank or terse result.
- Rendered those guidance responses as a framed “next steps” panel, using the same adaptive width and wrapping rules as assistant responses.
- Made the auto-update check cache work in git worktrees (including Windows/macOS worktree layouts) and added cross-platform smoke coverage for the lightweight entry point, startup/update paths, and guidance panels.

## 1.2.14 — 2026-10-25

- Composer paste sanitization also covers non-bracketed paste paths (for example right-click paste in Windows Terminal), not only bracketed paste and Ctrl+V. Extended-colour SGR forms such as `[38:2::r:g:b m` are stripped as well.

## 1.2.13 — 2026-10-25

- `/auth` provider menus now always offer **Sign in** (re-authenticate), even when already signed in; **Sign out** remains alongside it. This lets an imported Codex credential be replaced without first signing out.
- Untyped agent-turn failures now include the exception class name (for example `RuntimeError`) so a remaining ChatGPT failure can be diagnosed without exposing arbitrary exception text.

## 1.2.12 — 2026-10-25

- Composer paste now also strips literal SGR mouse reports (`[<35;44;19M`-style) that Windows terminals can leave in copied text, in addition to ANSI color sequences. Raw mouse reports are ignored if a host delivers one as input.

## 1.2.11 — 2026-10-25

- Composer paste now strips terminal SGR/ANSI control sequences, including literal colour residue copied from a terminal, before the draft is created. Pasted prose, paths, and line breaks are preserved.

## 1.2.10 — 2026-10-25

- `/auth` now opens a provider/action menu instead of printing a status card; select a provider, then sign in, sign out, import, or inspect status.
- Windows now-playing detection and transport controls use the native System Media Transport Controls API through WinRT, so the music bar works with Windows media sessions.
- Legacy Windows Ctrl+Arrow encodings are normalized for the composer/editor; failed agent turns now include the redacted typed provider error so subscription failures are actionable.

## 1.2.9 — 2026-10-24

- The interactive launcher now checks `origin/main` and fast-forwards a clean source checkout, then re-executes into the updated code. `MACHINA_AUTO_UPDATE=0` disables it; dirty checkouts are left untouched.
- `/auth` is now an interactive subscription menu for ChatGPT, Grok, and GitHub Copilot: select a provider, then sign in, sign out, import, or inspect status.
- Windows now-playing detection and transport controls use the native System Media Transport Controls API (`winrt`), so the music bar works with Spotify, browsers, and other Windows media sessions.
- Normalized legacy Windows Ctrl+Arrow encodings to the standard xterm sequences; composer/editor word motion continues to accept Alt+Arrow as a fallback.
- Successful `/auth` sign-in activates the matching provider and a catalog model for the current session and future launches, addressing turns that kept using the previous provider after login.
- Failed agent turns now include a redacted provider error detail instead of only the generic `agent turn failed` notice.
- Clarified model naming: OpenAI ships GPT-6.1 Sol, but Luna remains GPT-6 Luna; the picker keeps `gpt-6-luna` rather than inventing a `gpt-6.1-luna` id.

## 1.2.8 — 2026-10-24

- When winget cannot install Docker Desktop, the Windows installer now downloads Docker's official signed installer and runs it silently instead of giving up.
- Added `MACHINA_AUTO_INSTALL_DOCKER=0` to opt out of automatic Docker Desktop installation.
- Documented the remaining hard requirement: Docker Desktop needs WSL2/Hyper-V and may require administrator approval or a reboot; without it, a native PostgreSQL server must already provide pgvector.

## 1.2.7 — 2026-10-24

- `machina init` now creates a missing database and enables `vector` and `pg_trgm` on an already-reachable PostgreSQL server instead of failing with `database "machina" does not exist`.
- The Windows installer detects an occupied PostgreSQL port and provisions the Docker container on the next free port (typically 5433), updating `DATABASE_URL` and `MACHINA_DB_PORT` together.
- The database lifecycle honors `MACHINA_DB_PORT`, so later `machina` launches start the alternate-port container correctly.
- The Windows installer now fails with a clear error when the database is not ready instead of printing “Machina is installed” after a failed `machina init`.
- Docker installation failures now explain whether winget was unavailable and what to install manually.
- Re-running the installer from inside an existing checkout now fast-forwards the repository first, so installer fixes apply instead of silently reusing stale code.

## 1.2.6 — 2026-10-24

- The Windows installer now detects a virtualenv created by a different Python version and rebuilds it, avoiding ABI-broken extension packages.
- The installer verifies `typer`, `asyncpg`, and `fastapi` imports after dependency installation and rebuilds the virtualenv once if any package is incomplete or corrupt.
- Documented the repair command for an existing broken Windows virtualenv.

## 1.2.5 — 2026-10-23

- Pinned Typer below 0.21.2 so fresh Windows installs do not pull the new `annotated-doc` dependency and fail at startup with `ImportError: cannot import name 'Doc'`.
- Re-running the installer downgrades an existing broken Typer installation in the Machina virtual environment.

## 1.2.4 — 2026-10-23

- `machina`, `machina serve`, and `machina init` now start Docker Desktop/Engine when needed, then start or create the managed `machina-db` PostgreSQL container automatically.
- A newly created container receives a generated password, a persisted `DATABASE_URL`, an automatic schema initialization, and an `unless-stopped` restart policy.
- The Windows installer now starts Docker Desktop and attempts a winget install of Docker Desktop when it is missing; the Bash installer starts Docker Desktop/Engine before provisioning.
- Kept native/external PostgreSQL untouched; `MACHINA_DB_MANAGE=0` still opts out.

## 1.2.3 — 2026-10-22

- Added `machina stop`, which shuts down the HTTP server first, then the TUI/gateway, and finally the installer-managed PostgreSQL container after the last client releases its lease.
- Long-lived TUI and `machina serve` processes now register database leases; concurrent clients prevent an early database stop.
- A normal TUI exit now stops the managed Docker database when no other Machina client is running; the next start relaunches it automatically.

## 1.2.2 — 2026-10-22

- Added a native Windows PowerShell installer (`install.ps1`) with no WSL requirement.
- The Windows installer can install missing Git/Python/Node with winget, create the venv, bootstrap dependencies, start a pgvector Docker database, run schema initialization, and add the venv Scripts directory to the user PATH.
- Windows users can now use `irm https://raw.githubusercontent.com/kiazh/Machina-Harness/main/install.ps1 | iex` as the primary install command.

## 1.2.1 — 2026-10-22

- Installer now retries transient clone failures, handles WSL/Windows explicitly, and produces a working venv when `ensurepip` is missing by bootstrapping pip.
- Added the Machina install banner and animated download indicator.
- README now surfaces WSL/Git Bash requirements for Windows users.

## 1.2.0 — 2026-10-21

- Model picker now uses the bundled models.dev catalog for built-in providers, with current names, context windows, efforts, and descriptions.
- Reworked grouped picker headers and rows: provider names in section headers, display names as labels, and compact model/context/effort descriptions.
- Grok subscription menu includes the current Grok variants and remains separate from the xAI API-key provider.

## 1.1.1 — 2026-10-21

- Cleaned the model picker to one current row per family and removed legacy duplicates.
- Added Grok subscription variants: Grok 4.7 Build Fast, 4.6, 4.5, 4.3, and Grok Build 0.1.

## 1.1.0 — 2026-10-21

- Updated curated model fallbacks and context windows from the models.dev catalog: GPT-6.1, Claude Sonnet/Opus/Haiku 5.x, Gemini 4, Grok 4.7, DeepSeek V4 Flash, Mistral Large 4, Qwen 3.8, and DeepSeek V4.1 Flash.
- Added Grok subscription OAuth via xAI's device flow (`machina auth grok login`), a `grok` provider over the Grok CLI Responses proxy, and `/models` fallback rows.

## 1.0.0 — 2026-10-21

First open-source release of Machina as a self-hosted agent harness.

- Terminal UI, CLI, HTTP API, MCP, PostgreSQL context/memory, permissions, jobs, skills, and provider integrations.
- Bounded MCP subagent delegation (`machina_agents`, `machina_delegate`, `machina_delegate_many`).
- Full-code review hardening across gateway framing/backpressure, approval redaction, memory/job ownership, extension-tool digest binding, MCP result bounds, session fork archives, OAuth cleanup, audit permissions, SoulSpec export ownership, quick-open containment, and LSP lifecycle handling.
- Release validation: 6,502 backend tests passed (2 skipped), 1,165 UI tests passed, Docker image/CLI smoke passed, wheel/sdist isolated-import checks passed, and GitHub Actions CI (backend, UI, Docker) passed.
- Known limitations: self-hosted only; no hosted service, production SLA, backup/restore certification, or guarantee for every provider/account combination. Review the release notes before use.

## 0.2.0 — development release (historical)

- Fullscreen terminal UI with themes, framed results, exact approval controls and quieter motion.
- Interactive persona editing, persisted runtime learning and feedback/revision restoration.
- Agent selection before delegation and checked, confirmed session deletion.
- 42 curated offline ECC skills and 71 default ECC subagent roles, with upstream licenses and hash manifests.
- Shell sequencing through the shared permission parser and model-capacity-based context handling.
- Default MCP SDK, saved installation-local server configuration, interactive connection management and lazy broker-bound native calls. OAuth browser authorization (Authorization Code + S256 PKCE) is supported for HTTP servers; HTTP credential profiles remain deferred.
- Consolidated CI, source/wheel resource checks, vendored-data lint exclusions and removal of tracked environment backup artifacts.
- Opt-in, independently gated semantic response caching for auxiliary/read-only calls, prompt-cache modes with an enforced minimum prefix, protected learned-compression spans on both complete and streaming calls, shadow telemetry, local startup warmers, and read-only `machina optimization` status/economics commands. All response-path modes default off; model selection and authority are unchanged.
- Resumed sessions now continue with native user/assistant history after a sawtooth compaction boundary; stable turns stay append-only for provider prefix caches, evicted turns fold into a durable summary, and the dynamic tail prunes cold tool output to single-line tombstones.
- Hot-path sweep: linear tool-call pair detection, bounded skill-token and metric-label caches, a single-query document-excluded compaction aggregate, retained embedder-close tasks, event-driven terminal completion, and corrected request-projection default documentation.
- MCP subagent delegation: `machina_agents`, `machina_delegate`, and `machina_delegate_many` let OpenCode/Codex orchestrate durable Machina child agents; batches allow up to 20 tasks in bounded waves (`max_delegate_concurrency`, default 4).
- Debug/dead-code/performance sweep: constraint detection runs once per task descriptor, continuity trimming drops a partial leading turn in one slice, and compression memoizes fallback token counts per chunk.
- Repository-performance follow-up: bounded manifest and repo-source reads, malformed-cache recovery, stale-path cache pruning, awaited LSP restart-task shutdown, and a pre-mutation overlap guard for head-to-head run destinations.
- Full-code review fixes: one-reservation ownership across wrapped providers, sanitized media metadata, editor Ctrl+C selection routing, tab-width cache invalidation, session-scoped mode responses, blank-query skill filtering, and MCP batch mode-ceiling propagation.
- Keep the workspace Problems section composed when the composer slash menu changes chrome height, instead of letting the first popup open clip it.

This candidate has not been published or deployed. See [release readiness](internal/docs/verification/RELEASE_READINESS.md) for evidence and open deployment gates. Runtime adaptation does not train neural weights or authorize self-editing.
