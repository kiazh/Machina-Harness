# SWE-bench held-out check — 2026-11-12

## Verdict

**Competitive on this small clean-history set; not yet proven best overall.**

On two fresh, gold-validated pytest tasks, Machina, OpenCode, and Hermes all
solved both. Machina was 1.24× slower than OpenCode and used 72% fewer tokens.
That is within the latency gate and well inside the token gate, but OpenCode is
still faster, so “best all-rounder” is not established.

A later trajectory audit showed that the first benchmark clone retained future
Git refs, and Machina used `git log`/`git show` to inspect a later fix commit
before patching. That is hidden-test contamination. The runner now reinitializes
Git with a single base snapshot before the agent starts; all numbers below are
from the corrected run.

## Setup

- Model: `deepseek-flash` for all three harnesses
  - Machina: direct DeepSeek API
  - OpenCode: `deepseek/deepseek-flash`
  - Hermes: direct DeepSeek API with the same model id
- Task set: `pytest-dev__pytest-5495`, `pytest-dev__pytest-5692`
- Both tasks were absent from the earlier development/holdout manifests.
- Gold validation: both tasks pass their hidden tests with the gold patch.
- Ranking criterion: `FAIL_TO_PASS` tests. `PASS_TO_PASS` is contaminated by
  Windows environment failures and is not used for this comparison.
- One trial per task. No pass^k claim.

## Contaminated pre-isolation run (excluded)

| Harness | Solved | Time (s) | Model calls | Tokens |
|---|---:|---:|---:|---:|
| Machina | 2/2 | 418.9 | 69 | 1,366,958 |
| OpenCode | 2/2 | 98.7 | 25 | 440,388 (logged categories) |
| Hermes | 2/2 | 294.5 | not logged | not logged |

These numbers are retained only to show the effect of contamination and
optimization. Do not use them for a harness ranking.

Machina’s tokens are `prompt + completion` from its durable usage ledger.
OpenCode’s number is the sum of its logged input, output, and cache-read
categories; Hermes did not expose a comparable ledger in this run.

## Clean-history comparison

After the runner was changed to expose only a base snapshot:

| Harness | Solved | Total time (s) | Tokens | Model calls |
|---|---:|---:|---:|---:|
| Machina | 2/2 | 205.4 | 279,735 | 30 |
| OpenCode | 2/2 | 166.1 | 996,249 | 32 |
| Hermes | 2/2 | 374.0 | not logged | not logged |

Per task:

| Harness | `pytest-5495` | `pytest-5692` |
|---|---:|---:|
| Machina | 101.1 s | 104.3 s |
| OpenCode | 125.4 s | 40.7 s |
| Hermes | 146.1 s | 227.9 s |

Machina is **1.24× slower than OpenCode overall**, faster than Hermes, and uses
**72% fewer tokens than OpenCode**. It meets the 2× latency and 1.5× token gates
on this set. OpenCode remains the latency winner.

## Optimization diagnostics (shared-history, excluded)

The same pathological task (`pytest-dev__pytest-5495`) was rerun after:

- adding Git Bash Unix tools to the child `PATH` preflight;
- restricting the repair tool schema to file/terminal/code/artifact tools;
- adding no-edit, artifact-paging, job-polling, and finalize nudges;
- requiring a single test node before broader verification;
- recognizing a direct behavior check as verification evidence.

| Run | Solved | Time (s) | Model calls | Tokens |
|---|---:|---:|---:|---:|
| Before optimization | yes | 413.1 | 55 | 951,017 prompt + 16,445 completion |
| After optimization | yes | 164.5 | 26 | 284,549 prompt + 7,442 completion |

The optimized run is **2.5× faster** and uses **72% fewer prompt tokens** than
the pre-optimization run, while still solving the hidden task. It remains
slower than OpenCode on the same task and model, so the competitive claim is
still unproven.

## Benchmark-integrity finding

The runner used a shared clone and checked out the base commit without removing
future objects or refs. An agent could therefore run `git log` or `git show`
against the upstream fix. The corrected runner replaces `.git` with a fresh
single-commit repository at the base snapshot, so only the base state and its
own commits/refs exist during the trial. Any earlier result produced by the
shared clone is excluded from the competitive evidence.

## What this does not show

- No multi-trial pass^k.
- No statistical confidence interval.
- No Terminal-Bench, OSWorld, or tau3 result.
- No token efficiency comparison against Hermes (no ledger).
- No claim that OpenCode’s latency advantage survives broader task coverage.

## Next gate

Run at least ten fresh held-out tasks, three trials each, with identical model,
tool/network policy, and verifier configuration. Report pass@1, pass^3, tokens
per resolved task, cost, p50/p95 latency, and verifier integrity. Until then:
**accuracy is competitive on this small set; overall best is not established.**
