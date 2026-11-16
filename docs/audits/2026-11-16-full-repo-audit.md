# Full-repository audit - 2026-11-16

Scope: backend (`machina/`, `tools/`, `integrations/`), UI (`ui/src/`, `ui/test/`), shared contracts, tests, CI, packaging, and tracked artifacts. Branch: `main` at `f245ed8`.

## Verification baseline

| Check | Result |
|---|---|
| UI typecheck | pass |
| UI tests | 1203 passed, 10 skipped |
| UI lint | pass |
| Python tests | 453 passed, 9 skipped |
| Ruff check | pass |
| Ruff format check | pass after formatting 13 files |
| Bandit (`-ll`) | pass after removing the wildcard-loopback finding |
| Contracts tests | 3 passed |
| `machina doctor` | runs successfully against the live local DB |
| Source distribution | builds; verified clean at 2.47 MB (down from 8.99 MB) |
| Wheel | builds; verified clean at 2.13 MB |

## Fixed in this pass

1. **`machina doctor` crashed on import.** `machina/cli/__init__.py` imported private `_node_version` from the compatibility shim, which did not re-export it. It now imports from `machina.ui_launcher`; a CliRunner regression test was added.
2. **CI referenced a deleted UI test.** `.github/workflows/ci.yml` still ran `test/help-menu.test.ts`, so all keyboard matrix jobs failed. The stale path was removed and `tests/test_ci_workflow.py` now fails if CI names a missing UI test.
3. **Source distribution omitted shared contracts.** Hatchling pruned `packages/contracts` through the npm workspace junction, while `ui/src` imports it directly. The sdist now force-includes the contract source and `tools/check_release_artifacts.py` requires it.
4. **Source distribution shipped the review archive.** sdist selection now allowlists package/UI sources and excludes `docs/verification`, `docs/archive`, `tests`, `.agents`, `.github`, `research`, and `integrations`. The archive dropped from 8.99 MB to 2.47 MB and no longer carries 39 MB of internal evidence when unpacked.
5. **Docker context was mostly unnecessary.** `.dockerignore` now excludes `docs/`, `tests/`, `ui/`, `packages/`, `research/`, `integrations/`, `communications/`, `.agents/`, and `.github/`.
6. **Wildcard address was treated as loopback.** `browser_verify` accepted `0.0.0.0` URLs, and `is_loopback_base_url` exempted `0.0.0.0` from the plaintext-HTTP gate. Both now accept only real loopback targets; tests cover the refusal. This also clears Bandit B104.
7. **Gateway exit left work running.** `onGatewayExit` now stops now-playing polling/animation, clears chrome, key-leader, and completion timers, clears the completion session, stops cursor blink, and is one-shot across duplicate error/exit events.
8. **Replaced previews leaked view caches.** `WorkspaceController` now reports released document ids; the app releases the editor view's state, line, wrap, and syntax caches for replaced previews and stale opens.
9. **Dead code removed:** `ui/src/events.ts` (unused duplicate), `catalogDescription`, `pickCuratedModel`, `createLayout`, the unreachable sync compression fallback, and the zero-caller `compress_context_chunks` wrapper.
10. **Formatting gates repaired.** `ruff format` brought 13 files into the CI-enforced format.

## Remaining findings by severity

### High

1. **Two copies of the ReAct loop.** `machina/core/agent.py:2399-2727` (`run`) and `:2728-3181` (`run_stream`) repeat roughly 300 lines of policy: pending chunks, parallel planning, repair budgets, stop handling, iteration counters, recovery, and consolidation. Change one loop and the other can drift. Recommended seam: one `_react_loop(emit: Callable[[AgentEvent], None])`; non-streaming captures the final event, streaming maps events to `StreamEvent`.
2. **Turn lifecycle is implemented four times.** `TurnEngine` exists but only Gateway/HTTP use it fully. `gateway/server.py:1059-1501`, `api/routes/streaming.py:44-504`, `cli/__init__.py:345-585`, and `core/scheduler.py:1080-1190` repeat claim/renewal/cleanup/terminal-frame logic. This is the main risk for divergent approval, ownership-loss, and cancellation behavior. Move the common lifecycle into `TurnEngine`, leaving transport serialization as adapters.
3. **`ui/src/app.ts` is a god object.** About 4.7k lines and 150 methods own turn state, approvals, transcript, composer, keybindings, now-playing, dialogs, and the entire workspace surface. Extract at least `WorkspaceSurface` (workspace open/restore/close/event flow) and `TurnSessionStore` (turn bindings, approvals, in-flight data).
4. **Request projection blocks the event loop.** `core/request_gate.py:410` runs synchronous `project_request` from the async iteration path; `request_projection.py` deep-copies messages up to three times and can run 24 fit passes. Long histories can stall unrelated SSE turns. Offload with `asyncio.to_thread` and derive projections from one captured copy.
5. **CLI is a second god module.** `machina/cli/__init__.py` is about 2.45k lines with 60+ Typer commands, interactive chat, memory CRUD, skills, setup/doctor, and host uninstall mixed together. Split at least `cli/chat.py`, `cli/memory.py`, and `cli/maintenance.py`.

### Medium

6. **UI streaming is quadratic.** `AssistantTurn.ts` sanitizes all accumulated text on every delta; `transcript.hideSpinner` scans container children per token. Coalesce deltas per animation frame, sanitize incrementally, and track spinner presence with a boolean.
7. **Editor edits are O(file size) per keystroke.** `editor/model.ts` rescans line endings, encodes old/new text twice, and hashes the full document on dirty recomputation; `workspace/controller.ts:replacementChange` diffs full old/new text for one range. Track bytes/dirty/line endings incrementally and pass the edit range from the store.
8. **Chrome is rendered to measure rows on every key.** `AppShell.chromeRows()` renders composer, footer, activity, and approval dock; `ActivityBar` recomputes activity summaries and job filters. Cache row budgets by layout/state key.
9. **Problems projection is repeatedly rebuilt.** `ProblemsPane.currentRows()` and `counts()` each sort/dedupe; cap enforcement sorts all rows and removes via `indexOf`, making it quadratic. Cache one projection keyed by a mutation revision.
10. **Per-session state is not fully pruned.** `rememberSession` drops turns/token totals only. Active tools, steering queues, delegated text, Kiro fallback flags, and `ActivityStore` queues can outlive evicted sessions. Add one `pruneSessionState(id)` seam.
11. **Explorer tree is walked twice per render.** `buildTreeRows()` calls `visibleNodes()`, then `hasMorePages()` calls it again. Cache visible nodes by model revision.
12. **`TabModel.enforceOverflow` is never called.** The documented tab limit is dead policy; pinned/non-preview tabs grow without it. Wire it into open or remove the policy.
13. **`explorerOverlay` is computed but never composed.** Narrow layouts report overlay mode, but the shell only composes a docked explorer; `/explorer` can claim success with no visible tree. Implement overlay composition or refuse honestly.
14. **HTTP synthesizes JSON-RPC frames and reaches private gateway state.** `api/app.py:_RpcGateway` builds `{"jsonrpc": ...}` and pokes `_gateway._db_ready`. Add a typed `Gateway.call(method, params)` seam.
15. **Transcript projection exists in four places.** `core/context.py`, `gateway/features/history.py`, `services.export_markdown`, and `gateway/serializers.py` each map/redact chunks differently. One `TranscriptReader` should own page/render semantics.
16. **Session creation is split by transport.** RPC and REST already diverge on agent validation, desktop workspace state, and emotion. A shared `SessionService.create` should own resolution and creation.
17. **Model catalog failures are misreported.** `features/models.ts:fetchCatalog` collapses transport errors into “older gateway” advice. Distinguish `RpcError -32601` from real failures.
18. **`ruff format` covers less than `ruff check`.** CI checks format only for `machina/` and `tools/`, while lint covers `tests/` and `integrations/`. Either format-check all or document the split.

### Low / dead surfaces

- Test-only or zero-caller UI surfaces: `IntelligencePopups` (hover/completion popups), `JumpHistory`, `narrowSymbolsPayload`/`prepareHoverText`, `formatEditsForDocument`, `codeActionsFromPayload`, and several `WorkspaceGateway` methods. Some are pre-wired future features; decide keep-and-test or delete.
- Test-only Python helpers with no current tests/callers are listed in the backend audit: `get_tool`, metrics accessors, several memory helpers, `read_stderr`, `_local_reset_for_tests`, `document_snapshot`, telemetry accessors, and others.
- `SkillPreselectCache` is explicitly deferred, exported, and documented but unused. Keep only if it has an owner and a milestone.
- `core/checkpoint.py` checkpoint builders and `JsonFormatter` have no consumers.
- `ui/src/events.ts` duplicate was removed; do not reintroduce a second generated copy.
- `pyproject.toml` still has a no-op `connectors` extra (duplicates base `mcp`), and unused-in-repo `pytest-cov`/`hypothesis` dev dependencies; confirm external DevTools usage before removing.
- `docs/screenshot.png` was removed as unreferenced. `docs/verification/proofs/rerank_wave_2026-10-14/` still contains 16.4 MB of mostly repeated audit logs; `summary.md` carries the actual results. Decide whether to keep logs as evidence or move them to release assets.

## Repository bloat

- Tracked tree: 2,013 files. Largest tracked items are the rerank proof logs (16.4 MB), `docs/verification` fixtures/screenshots, and `uv.lock` (843 KB). The wheel is clean; the sdist is now clean of review/test material.
- Local ignored junk: `.venv` about 388 MB, `ui/node_modules` about 64 MB, `.pytest_cache`, `.env`, skills telemetry. These are ignored and safe to delete locally; they should not be committed.
- `.git` is about 103 MB with 95.7 MB loose objects versus 7.4 MB packed. `git gc` is safe housekeeping; do not rewrite history without owner approval.
- Docker context was reduced to the files the Dockerfiles actually copy.

## Recommended order

1. Unify `run` and `run_stream` behind one loop.
2. Make `TurnEngine` the only turn driver; migrate CLI and scheduler.
3. Extract `SessionService.create` and `TranscriptReader`.
4. Add `Gateway.call` and remove HTTP's JSON-RPC synthesis.
5. Extract `WorkspaceSurface` and `TurnSessionStore` from `App`.
6. Make ambient request projection non-blocking.
7. Fix UI streaming/editor/chrome/Problems hot paths.
8. Delete or wire the remaining test-only surfaces.
9. Decide the long-term home of the 16.4 MB rerank evidence.
