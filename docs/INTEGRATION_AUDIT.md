# Integration audit - 2026-11-08

Scope: the memory digest, domain experience, harness picker, Windows CLI,
RAG loader, telemetry persistence, and the Machina-DevTools acceptance suite.

## Fixed during the audit

| Finding | Fix |
| --- | --- |
| Windows gateway E2E wrote to the operator's real `~/.machina/config.yaml` because `MACHINA_HOME` was ignored by the config-path resolver | `default_config_path()` now honors `MACHINA_HOME`; save/load resolve it per call. The test-created sessions/memories and test persona were removed. |
| Skill telemetry failed on Windows: `os.fsync` on a read-only handle raised `OSError` | The temp telemetry file is opened `rb+` for the fsync, then atomically replaced. |
| RAG's documented Windows fallback could follow a symlink swapped after validation | The fallback re-resolves immediately before opening and refuses a target outside the authorized base. |
| The new cached/parallel interop path bypassed the existing `detect` test/legacy seam | Production keeps the fast path; a replaced module-level `detect` now receives the compatibility path and the legacy wire shape. |
| `MemoryDigest` source IDs were placed in a chunk payload with no consumer | Removed the payload data and corrected the docs; selection details remain in the returned `MemoryDigest`, not dead request data. |
| Minimal session-like test doubles lacked `parent_session_id`/`is_fork` | The factory uses defensive `getattr` defaults when deriving delegation status. |

## Verification

| Suite | Result |
| --- | --- |
| Machina backend | 287 passed, 9 skipped |
| Machina terminal UI | 1,208 passed, 10 skipped |
| Desktop unit | 240 passed |
| Desktop E2E (prior unchanged run) | 50 passed, 1 skipped |
| Contracts | 4 passed |
| Real gateway E2E | 5 passed |
| Real MCP OAuth gateway test | 18 passed |
| DevTools backend collection | 6,472 selected / 8 deselected |
| DevTools targeted integration set | 305 passed, 1 deselected |
| Ruff (repository) | clean |
| Compileall | clean |

Manual Kiro Claude Haiku 5.5 memory digest E2E: three seeded memories,
`memory_digest` audit `selected=3 chars=239`, correct answer `Oat milk flat
white`. The temporary memories were deleted.

## DevTools platform gaps

The full DevTools selection is Linux-oriented. On this Windows host the
following are test-harness limitations, not Machina runtime failures:

- `test_installer_env.py` imports the POSIX-only `fcntl`.
- `test_harnesses_core.py::test_run_task_delivers_option_like_prompts_literally`
  installs an extensionless shell stub; Windows resolves the installed
  `opencode.CMD` instead.
- `test_a3_rag_bounded_io.py::test_non_regular_substitution_is_refused` uses
  `os.mkfifo`; the symlink regressions pass (8 passed, 1 deselected).
- `test_a6_release_evidence.py` contains a Windows asyncio loop wait that
  hangs; pytest-timeout reported the hang.
- Architecture ratchet ceilings and provider-package totals were measured
  against an older tree and are already exceeded by this checkout's Kiro /
  provider work before this audit's changes.

## Dead-code and wiring checks

- Every new config key is read by production code and documented.
- `MemoryDigest`, `build_memory_digest`, `select_diverse`,
  `retrieve_digest`, and `build_experience_brief` have callers or public
  exports; no orphan implementation was left.
- `detect` is retained as an intentional compatibility seam and is used by the
  fallback when replaced; it is not an unused import.
- No new module-level core imports were added in `machina/tools`; the audit
  import is bound only on the execution-reporting path.
- `ruff` reports no unused imports or locals across `machina`, `tests`, or
  `tools`.

## Remaining honest gaps

The full 6,472-test DevTools suite was not green on Windows because of the
platform-specific tests above. The relevant memory/routing/config/harness/
gateway subsets are green. The broader architecture ratchets need a separate
baseline reconciliation with DevTools; changing live code to meet an outdated
ceiling would be the wrong optimization.
