# Debugging, performance and bloat sweep — 2026-10-20

Baseline: `a775fb09424fb89f323f3cf0b65c4beb6f716366`. The source edits and regression tests below were independently reproduced in a verification pass; the report records both the original claims and the corrections made after testing them.

## Scope and outcome

Ran broad automated checks and inspected Python prompt/token handling, task metadata, repository scanning/caching, retrieval, session caching, instrumentation, and process/task handling. Ran the full UI check and an initial backend selection excluding database, UI integration, and optional workflow-trial tests. A subsequent pass enables database checks using dedicated local test databases; results are recorded below. This is a broad engineering sweep, not an exhaustive proof that every subsystem is bug-free or a production load test.

Three fixes across three Python source files address unnecessary allocations and ineffective read limits. Two cache follow-ups and the five database-dependent gateway checks were also fixed after independent reproduction. The regression suite now covers malformed cache JSON, stale-path pruning, and the original bounds checks. One LSP teardown warning observed in the earlier database run was not reproduced in the final full run after a shutdown-await fix; the original warnings remain recorded below.

## Applied fixes

### 1. Enforce the manifest limit before allocating the whole file

Location: `machina/core/task_descriptor.py:529`.

Before: `read_text()[:_MANIFEST_MAX_BYTES]` read and decoded the entire file, then sliced characters. The nominal 256 KiB limit therefore bounded neither I/O nor allocation, and multibyte text could exceed the byte budget while still being accepted.

After: read at most 262,145 bytes in binary mode; refuse a file over 262,144 bytes before decoding/parsing. Ordinary manifests retain their existing stack inference. Oversized manifests yield no inferred project stack, following the existing fallback for unusable metadata.

Validation: oversized ASCII and multibyte manifests are refused; an ordinary React manifest still identifies React. These reproductions failed before the fix.

### 2. Avoid copying unused history and dependency entries

Location: `machina/core/task_descriptor.py:546` and `:557`.

Before: `list(recent)[:3]` copied all history to inspect three items; `list(declared)[:64]` copied all dependency keys to inspect 64.

After: `itertools.islice` visits only the existing inspection windows. Ordering and window sizes remain the same.

Validation: a history iterable that raises if visited beyond the three-entry window now completes successfully. Existing descriptor tests pass.

### 3. Bound repository source reads throughout scanning and cache validation

Locations: `machina/repomap/scan.py:110`, `machina/repomap/cache.py:42`, and `:69`.

Before: scanner admission checked pathname size, then performed an unrestricted `read()`. A file that grew between the check and read bypassed the 2,000,000-byte admission limit. Cache insertion and lookup also used unrestricted reads.

After: scanner and cache share the same source-size constant and read at most 2,000,001 bytes. The scanner marks an oversized read as skipped before parsing; cache lookup/insertion refuse it. Existing content-digest checks still reject same-stat rewrites and insertion of a parse belonging to different bytes. Removed the unused `_PARSED` alias when centralizing the limit.

Validation: simulated growth after the size check is skipped without parsing; tracked source reads remain bounded on both fresh and cached scans. Existing same-stat rewrite and parse/hash-race regressions pass.

## Measured allocation changes

A single local synthetic run under `tracemalloc`, comparing the baseline functions with the working tree. Times are illustrative, sensitive to cache/machine load, and not an end-to-end application speed claim. Peak allocation is Python-tracked allocation, not process RSS.

| Scenario | Before peak | After peak | Before time | After time |
| --- | ---: | ---: | ---: | ---: |
| Read 32 MiB manifest with trailing whitespace | 67,241,335 bytes | 394,058 bytes | 13.278 ms | 0.166 ms |
| Inspect first three of 1,000,000 history entries | 8,000,080 bytes | 104 bytes | 8.656 ms | 0.012 ms |

The oversized manifest is now deliberately refused. Typical small-manifest behavior is preserved. The history benchmark represents a large supplied sequence; it does not establish that normal sessions contain a million entries.

Benchmark script and raw output: `/Users/shadow/machina-devtools/reports/performance_sweep_2026-10-20/benchmark.py` and `benchmark.json`.

## Confirmed follow-up fixes

### Fixed: tolerate malformed repository cache structure

Location: `machina/repomap/cache.py:24` and `:34`.

Reproduction: create a valid Python source file, write `[]` to the persistent cache JSON, construct `ContentHashCache(cache_path)`, then call `build_repomap(root, cache)`. Before the fix the result was `AttributeError: 'list' object has no attribute 'get'`. JSON parsing succeeded, but the result was installed without checking that it was a mapping; incorrectly shaped entry objects could similarly fail during lookup.

Impact: an invalid cache could prevent directory learning rather than falling back to a fresh scan.

Fix applied: `ContentHashCache` accepts only a mapping at the top level and only entries with the expected field types. Invalid entries are dropped and therefore behave as misses; digest and timestamp checks are unchanged. Verified with array/null/scalar top-level JSON, malformed entries, missing fields, and malformed JSON. Healthy caches still avoid parsing, and same-stat content changes remain detected.

### Fixed: prune deleted paths from the repository cache

Location: `machina/repomap/cache.py:85`, `:95`, and `machina/repomap/scan.py:85`.

Reproduction: using one cache, create a uniquely named Python file, scan, delete it, and repeat ten times. A final empty-tree scan reported zero files while the cache retained ten entries. No scan or save removed stale paths.

Impact: generated-file churn could grow in-memory cache state and the JSON written/read on every persistent scan. Growth was across distinct historical paths, even when the current repository was small.

Fix applied: a completed scan reconciles entries under its own root against the paths it observed; entries for other roots and entries retained during an incomplete scan are preserved. Regression coverage checks deleted-path pruning and unrelated-root preservation.

Acceptance checks: repeated create/delete cycles stop growing the cache; live unchanged files remain cache hits; partial/error scans do not invalidate unrelated entries; unrelated roots remain intact.

### Gateway database prerequisites — verified and guarded

Location: `Machina-DevTools/tests/test_gateway.py:233` through `:305`.

Five tests fail outside the sandbox when `MACHINA_TEST_DATABASE_URL` is unset. All five subsequently pass with the dedicated test database configured (five passed in 3.01 seconds); the application failure was an unmet test prerequisite, not a reproduced gateway defect. Each attempts gateway initialization, which returns error 1001 (`database unavailable`) because the isolated test configuration supplies no database DSN. These tests do not use the DB fixtures that automatically add the `db` marker, so `-m "not db"` still selects them.

| Test in `tests/test_gateway.py` | Observed failure |
| --- | --- |
| `TestAuthentication::test_correct_token_accepted` | Expected result; received database-unavailable error |
| `TestAuthentication::test_initialize_without_token_when_no_auth` | Expected result; received database-unavailable error |
| `TestErrorMessages::test_handle_line_returns_generic_error` | Expected INVALID_PARAMS; received database-unavailable error |
| `TestTurnTimeout::test_turn_timeout_configurable` | Session creation returns an error; result key missing |
| `TestTurnTimeout::test_turns_dict_cleaned_up_after_completion` | Session creation returns an error; result key missing |

Fix applied: the five tests now carry the existing `@needs_db` guard. A no-DB run reports five skips with the explicit missing-test-DSN reason (`gateway-no-db.log`); the dedicated-DB run exercises all five successfully (`gateway-db.log`). No gateway production behavior was changed to satisfy these checks.

## Additional confirmed data-loss finding

The separate full-code-review notes claimed a P1 data-loss path in `machina/research/head_to_head.py`. It reproduced: with task directory `<base>/machina/machina`, `--runs-dir <base>`, and harness `machina`, the computed run directory equals the pristine task directory. The old code called `shutil.rmtree(run_dir)` before copying and deleted the fixture. The computed destination is now checked for equal/ancestor/descendant overlap before any mutation, and `tests/test_head_to_head_symlink_containment.py` covers the exact layout. This is a correctness fix, not a performance claim.

## Local database verification follow-up

PostgreSQL was already running locally, so starting another server was unnecessary. Verified PostgreSQL 16.15 (Homebrew), pgvector 0.8.7, and pg_trgm 1.6. Created `machina_sweep_20261020` for backend tests and `machina_sweep_ui_20261020` for the UI's real-gateway tests. No existing application database was selected. Both dedicated databases remain available for reproducing these checks; no server startup configuration or application DSN was changed. Test schema initialization uses the project's own schema and runtime migration methods.

The five previously failing gateway checks pass with `MACHINA_TEST_DATABASE_URL=postgresql://shadow@127.0.0.1:5432/machina_sweep_20261020`. The full UI check with database integration enabled passes against the dedicated UI database: 1,158 tests passed, zero failed, zero skipped, plus typecheck and ESLint. Running the UI E2E suite against the already-used general `machina_test` database instead fails its empty-list expectation because that database has more than 100 pages of sessions; this is test-data contamination, not a UI regression. The database-enabled backend run passes: 6,477 tests passed, two skipped, eight deselected, exit code zero. A later verification run including all tests and seven added cache regressions passed 6,492 with two skipped. It emitted no LSP cleanup warnings.

### Follow-up investigation: LSP tasks outlive the test event loop

The database-enabled backend run passes but emits `Task was destroyed but it is pending!` at shutdown for `lsp-restart:typescript-language-server`, `lsp-restart:pyright`, and TypeScript LSP writer/reader/stderr tasks. It also reports unawaited `LspConnection` coroutines and a closed child-process event loop. These messages are preserved in `backend-db.log`.

This is an observed cleanup warning in the earlier database run; the evidence did not distinguish missing fixture teardown from a production manager lifecycle defect. A concrete gap was found and fixed: `LspManager.stop_all()` cancelled pending auto-restart tasks but did not await them, so a cancelled restart could still be pending when the test loop closed. It now awaits those tasks before stopping connections. After the fix, the final full database run produced no pending-task or unawaited-coroutine messages. If the warning recurs, isolate the owning fixture rather than suppressing it; the original log remains the evidence that the warning was real.

## Verification

- Final database-enabled backend command: `MACHINA_TEST_DATABASE_URL=postgresql://shadow@127.0.0.1:5432/machina_sweep_20261020 PYTHON=/Users/shadow/machina/.venv/bin/python HYPOTHESIS_STORAGE_DIRECTORY=/tmp/machina-sweep-hypothesis bash /Users/shadow/machina-devtools/run-tests.sh tests/ -q -p no:cacheprovider -m 'not ui_integration and not workflow_trial' --tb=short` — **6,477 passed, two skipped, eight deselected**, exit code zero, in 487.69 seconds. Evidence: `backend-db.log`. The LSP warnings recorded in that run were not reproduced after the shutdown-await fix; see below.
- All-tests verification against the dedicated database: `MACHINA_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/machina_sweep_20261020 ./run-tests.sh -q` — **6,493 passed, two skipped**, exit code zero, in 407.07 seconds. This includes the seven cache regression tests and one head-to-head destination-overlap regression added during verification; 6,477 + 7 new + 8 deselected + 1 destination-overlap regression test reconciles with the earlier selected run. No pending-task or unawaited-coroutine warnings appeared.
- Database-enabled UI command: `MACHINA_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/machina_sweep_ui_20261020 MACHINA_TEST_PYTHON=/Users/shadow/machina/.venv/bin/python npm run check` in `ui/` — **1,158 passed, zero skipped**, plus typecheck and ESLint. Evidence: `ui-db-check.log`. The same command against the used `machina_test` database failed the E2E empty-list expectation after the database exceeded 100 session pages; that is test-data contamination, not a UI failure.
- Five gateway regressions with the dedicated test database — **five passed**; without a DSN — **five skipped** with the missing-DSN reason. Evidence: `gateway-db.log` and `gateway-no-db.log`.
- Targeted backend command: `PYTHON=/Users/shadow/machina/.venv/bin/python bash /Users/shadow/machina-devtools/run-tests.sh tests/test_sweep_bounds.py tests/test_task_descriptor.py tests/test_repomap.py tests/test_a3_repomap_snapshot.py tests/test_v1_repomap_tool.py -q -p no:cacheprovider` — **97 passed** after the cache regressions were added.
- New regressions: 13 tests in `/Users/shadow/machina-devtools/tests/test_sweep_bounds.py` (six original bounds checks plus seven malformed-cache/pruning checks). A detached worktree at `a775fb0` reproduced five failures and one pass for the original six; all 13 pass in the final tree.
- `npm run check` in `ui/` without a database — **passed**: typecheck, 1,152 passing tests, six environment-gated skips, and ESLint. The original sandbox run had one AppKit clipboard failure; rerunning the isolated clipboard test and full check outside the sandbox passed.
- `ruff check --no-cache machina/` — passed.
- `ruff format --check --no-cache machina/` — 285 files already formatted.
- `bandit -r machina/ -ll` — zero medium/high findings and zero scan errors. This is a static scan, not a comprehensive security assessment.
- `git diff --check` — passed.
- Broad backend command: `PYTHON=/Users/shadow/machina/.venv/bin/python HYPOTHESIS_STORAGE_DIRECTORY=/tmp/machina-sweep-hypothesis bash /Users/shadow/machina-devtools/run-tests.sh tests/ -q -p no:cacheprovider -m 'not db and not ui_integration and not workflow_trial'` — **5,819 passed, 71 failed, 109 skipped, 482 deselected**, in 331.68 seconds. This run started in the original sandbox before the source edits and does not establish a completely green final backend suite.
- Reran all 71 failing node IDs against the final working tree outside the sandbox — **66 passed, five failed**, in 41.06 seconds. The five are the gateway prerequisite cases above; they now skip without a DSN and pass with the dedicated database. The initial failures include denied local socket/process operations, unavailable model-download DNS, and one performance-threshold failure. The ledger test `tests/test_a6_release_evidence.py::test_a6_performance_and_cost_ledger` initially measured warm-search p95 at 53.55 ms against a 50 ms bound; it passed on rerun and in **five consecutive independent runs** during verification. This is not evidence of a stable performance regression or improvement.
- The 71-failure count is a sandbox-specific initial run: it includes denied socket/process operations and unavailable DNS that do not occur in the canonical local run. It is retained as historical evidence, not presented as a current product failure. All 71 node IDs remain in `backend-initial-failures.json`; complete traces are in `backend-initial.log` and `backend-rerun.log`.

Logs and raw static-scan output are retained under `/Users/shadow/machina-devtools/reports/performance_sweep_2026-10-20/`.

## Limits and next work

The follow-up runs use dedicated local PostgreSQL databases. No optional LangGraph workflow trial, Docker builds, dependency upgrades, production traces, or dedicated performance load tests were performed. No dependency or feature was removed on the assumption that its size alone made it unnecessary. Automated unused-import/name checks pass; larger removal decisions need usage evidence.

The two cache follow-ups and the five gateway prerequisites are now implemented. The LSP teardown warning was not reproduced after awaiting cancelled restart tasks in `stop_all`; keep the warning as a regression signal if it returns. For further performance work, profile representative real agent turns and directory-learning runs to establish which helper costs materially affect user-visible latency before changing concurrency, caching semantics, or dependencies.
