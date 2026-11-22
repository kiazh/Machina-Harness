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
| The `MACHINA_HOME` resolver bypassed the exported `DEFAULT_CONFIG_PATH` override used by tests and embedders | Save/load now go through `_resolved_config_path()`: an explicit constant override wins, the environment is re-read otherwise, and a hidden legacy config is disclosed once. `uninstall` uses the same runtime root. |
| Two concurrent first interop writers could race while initializing the Windows lock file, dropping one entry with `PermissionError` | The losing sentinel write re-checks the byte and proceeds to the `msvcrt` lock. `tests/test_interop_state_lock.py` covers three concurrent platform writers. |
| Windows RAG's pathname fallback reported an opened directory as "Path is not accessible" | It preserves the cross-platform "Not a file" contract; a post-validation `RuntimeError` also maps to a validation error. |
| The fast-path interop test inherited the operator's real state file | Its fixture now points `MACHINA_INTEROP_STATE_FILE` and `MACHINA_HOME` at temporary paths. |
| Audit spill and gateway logging resolved `~/.machina` even when `MACHINA_HOME` was set | Audit spill checks the environment at call time, and gateway logging uses the same state root as config; both now follow the test/install isolation contract. |

## Skills-guided pass

The external Superpowers skill pack was inspected at commit `22f0b46` as a
workflow source, not vendored as a Machina dependency. The relevant workflows
were applied directly:

- `systematic-debugging`: every DevTools failure was reproduced before a
  production change; the Windows lock race and RAG message regression were
  traced to their actual call sites.
- `test-driven-development`: the config override and concurrent-state fixes
  each gained a regression test before final verification. The override test
  was observed failing against the old resolver.
- `requesting-code-review`: a read-only reviewer found the
  `DEFAULT_CONFIG_PATH` regression and the Windows/RAG edge cases. Critical
  findings were fixed before this verification table was produced.
- `verification-before-completion`: every result below comes from a fresh
  command on this working tree, except rows explicitly marked prior.

The reviewer run that exposed the config regression wrote the operator's real
config with defaults before the override seam was restored. The final tests
use temporary homes; if that profile had local customizations, restore them
from backup.

## Verification

Fresh on this tree:

| Suite | Result |
| --- | --- |
| Machina backend | 297 passed, 9 skipped |
| Machina terminal UI (typecheck, tests, lint) | 1,208 passed, 10 skipped; exit 0 |
| Desktop unit | 240 passed |
| Desktop contracts | 4 passed |
| Desktop artifact tests | 3 passed |
| DevTools targeted integration set (the 435-test subset used eight explicit Windows-harness deselections) | 435 passed, 1 skipped |
| Meta-harness routing benchmark | 44/44; p50 0.4 ms, p95 0.81 ms; 0 provider calls/tokens |
| Memory digest benchmark | coverage 0.816 → 0.915; hard 0.532 → 0.746; p50 0.233 ms; 0 provider calls |
| Ruff / compileall / `git diff --check` | clean |

Prior unchanged runs:

| Suite | Result |
| --- | --- |
| Desktop E2E (no renderer changes this pass) | 50 passed, 1 skipped |
| Real gateway E2E (provider path unchanged) | 5 passed |
| Real MCP OAuth gateway test | 18 passed |
| DevTools backend collection | 6,472 selected / 8 deselected |

Manual Kiro Claude Haiku 5.5 memory digest E2E (prior run): three seeded
memories, `memory_digest` audit `selected=3 chars=239`, correct answer `Oat
milk flat white`. The temporary memories were deleted.

## DevTools platform gaps

The full DevTools selection is Linux-oriented. On this Windows host the
following are test-harness limitations, not Machina runtime failures:

- `test_installer_env.py` imports the POSIX-only `fcntl`.
- `test_harnesses_core.py::test_run_task_delivers_option_like_prompts_literally`
  installs an extensionless shell stub; Windows resolves the installed
  `opencode.CMD` instead.
- `test_a3_rag_bounded_io.py::test_non_regular_substitution_is_refused` uses
  `os.mkfifo`; the symlink and containment regressions pass.
- `test_a6_release_evidence.py` contains a Windows asyncio loop wait that
  hangs; pytest-timeout reported the hang.
- `test_interop.py::test_detect_uses_list_output_and_config_file_evidence`
  fakes `pgrep`; the Windows branch correctly uses `ps`, so the fake runner
  answers with its safety refusal.
- `test_interop.py::test_state_write_is_atomic_0600_and_reads_back`,
  `test_final_review_cli_config_setup.py::test_setup_force_noninteractive_replaces_env`,
  and `test_provider_store.py::test_store_is_0600_and_never_contains_the_key`
  assert POSIX `0600`; Windows reports `0666` through `stat`.
- `test_a4_skills_fixes.py::test_unlink_failure_keeps_the_live_entry` relies
  on `chmod(0o555)` denying unlink; Windows has no POSIX directory-write bit,
  so the delete succeeds and the test's failure injection never fires.
- `test_r4_080_boot_config.py::test_cli_runs_with_a_non_mapping_config_root`
  passes a minimal subprocess environment without `SYSTEMROOT`/`WINDIR`;
  Windows Python then fails to initialize `_overlapped` with `WinError 10106`
  before Machina starts.
- Architecture ratchet ceilings and provider-package totals were measured
  against an older tree and are already exceeded by this checkout's Kiro /
  provider work. The current run is 19 passed, 7 failed: `core/agent.py`
  2545 > 2303, `core/provider/specs.py` 210 > 189, `core/provider/factory.py`
  167 > 142, provider package 7109 > 5662, deferred imports 933 > 826, `tools`
  core imports 26 > 23, plus three provider submodules importing the facade.
  `core/agent.py` and the provider package were not changed by this pass; the
  new code contributes one function-level `uninstall` import.

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
gateway/RAG subset is green after eight known Windows harness deselections:
435 passed, 1 skipped. The broader architecture ratchets need a separate
baseline reconciliation with DevTools; changing live code to meet an outdated
ceiling would be the wrong optimization.
