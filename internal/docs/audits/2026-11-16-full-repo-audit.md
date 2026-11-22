# Full-repository audit - 2026-11-16

Scope: backend (`machina/`, `tools/`, `integrations/`), UI (`ui/src/`, `ui/test/`), shared contracts, tests, CI, packaging, and tracked artifacts. Branch: `main` at `75f075d` plus this remediation pass.

## Final verification

| Check | Result |
|---|---|
| UI typecheck | pass |
| UI tests | 1219 passed, 10 skipped |
| UI lint | pass |
| Python tests | 519 passed, 9 skipped |
| Ruff check | pass |
| Ruff format check | pass — 435 files formatted |
| Bandit (`-ll`) | pass — 0 medium, 0 high |
| Contracts tests | 3 passed |
| Instrumentation catalog | `--check` current |
| `machina doctor` | runs successfully against the live local DB |
| Source distribution | builds; verified clean at 2.49 MB (down from 8.99 MB) |
| Wheel | builds; verified isolated imports at 2.14 MB |

## Fixed in this pass

1. **`machina doctor` crashed on import.** `machina/cli/__init__.py` imported private `_node_version` from the compatibility shim, which did not re-export it. It now imports from `machina.ui_launcher`; a CliRunner regression test was added.
2. **CI referenced a deleted UI test.** `.github/workflows/ci.yml` still ran `test/help-menu.test.ts`, so all keyboard matrix jobs failed. The stale path was removed and `tests/test_ci_workflow.py` now fails if CI names a missing UI test.
3. **Source distribution omitted shared contracts.** Hatchling pruned `packages/contracts` through the npm workspace junction, while `ui/src` imports it directly. The sdist now force-includes the contract source and `tools/check_release_artifacts.py` requires it.
4. **Source distribution shipped the review archive.** sdist selection now allowlists package/UI sources and excludes `docs/verification`, `docs/archive`, `tests`, `.agents`, `.github`, `research`, and `integrations`. The archive dropped from 8.99 MB to 2.47 MB and no longer carries 39 MB of internal evidence when unpacked.
5. **Docker context was mostly unnecessary.** `.dockerignore` now excludes `docs/`, `tests/`, `ui/`, `packages/`, `research/`, `integrations/`, `communications/`, `.agents/`, and `.github/`.
6. **Wildcard address was treated as loopback.** `browser_verify` accepted `0.0.0.0` URLs, and `is_loopback_base_url` exempted `0.0.0.0` from the plaintext-HTTP gate. Both now accept only real loopback targets; tests cover the refusal. This also clears Bandit B104.
7. **Gateway exit left work running.** `onGatewayExit` now stops now-playing polling/animation, clears chrome, key-leader, and completion timers, clears the completion session, stops cursor blink, and is one-shot across duplicate error/exit events.
8. **Replaced previews leaked view caches.** `WorkspaceController` now reports released document ids; the app releases the editor view's state, line, wrap, and syntax caches for replaced previews and stale opens.
9. **Dead code removed:** `ui/src/events.ts` (unused duplicate), `catalogDescription`, `pickCuratedModel`, `createLayout`, the unreachable sync compression fallback, `_local_reset_for_tests`, and the zero-caller `compress_context_chunks` wrapper.
10. **Formatting gates repaired.** All of `tests/` and `integrations/` are now `ruff format`-clean and CI checks the same scope as lint.
11. **ReAct loop unified.** `run` and `run_stream` are thin adapters over one `_react_loop` policy loop (net −228 lines); parity tests cover streaming, cancellation, token accounting, and auto-parallel presentation.
12. **Turn lifecycle unified.** New `core/turn_lifecycle.py` owns claim/session/agent/approval/renewal/cleanup and drives `TurnEngine` for gateway, HTTP, CLI, and scheduler. `TurnEngine.run` and the explicit `emit_pause_markers` policy remove the remaining duplicated terminal/approval branches; 15 focused lifecycle and transport tests cover approvals, ownership loss, timeouts, and paused jobs.
13. **Typed gateway seam.** HTTP no longer synthesizes JSON-RPC frames or pokes private readiness: `Gateway.call(method, params)` returns typed results, `db_ready`/`refresh_db_readiness` are public, and `/rpc` owns only its HTTP envelope.
14. **Shared session creation.** `core/session_service.py` owns agent/provider/model resolution, validation, initial state (emotion/workspace), context budget, and persistence. RPC and REST keep only their request/response and activation differences; 19 parity tests cover the old divergences.
15. **CLI split.** `machina/cli/__init__.py` is a ~220-line composition root; `chat`, `memory`, `sessions`, `skills`, `maintenance`, and `common` own the command bodies. Command names, order, help, and sub-app hierarchy are pinned by a new test.
16. **App split.** `ui/src/app.ts` dropped from 4,861 to 3,772 lines. `TurnSessionStore` owns turn/approval/steering/delegated/token state and pruning; `WorkspaceSurface` owns workspace construction, open/restore/switch/close, events, and shell geometry.
17. **UI hot paths.** Streaming deltas coalesce per frame and sanitize incrementally; editor edits update bytes/dirty/line endings incrementally and send exact ranges; chrome, Problems, and explorer measurements are revision-cached; session state is pruned; tabs are bounded at 32; narrow explorer toggles refuse honestly; catalog errors distinguish an old gateway from real failures.
18. **Python hot paths.** Request projection runs through `asyncio.to_thread` with one message copy; configured RAG construction failures are recorded; built-in tools load lazily (~190 ms vs ~839 ms for `import machina.tools`); the dead synchronous compression path is gone.
19. **Repository and release cleanup.** Rerank evidence is gzip-compressed (16.4 MB → ~685 KB) with `summary.md` preserved, Compose exposes the container-relevant Kiro/meta-harness/repair/terminal keys, the README links this audit, and the generated instrumentation catalog matches the moved gateway dispatch seam.
20. **Transcript projection unified.** `core/transcript.py` owns role/content/tool/interrupted projection, raw-vs-redacted policy, payload decoding, and UTF-8 truncation. `gateway/serializers.py`, `gateway/features/history.py`, and `services._export_one_session` use it while preserving wire shapes, SQL paging/oversized fallbacks, and Markdown rendering. Nine parity tests plus a 2,000-case differential fuzz pass against the old code.

## Remaining findings

No high or medium findings remain. The low-priority decisions below are intentional rather than unowned debt.

### Low / dead surfaces

- The remaining pre-wired UI surfaces (`IntelligencePopups`, `JumpHistory`, `narrowSymbolsPayload`/`prepareHoverText`, `formatEditsForDocument`, `codeActionsFromPayload`, and the future `WorkspaceGateway` methods) are retained intentionally: they are covered by focused UI tests and are the producer/consumer seams for the already-shipped hover, completion, navigation, formatting, and code-action flows. They are not duplicate generated code.
- `_local_reset_for_tests` was removed as a true zero-caller helper. `get_tool`, metrics accessors, memory helpers, `read_stderr`, `document_snapshot`, and telemetry accessors are retained as documented public/test seams (R4-106); `read_stderr` and `document_snapshot` carry explicit keep comments.
- `SkillPreselectCache` is retained as the explicitly deferred TurnController foundation documented in `machina/controller/README.md`; its wiring milestone is the `_prepare_context` follow-up, so it is not an unowned dead surface.
- `core/checkpoint.py` is not dead: `validate_checkpoint` is used by `core/assembler.py` and `core/compression.py`. `JsonFormatter` is retained and covered by its existing formatter tests.
- `ui/src/events.ts` duplicate was removed; do not reintroduce a second generated copy.
- The `connectors` extra is a no-op alongside base `mcp`, but it is kept for install compatibility: CI, docs, and the connector error message still name `Machina[connectors]`. `pytest-cov`/`hypothesis` are kept as external DevTools dependencies; no in-repo test uses them.
- Compose environment allowlist now includes the container-relevant Kiro, meta-harness, repair-deadline, and terminal-shell settings.
- `docs/screenshot.png` was removed as unreferenced. The rerank proof logs were gzip-compressed from 16.4 MB to about 685 KB; `summary.md` remains the readable result table and the raw runs are preserved as `*.log.gz`.

## Repository bloat

- Tracked tree after this pass: 2,046 files. The rerank proof logs were compressed to about 685 KB; the remaining large items are `docs/verification` fixtures/screenshots and `uv.lock` (843 KB). The wheel is clean; the sdist is now clean of review/test material.
- Local ignored junk: `.venv` about 388 MB, `ui/node_modules` about 64 MB, `.pytest_cache`, `.env`, skills telemetry. These are ignored and safe to delete locally; they should not be committed.
- `.git` is about 104 MB with 95.7 MB loose objects versus 7.4 MB packed. `git gc` is safe housekeeping; do not rewrite history without owner approval.
- Docker context was reduced to the files the Dockerfiles actually copy.

## Completion

All recommended remediations are complete: the ReAct loop, turn lifecycle, session creation, transcript projection, typed gateway seam, CLI split, App split, request projection, UI hot paths, and release cleanup. The remaining low-priority surfaces are retained deliberately with tests, keep comments, or a documented deferred milestone; see the decisions above.
