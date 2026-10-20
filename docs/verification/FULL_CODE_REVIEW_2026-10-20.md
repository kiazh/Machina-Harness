# Full-code debugging and debloating review — incomplete

Status: **partial audit; do not treat this as a complete review or an all-clear.** Parallel reviewers hit an account usage limit before finishing their assigned reads. Review baseline: `8f2085480577285900a9d1e45c7a382189bc47b9`; verification fixes: `610ff50` plus DevTools `e9da800`. An independent verification pass reproduced every listed P1/P2 finding, fixed the confirmed defects, and reran the complete backend and UI suites; the coverage ledger remains partial.

## Coverage

The source/config inventory contains 624 tracked files and 190,087 lines. It includes Python, TypeScript, JavaScript, shell, SQL, build/runtime configuration, bundled ECC scripts, and historical executable proof scripts. It excludes binary assets, archived output logs, ordinary prose, dependency lock data, and third-party installed dependencies. The separate Machina-DevTools backend test repository is not part of this source inventory; UI tests are included.

| Area | Documented complete-file reads | Assigned files | Documented lines |
| --- | ---: | ---: | ---: |
| Root reviewer: skills/tools/research/packaging/bundled and proof scripts | 52 | 226 | 16,321 |
| Core/database/memory/RAG/services | 118 | 122 | 43,094 |
| UI source/config/tests | 107 | 170 | 37,193 |
| API/CLI/connectors/gateway/security/workspace boundaries | 0 conservatively credited | 106 | 0 conservatively credited |
| **Total** | **277** | **624** | **96,608** |

The boundary reviewer reported substantial reading and candidate findings but did not persist a complete per-file ledger before its usage-limit failure. Those reads are deliberately not counted. The 347 remaining-or-undocumented paths are listed explicitly in `remaining-files.txt`.

Evidence directory: `/Users/shadow/machina-devtools/reports/full_code_review_2026-10-20/`. `combined-coverage.json` records reviewed byte hashes, current byte hashes, coverage sources, and unresolved paths. Each review ledger distinguishes full reads from pending/partial reads. File counts do not imply equal review depth or that an already-read file is bug-free.

The coverage ledgers describe the review snapshot. Verification changed several reviewed files while fixing the reproduced findings; the current tree was retested end to end rather than carrying old hashes forward. The original `head_to_head` destructive trigger was rechecked after its guard patch and remains resolved.

## Findings reproduced against the current working tree

### P1 — one provider request is reserved and settled twice — fixed

Locations: `machina/core/usage.py:288`, `:312`; `machina/core/provider/direct.py:136`.

`UsageStore.complete_call` reserves usage, then forwards `session_id` and `agent_id` to `DirectProvider.complete`. The concrete provider makes its own reservation and settles it; the wrapper subsequently settles its outer reservation. A no-network `httpx.MockTransport` reproduction records **one transport POST, two reservations, two finishes**.

Impact: wrapped calls can over-count usage and consume request budgets twice. The effect on a specific configured budget should receive a database regression test before changing accounting ownership.

Fix direction: establish one explicit reservation owner for wrapped calls while keeping direct provider calls accounted. Do not remove either layer's accounting without preserving cancellation, failures, cached-response attribution, and auxiliary-call purpose labels.

Acceptance: a wrapped real-provider request produces exactly one ledger reservation and settlement; a one-request budget permits one outgoing request; direct provider calls and failed/cancelled requests still settle correctly.

Fixed: the accounting wrapper now marks the call `usage_reserved=True` for provider classes that own their reservation; the concrete provider skips its private reserve (and any stream reservation) while direct provider calls retain the old self-accounting path. The regression test in `tests/test_usage.py` uses a real `DirectProvider` and asserts one POST, one reservation, and one finish.

Evidence: `repro_double_reservation.py` and `repro_double_reservation.log`; post-fix output is `network_posts: 1, reservations: 1, finishes: 1`.

### P1 — media metadata carries terminal controls into rendered output — fixed

Locations: `ui/src/nowplaying/controller.ts:24`; `ui/src/components/NowPlayingBar.ts:198`.

The controller accepts a raw title/artist and the bar interpolates it into styled terminal text. An isolated controller/bar reproduction supplied an OSC 52 sequence in a title; that sequence survived the rendered output.

Impact: terminals that honor the sequence may interpret metadata as terminal control, including clipboard control. The reproduction checked emitted bytes; it did not execute terminal controls or access the user's clipboard.

Fix direction: use the existing UI untrusted-text sanitizer on title, artist, and source before styling; keep trusted renderer formatting separate.

Acceptance: hostile OSC/CSI/control characters are removed from all media render variants; ordinary Unicode metadata still renders and truncates correctly.

Fixed: the controller normalizes title, artist, album, source, and error through the shared single-line sanitizer, and the bar sanitizes again before styling as defense in depth. The OSC-52 reproduction now reports `osc52Survives: false`; focused bar/controller tests cover the attack string.

### P2 — application Ctrl+C routing preempts selected-text copy — fixed

Locations: `ui/src/app.ts:1736`; `ui/src/editor/view.ts` selected-text copy handling.

With the editor focused and a real selection present, the global App handler consumes Ctrl+C before the editor handler receives it. The isolated reproduction selected `\tX` and called the actual App handler: **exit invoked once, clipboard copy invoked zero times**. A running turn can instead be cancelled by the same global route.

Fix direction: let the focused editor handle Ctrl+C when it owns a selection, preserving the established cancel/clear/exit behavior when it does not.

Acceptance: test input through App routing, not only directly through EditorView; cover selection, no selection, running turn, and composer focus.

Fixed: `EditorView.hasSelection()` and the App routing now let the focused editor consume Ctrl+C only while it owns a selection; the global cancel/clear/exit branches remain unchanged otherwise. The App-level regression selects text and asserts the route is not consumed.

### P2 — changing tab width leaves soft-wrap layout stale — fixed

Location: `ui/src/editor/view.ts:398`.

`setTabWidth` changes the setting and requests rendering without invalidating the cached wrap layout. With `\tX` at width seven, switching tab width four to eight hides `X`; calling `invalidate()` makes it reappear.

Fix direction: invalidate the affected layout caches when tab width changes and ensure the frame/wrap identity includes every geometry setting.

Acceptance: compare rendering immediately after the preference change with rendering after a fresh layout; include tabs, horizontal clipping, wrapping, and caret positioning.

Fixed: tab width is part of the wrap-cache identity and `setTabWidth` clears the wrap/frame caches. The editor regression compares the immediate frame with a fully invalidated frame and requires `X` to remain visible.

### P2 — stale session mode responses overwrite the current footer — fixed

Location: `ui/src/app.ts:663`.

`App.request` copies every `mode.get/set/revoke` response into the footer without checking its session against the current session. A delayed `mode.get` for session A reporting `full`, delivered after switching to session B in `ask`, changes B's displayed mode to `full`.

Impact: the displayed execution mode becomes inaccurate. This reproduction establishes a display defect, not a change in backend authorization.

Fix direction: bind mode updates to the response/request session and current session generation; ignore stale display updates.

Acceptance: resolve mode requests out of order across session switches and mode changes; the footer must reflect the current session's latest accepted state.

Fixed: mode responses carry a response/session identity; `App.request` ignores a response whose session no longer matches `this.current`. The regression resolves session A's delayed `full` response after switching to B and keeps B's `ask` footer.

### P2 — blank-query skill browsing ignores selected filters — fixed

Location: `ui/src/components/SkillsMenu.ts:1074`.

The blank-query branch invokes `providers.catalog()` with no filters. Choosing the disabled filter still displayed both enabled and disabled candidates in the isolated menu reproduction; the filtered search provider was never invoked.

Fix direction: apply the selected filters to catalog browsing, or route through a backend endpoint that implements blank-query filtering and pagination consistently.

Acceptance: cover enabled/disabled and source filters for both blank and nonblank queries, including pagination and stale requests.

Fixed: the empty-query branch passes filters to the catalog provider, the gateway catalog handler applies the same closed filters, and the menu defensively filters enabled/source on legacy catalog responses. The regression changes enabled and source filters without typing and verifies the exact catalog request and rows.

All five UI reproductions are retained in `repro_ui.mjs` and `repro_ui.log`. They use real component/handler methods with isolated fake host dependencies and make no provider calls.

## Previously reproduced data loss — resolved in the current checkout

The head-to-head benchmark accepted a `--runs-dir` layout whose computed destination equaled the pristine task fixture and then deleted that fixture with `shutil.rmtree`. The original isolated temporary-fixture reproduction is retained in `h2h-data-loss-repro.json`.

Commit `8f20854` subsequently added computed-destination overlap checks at CLI preflight and inside `run_one`. The patch was inspected and the same trigger now exits with status two while preserving the fixture. Evidence: `h2h-current-guard-check.json`. This is not an outstanding finding.

## Candidate leads requiring reproduction before fixes

### P2 — MCP batch delegation did not apply the server mode ceiling — fixed

`Orchestrator.run_parallel` had no authority parameter, so `machina_delegate_many` children bypassed the explicit ceiling that single `machina_delegate` calls applied. The parallel runner now accepts and forwards `authority`, and the MCP batch path passes `{"max_mode": _mcp_mode_ceiling()}`. The MCP regression asserts the captured authority is the configured `ask` ceiling.

Reviewers reported the following additional leads, but did not finish their proof/report work before the usage limit. Do not treat them as confirmed vulnerabilities or apply broad fixes from this list alone:

- Anthropic wired cache-breakpoint fallback may omit system/tool fields below the cache-prefix threshold.
- Gateway request dispatch may drain a bounded queue into an unbounded chain of waiting tasks.
- Cancelling a workspace save may release its async lock while the filesystem worker continues, permitting overlapping stale writes.
- MCP structured results may undergo several full serialization/redaction copies before their output limit is enforced.
- Archive event ordering, continuity repair persistence, and semantic-cache eviction order require isolated checks.
- Preview-document eviction may retain editor caches beyond document closure.

Root notes additionally record possible malformed-telemetry handling, whitespace-only verification identifiers, section-anchor ambiguity, and repeated lexical benchmark indexing. These remain leads, not confirmed findings.

## Verification and source changes

The independent verification pass reproduced every P1/P2 finding above and applied minimal fixes with regression tests. It also confirmed the earlier `head_to_head` data-loss reproduction is closed by the destination guard. Coverage counts were checked against the inventory: 624 files, 190,087 lines, 277 documented full reads, and 347 remaining-or-undocumented paths. No runtime feature, public compatibility export, bundled script, historical proof, or dependency was deleted.

Verification evidence:

- Full backend suite against a fresh dedicated database (`machina_fullreview_final`): **6,495 passed, 2 skipped**, exit code zero, in 437.75 seconds.
- Full UI check against the dedicated UI database (`machina_sweep_ui_20261020`): **1,164 passed, zero skipped**, typecheck and ESLint clean.
- `repro_double_reservation.py`: **1 POST, 1 reservation, 1 finish** after the fix.
- `repro_ui.mjs`: wrap layout stays valid, Ctrl+C is not consumed with a selection, stale mode response leaves `ask`, OSC-52 does not survive, and blank-query disabled filtering shows only the disabled candidate.
- Focused regressions: usage/skills/provider **57 passed**; MCP/multi-agent/cache checks passed; UI repro tests **163 passed** before the final full UI run.
- `ruff`, format, compileall, Bandit (**0 medium/high**), instrumentation catalog, `uv lock --check`, and `git diff --check` passed.

The earlier 6,477/1,158 figures and the 71-failure sandbox run belong to the preceding sweep and are not used as validation for these fixes. The source tree is committed for this verification pass; the coverage ledger remains partial.

## Continuation work

1. Finish every path in `remaining-files.txt`, starting with undocumented boundary coverage and remaining UI tests; maintain source hashes and line ranges.
2. Check already-reviewed hashes against the current checkout and inspect changed code before carrying findings forward.
3. Reproduce the remaining candidate leads (Anthropic cache-prefix fields, gateway queue growth, workspace-save lock ownership, MCP serialization copies, archive/continuity/semantic-cache eviction, preview-document eviction) before applying fixes.
4. Keep the full backend and UI suites green as the remaining coverage is completed. The current ledger still does not support an all-clear claim.
