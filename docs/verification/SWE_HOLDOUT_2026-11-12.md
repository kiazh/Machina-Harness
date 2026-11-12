# SWE-bench held-out check — 2026-11-12

## Verdict

**Not yet best.** On two fresh, gold-validated pytest tasks, Machina, OpenCode,
and Hermes all solved both. Machina was slower than OpenCode on the exact same
model. This is a small pass@1 check, not a competitive ranking.

The result is recorded because it falsifies a “best harness” claim and gives a
concrete latency target for the next iteration.

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

## Results before the latest optimization

| Harness | Solved | Time (s) | Model calls | Tokens |
|---|---:|---:|---:|---:|
| Machina | 2/2 | 418.9 | 69 | 1,366,958 |
| OpenCode | 2/2 | 98.7 | 25 | 440,388 (logged categories) |
| Hermes | 2/2 | 294.5 | not logged | not logged |

Machina’s tokens are `prompt + completion` from its durable usage ledger.
OpenCode’s number is the sum of its logged input, output, and cache-read
categories; Hermes did not expose a comparable ledger in this run.

## Post-optimization development check

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
