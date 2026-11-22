# Held-out SWE-bench Lite attempt - 2026-11-20

## Verdict

**No ranking result is reported.** The attempted rerun was excluded after a
benchmark-integrity audit. The failure is in the runner and environment, not a
claim that the harnesses are equal.

Two independent leaks were found:

1. **Future Git history.** The first run copies retained the full upstream
   clone. OpenCode ran:

   ```text
   git diff 042a10f2e 65c2a8192 -- src/_pytest/_code/code.py testing/code/test_excinfo.py changelog
   ```

   That exposed the upstream fix. Rebuilding `.git` at the base snapshot
   prevents this in the corrected runner.

2. **Live upstream access.** A later run used a local allowlist proxy, but
   `curl` launched from the task shell still reached GitHub PR diffs. Machina
   fetched the upstream patch during `pytest-dev__pytest-5413`. Blocking
   uppercase proxy variables is not enough for every child shell; the
   environment needs an OS-level network policy or a shell shim that cannot be
   bypassed, not just advisory proxy variables.

A third environment issue appeared when the network was restricted: the
`psf__requests-1963` hidden tests require live `httpbin.org`. With the proxy
blocking it, six tests fail for lack of the fixture service even when the
source repair is correct. The benchmark needs a local httpbin-compatible
fixture, not public network access.

## Partial corrected run (not a ranking)

After adding Git isolation and the first proxy attempt, the run reached six
tasks before it was stopped for the integrity audit. These observations are
kept only as diagnostics; they are not a valid ten-task score:

| Task | Machina | OpenCode | Hermes |
| --- | --- | --- | --- |
| `pytest-11143` | PASS | PASS | PASS |
| `pytest-11148` | FAIL | PASS | FAIL |
| `pytest-5103` | FAIL | PASS | PASS |
| `pytest-5227` | PASS | PASS | PASS |
| `pytest-5413` | PASS | PASS | FAIL |
| `requests-1963` | invalid (httpbin blocked) | invalid | invalid |

The useful signal is that `pytest-5413` now passes for Machina in the corrected
run. The remaining Machina failures are `11148` and `5103`, not `5413`.
`requests-1963` cannot be scored under the restricted network until a local
httpbin fixture exists.

## What was learned

- Git isolation and network isolation are separate requirements.
- A local HTTP proxy that honors uppercase variables is not a security
  boundary; children can bypass it.
- `requests` F2P tests are network tests in this environment and must use a
  local fixture.
- Model prior knowledge cannot be removed; a valid comparison must at least
  eliminate access to the future repository state and live upstream fixes.
- The 2026-11-20 numbers previously written to the README were withdrawn.
  They must not be used for a ranking.

## Corrected-run state

The corrected runner now:

- rebuilds `.git` as one base snapshot before the agent starts;
- sets both uppercase and lowercase proxy variables;
- blocks future history and arbitrary web access through the allowlist proxy;
- records the blocked-host log.

It still does **not** provide a non-bypassable network boundary, and the
`httpbin`-dependent tests are not valid under the restricted network. Therefore
this file does not publish a pass count, token total, or winner.

Long-horizon synthetic results are in
[`LONG_HORIZON_2026-11-20.md`](LONG_HORIZON_2026-11-20.md); those fixtures have
no upstream gold patch and no external test service.

## Required next gate

A defensible rerun needs all of the following before it can replace the
2026-11-10 baseline:

1. A single-commit task snapshot with no future refs.
2. A network namespace/firewall that allows only the model API; agents cannot
   unset it from a shell.
3. A local fixture for `httpbin` and any other network-dependent tests.
4. External hidden-test verification with test edits excluded.
5. At least three trials per task and an integrity audit of every log.

Until then, the README keeps the historical 2026-11-10 result and marks this
attempt as excluded.
