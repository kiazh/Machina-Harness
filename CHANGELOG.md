# Release notes

## Unreleased

**Changes screen: review each prompt, not each commit.**

### Added

- A second full-page surface: on an empty composer, `←` opens Activity and `→` opens Changes. Either arrow or Esc returns to chat; with text in the composer, arrows keep editing. The legacy `Ctrl+N` then `←`/`→` chord remains a fallback. `/changes` opens Changes directly; `/changes all` includes every chat in the workspace.
- Per-prompt change history. Each completed turn that changed a file stores its prompt, provider/model, file list, `+/-` line counts, and a stable end-of-turn copy, so a later prompt editing the same file cannot rewrite an earlier row's diff.
- Three inspector views: `FILES` (bounded, selection-following), `DIFF` (stable unified diff), and `REVIEW` (status, summary, findings with severity, file and line). Keys: `↑/↓` prompt, `[`/`]` file, `1/2/3` or Tab view, PgUp/PgDn scroll, `r` review, `x` cancel, `w` scope.
- Review runs attached to a prompt through new `changes.list`, `changes.diff`, `review.start`, and `review.cancel` RPCs. The first engine is deterministic (`rules`): conflicts, credential-like values, debug breakpoints/prints, unfinished markers, broad exception handlers, `shell=True`, disabled TLS verification, world-writable permissions, dynamic execution, URL credentials, and truncated-diff disclosure.
- Findings are actionable. `n`/`p` selects one; `Enter` or `a` opens an action request that explains the issue and asks `Implement fix` / `Not now`. Nothing is sent to the agent unless the user chooses `Implement fix`; that path closes the screen and sends a focused repair prompt scoped to the finding.

### Changed

- Full visual overhaul: `moonstone` is now **Nocturne** (obsidian canvas, bone ink, acid-lime signal), with full-width bone `❯` prompt bands, a solid accent `MACHINA` block, `⏺` assistant prose, `⏺`/`⎿` tool activity with colored diff previews, an accent-ruled `❯` composer, a quiet context header, a one-row footer, unframed Activity/Changes tabs, bracketless editor tabs, numbered prompt bands, per-turn `✻` colophons, and calmer diagnostics. Assistant prose caps at a readable 96 columns; tool activity at 108. Approval evidence remains the one intentional framed surface. Workspaces now start as a full-width chat: the editor and explorer appear only when a file is opened, so no empty half-screen pane is reserved. The music bar is a single compact line.
- Snapshot manifests now record the prompt, route, per-file change kind, additions/deletions, and an end-of-turn copy. No-op writes are dropped; ignored paths remain as an honest disclosure.
- Command and remote RPC registries expose 53 slash commands and 176 JSON-RPC methods.

### Fixed

- Runaway tool loops now stop with a typed failure instead of burning the turn. The controller detects the same successful tool + exact args repeating three times, more than ten terminal/`list_files` directory listings without a read or edit, and more than twelve `session_recall` / `session_recall_window` calls. The exact-action counter resets after an edit; ordinary `read_file` / `search_files` exploration remains unbounded. These guards apply to chat turns too; repair-only turn and tool-call caps are unchanged.

## 2.4.2 - 2026-11-24

**Delegation survives a broken provider key.**

### Fixed

- **Subagents no longer inherit a dead provider route.** A free Hermes parent's model is now sticky for delegated children across *all* providers, not just other Hermes routes. The router ranks from "a key exists", so an unfunded DeepSeek key (HTTP 402) or an invalid OpenRouter key (HTTP 401) used to outrank the working free model; every child then failed before doing any work. An explicit agent-definition pin still wins.
- **Provider failures say what happened.** A failed turn now reports the HTTP status, provider/model and the fix ("Check the provider credential", "Add credits or switch provider/model") instead of the generic "LLM provider error. Check server logs."
- Test isolation: the local `MACHINA_HARNESS_MODEL*` overrides no longer leak into command-shape tests.

### Changed

- **Approvals no longer expire by default.** `approval_timeout` now defaults to `0`, meaning Machina waits for the human until they approve, deny, or stop the turn. Leave the screen for an hour and the card is still there. Set a positive value only when you deliberately want an unattended timeout; the previous 300-second default expired pending approvals. The setting accepts `0` through unbounded seconds.

## 2.4.1 - 2026-11-24

**Harness chats keep the conversation.**

### Fixed

- **Streamed answers no longer break into one word per line.** Hermes emits token-level `text` events; Machina rendered each as its own line. Token text is now concatenated verbatim, while tool/system events keep their own lines.
- **The final answer is no longer duplicated.** When the streamed log already ended with the answer, the completion used a raw substring check that failed across line breaks and appended the answer again. Both the transcript and the stored message now compare normalized text.
- **Harness child chats keep their conversation.** Each turn used to start a fresh harness session, so "can you retry" had no context. Machina now stores the harness session id on the child session and resumes it: Hermes with `chat --resume <id>`, OpenCode with `run --session <id>`. The id is persisted as soon as it appears, so a later turn continues even if the previous run was interrupted.
- **Headless runs cannot stall on a prompt.** Hermes runs with `--oneshot --yolo --accept-hooks`; OpenCode runs with `--format json --auto`. Previously a tool approval or clarify prompt had no TTY to answer it and the run sat until the deadline.
- **Long research turns get time to finish.** Harness chat turns are bounded at 10 minutes instead of 5; the deadline error is unchanged for shorter delegated runs.

### Changed

- **Model policy: DeepSeek only.** Machina never silently switches to another vendor's model to recover a run. The automatic replacement list only considers DeepSeek ids, and a provider-suggested model is adopted only when it is DeepSeek. `MACHINA_HARNESS_MODEL` (or `MACHINA_HARNESS_MODEL_<PLATFORM>`) pins the model used for a harness run.
- OpenCode runs now use `--format json`, so Machina captures the session id and renders `→ tool` / `← output` progress; the final answer is the concatenated text events.

### Activity screen

- Rebuilt the Activity surface for scanning: a `Machina · Activity` header with live/idle and freshness, filled filter pills, uppercase section bands with per-section counts, aligned `STATUS`/`UPDATED` columns, relative timestamps, and a `❯` selection marker.
- The inspector is now a proper details pane: title, state/section/age chips, project/detail/runtime/queue rows, and a count-labelled log block with a one-line hint that says whether Enter opens the conversation or toggles logs.
- Number keys `1–7` switch filters, the footer shows the current key map, and narrow terminals split into a list plus an inline Inspector instead of one long scrolling column.
- Empty sections and empty inspectors explain what they are waiting for instead of showing blank space.

## 2.4.0 - 2026-11-24

**Two-way harness chats.**

### Added

- Harness child sessions are now real two-way chats. Opening one from Activity and sending a message forwards it to the same harness platform as a background job; the answer streams back into that child transcript and is stored in history.
- Only harness-backed sessions get this behavior. Native delegated subagent sessions remain inspection-only.
- Session serialization exposes `harnessPlatform` so the UI can distinguish the two.
- Harness chat turns acquire/release the same durable turn claim as normal turns, so concurrent sends are still fenced.
- **Live harness output.** The shared bounded runner now streams stdout/stderr chunks to the job as they arrive (redacted, line-buffered). Activity shows the latest line and the last 20 live lines; a two-way harness chat streams the same output as `message.delta` before the final answer. This applies to every run-capable harness (OpenCode, Codex, Hermes, and any future platform using the shared runner).
- **Harness "thinking/doing" log in the chat.** A two-way harness chat keeps the streamed log in the transcript and appends the final answer, instead of replacing the log when the turn completes. The same log is stored on the child session, so reopening it shows what the harness did.
- **Hermes streams structured progress.** Hermes now runs as `hermes chat --query-file - --format stream-json` (prompt on stdin), and Machina renders its events as `model: …`, `→ tool: …`, `← tool: …` lines. The final answer is taken from the structured `result` event, so raw JSON never reaches the transcript.

### Fixed

- Streaming reads now use un-buffered byte reads. `read(n)` on a text pipe blocked until the child exited, so every harness looked silent until it finished; output now arrives line by line for all platforms.
- Hermes model fallback: when the provider reports exhausted credits, Machina skips the provider's suggested paid model, tries the live free models in a reliable order (fast ones first), refuses to start an attempt with less than 12 seconds of budget left, and remembers the last model that worked for the rest of the process. The first Hermes run after start recovers in about 30 seconds; later runs use the working model immediately.
- Retried-attempt provider prose ("didn't answer after 3 attempts", "free period has ended", billing notices) and `session_id:` lines are filtered out of the chat/Activity output, so a recovered retry no longer looks like a failure.
- A harness job publishes its terminal status only after its last progress flush and transcript store; polling never observes a half-finished completion.

## 2.3.0 - 2026-11-24

**Harness jobs become real child chats.**

### Added

- Every background harness job now creates a child chat session parented to the session that started it. The task and bounded result are stored as transcript chunks, so Activity → Agents/Sessions can open the run and read what happened.
- Activity Enter on a harness job row opens its child chat; the child also appears as an agent row after the family refresh.
- Question notifications: a harness completion that reads like a request for direction ("which one?", "need more direction", etc.) is flagged `needs_input`, shown as **“Harness X is asking: …”**, and marked `needs input` in Activity.
- `harness.job` snapshots now include `owner_session_id`, `session_id`, and `needs_input`.

### Changed

- `harness_jobs.start` is async (it creates the child session first). The tool and gateway background paths await it; the run still starts immediately after.

## 2.2.2 - 2026-11-24

**Session-workspace validation fix.**

### Fixed

- A plain chat session has no `desktop_workspace_root` pin. `/harnesses` and
  tool execution treated “no pin” as an invalid workspace and surfaced
  `Session workspace is invalid; select its folder again`. Harness runs now
  fall back to the configured workspace; explicit absolute pins still win.
- Legacy relative pins are resolved against the configured workspace instead
  of failing every harness/tool call for that session.

## 2.2.1 - 2026-11-24

**Keyboard-freeze hardening for slow commands.**

### Fixed

- `/harnesses` Run a task can never fall back to the old foreground path: it now requires a session and always starts a background job, so the "Consulting Hermes… waiting" freeze cannot occur.
- A slow slash command no longer swallows every key indefinitely. The command keeps the keyboard for its 1.5s dialog window; after that, ordinary typing reaches the composer while Enter/Esc stay reserved for the command. This protects any future long-running command, not only harness/delegation paths.

## 2.2.0 - 2026-11-24

**Background harness subagents and Activity navigation.**

### Added

- Activity screen: Enter on an agent or persisted-session row opens that session; Space still expands the retained logs. The footer says which key does what.
- External harnesses can run as background subagents. `harness_delegate` defaults to `background=true`, returns a job id immediately, and keeps the main agent usable; `harness_job` lists/reads/waits for jobs. Jobs appear in Activity under Harnesses with bounded output, and a `harness.job` event posts a completion notice in the visible session.
- `/harnesses` Run a task now starts a background job and returns immediately instead of blocking the client for the whole harness deadline.

### Changed

- `/delegate` is fire-and-forget: the command returns at once and prints the validated child result when it finishes, instead of holding `commandRunning` (and swallowing every key) until the delegation completes.

### Fixed

- Harness runs enforce one overall deadline across model-hint/free-model retries instead of granting each attempt the full timeout.

## 2.1.1 - 2026-11-24

**Dynamic tool loading and Hermes delegation patch.**

### Fixed

- `tool_load` now actually widens the next model step's schema surface. Tool execution runs in a child `asyncio` task, so the old `ContextVar` assignment was discarded when that task finished; the loaded set is now a shared per-turn value, and a regression test crosses the task boundary.
- `machina harnesses run hermes` now recovers from retired/paid model failures: it retries once with the replacement Hermes names (for example `switch to 'stepfun/step-5-preview'`), and when the account has no credits for that model it retries with a live `:free` Nous model instead of failing the delegation.
- Delegated children keep a free Hermes parent's route, and `route_task` filters paid Hermes candidates for any free session/global route (including MCP/headless callers). Free proxy rows are zero-cost in the routing inventory.
- Kiro one-click startup injects the detected `KIRO_CLI_DB_FILE` and the loopback `PROXY_API_KEY` into the gateway child, so a freshly provisioned checkout starts instead of failing with "No Kiro credentials configured".
- `machina status`, MCP `machina_status`, and HTTP status now start the idempotent runtime probes before reporting, so a first call shows `chat provider: ready` instead of `unknown`.

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
