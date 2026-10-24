# Release notes

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

This candidate has not been published or deployed. See [release readiness](docs/verification/RELEASE_READINESS.md) for evidence and open deployment gates. Runtime adaptation does not train neural weights or authorize self-editing.
