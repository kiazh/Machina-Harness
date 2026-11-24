# Machina 2.1.1

The dynamic-tool-loading, delegation-routing, and Kiro setup patch for 2.1.0.

## Fixed

- **`tool_load` now works on the next model step.** Tool calls execute in a
  child `asyncio` task; child tasks copy the context, so assigning a new
  `ContextVar` value inside the tool was invisible to the parent. The agent
  then denied the just-loaded tool as "not in the current tool surface".
  The loaded set is now a shared per-turn mutable value, so a child task's
  `tool_load` widens the parent's next `_tool_defs()` projection. A
  regression test executes `tool_load` through `asyncio.create_task`, the
  exact boundary that lost the state.
- **Delegated children keep a free Hermes parent's route.** The connected
  model router could rank a paid Nous model above the user's explicitly
  chosen free model, and a credit-less account then failed every child at the
  provider. A `:free` Hermes parent route is now sticky for children.
- **Routing never silently upgrades a free Hermes session to paid models.**
  `route_task` filters Hermes candidates to free rows when the effective
  session or global route is a `:free` model, including MCP/headless callers
  with no session. `machina route` now passes the configured route so its
  explanation matches what a turn will do. Free proxy rows are also marked
  zero-cost in the routing inventory.
- **Hermes harness model retirement and paid-model exhaustion.**
  `machina harnesses run hermes` retries once with the replacement model the
  provider names (`... switch to 'stepfun/step-5-preview'`), and if that model
  requires credits the account lacks, retries with a live `:free` Nous model
  (queried from the local proxy, with verified ids as fallback). Unrelated
  failures no longer get a misleading "Retried with..." annotation.
- **Kiro one-click startup passes the detected credentials.** The provisioned
  checkout now starts even though the upstream `.env.example` leaves
  `KIRO_CLI_DB_FILE` commented and carries a placeholder `PROXY_API_KEY`.
  Machina injects the detected Kiro CLI SQLite path and its loopback password
  into the child environment (the gateway's dotenv loader never overrides
  process environment).
- **Status surfaces report real capability on first use.** `machina status`,
  MCP `machina_status`, and HTTP status share `services.status_summary`, which
  now runs read-only provider/sandbox probes first. Previously they reported
  `chat provider: unknown` until some other command had initialized the
  runtime. The probe never connects the DB, starts audit, loads skills, or
  flips the runtime's `_started` flag, so the API lifespan remains the sole
  startup owner.

## Manual end-to-end verification (2026-11-24)

Verified against a disposable PostgreSQL database and a temporary
`MACHINA_HOME`, using the live Hermes proxy (Nous Portal) and a disposable
Kiro checkout:

- Real turn: `terminal` → `write_file` → `read_file` on `tencent/hy4-flash:free`;
  file contents and final answer verified.
- `tool_load(["delegate"])` followed by two parallel `delegate` calls in the
  next step; both children ran, returned `CHILD_ONE`/`CHILD_TWO`, and used the
  free parent route.
- Follow-up delegation on the same child session (archive/reactivate) returned
  `FOLLOWUP`.
- MCP `machina_status`, `machina_agents`, and `machina_delegate_many` (two
  children in one wave) completed.
- `machina harnesses run hermes` recovered from the retired model and completed
  on a free Nous model. OpenCode external harness completed. Codex is blocked
  locally by a malformed `~/.codex/config.toml` (outside Machina).
- Gateway RPC: `initialize`, `providers.list`, `models.list` (410 Hermes
  models), `auth.hermes.status`, `auth.kiro.status`, `session.create`,
  `prompt.submit` (`GATEWAY_OK`), `usage.get`.
- HTTP API: `/health`, `/ready`, authenticated `/api/v1/sessions`, `/agents`,
  and the `machina api` client.
- Sessions: list, context, export, fork; memory remember/list/search/stats;
  document indexing (`Document RAG: ready; 1 indexed documents`); background
  `terminal_job` (`JOB_OK`); snapshots list/diff/undo.
- Kiro one-click in a disposable checkout: install (clone + venv + requirements,
  ~12s), start, `/v1/models` returned 23 models, managed stop.
- UI: full typecheck, tests, and lint.

## Verification

- Repository smoke suite: `717 passed, 18 skipped`.
- UI suite: `1,262 passed, 6 skipped`; typecheck and lint clean.
- External suite: `6,548 passed, 13 skipped, 2 xfailed` on 2.1.0; the 2.1.1
  deltas are covered by the repository regression tests and CI.

## Compatibility

- No database migration.
- No configuration change.
- `tool_load` behavior is now the documented behavior; no API surface change.
