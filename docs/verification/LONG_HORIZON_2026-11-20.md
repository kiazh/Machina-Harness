# Multi-stage synthetic long-horizon suite - 2026-11-20

## Verdict

All three harnesses solved all three synthetic tasks. Accuracy therefore does
not separate them on this suite. On cost and latency, Machina used **6.4% fewer
processed prompt tokens than OpenCode** and was **6.4% slower in aggregate**;
OpenCode won the `jobs` task by a wide margin. Hermes was **2.6x slower than
Machina** and used roughly **4.8x** the processed prompt tokens.

This is a three-task, one-trial synthetic result, not evidence of general
superiority. Qwen4 Max was the requested model but its free route was
rate-limited; this run uses DeepSeek V4.1 Flash, as selected by the user. The
same access limitation is recorded in the held-out rerun report.

## Setup

| Item | Value |
| --- | --- |
| Model | DeepSeek V4.1 Flash (`deepseek-v4-flash`) |
| Harnesses | Machina, OpenCode, Hermes |
| Trials | One per task/harness |
| Timeout | 600 seconds per harness |
| Verification | Hidden test file copied in after the harness exits |
| Host | Windows 11, Python 3.12 |

Raw per-run JSON and logs are under
`C:\Users\kiash\AppData\Local\Temp\opencode\long-horizon\`.

## Tasks

Three self-contained Python repositories exercise different long-horizon
shapes. Each starts with a deliberately incomplete implementation and a short
visible smoke test; the scored tests are hidden and are copied in only after
the harness exits.

| Task | Shape | Requirements |
| --- | --- | --- |
| `jobs` | Durable worker + background thread | Exclusive SQLite claims, leases/reclaim, retry backoff, terminal failure, CLI, stop-aware `run_forever` |
| `ledger` | Multi-file persistence + CLI | Idempotent deposits/withdrawals, insufficient funds, daily limit, atomic JSON save, CLI exit semantics |
| `settings` | Configuration migration | APP_/legacy precedence, type/bool parsing, range validation, JSON CLI, error exit |

## Reference validation

A local reference implementation was run against the hidden tests:

| Task | Reference | Initial fixture |
| --- | ---: | ---: |
| `jobs` | 6/6 passed | 1/6 passed |
| `ledger` | 5/5 passed | 1/5 passed |
| `settings` | 18/18 passed | 0/18 passed |

The initial counts confirm the tasks are not satisfied by the starting tree.
The reference counts confirm the hidden tests are internally coherent.

## Results

All three harnesses passed all hidden tests, so this suite does not separate
accuracy. It separates overhead and latency.

| Task | Machina | OpenCode | Hermes |
| --- | ---: | ---: | ---: |
| `settings` | 18/18, 32.4 s | 18/18, 62.8 s | 18/18, 122.2 s |
| `ledger` | 5/5, 90.3 s | 5/5, 130.8 s | 5/5, 327.2 s |
| `jobs` | 6/6, 196.9 s | 6/6, 105.5 s | 6/6, 381.2 s |
| **Total** | **3/3 tasks, 319.7 s** | **3/3 tasks, 299.1 s** | **3/3 tasks, 830.6 s** |

OpenCode was **6.4% faster in aggregate** than Machina, mostly because of the
`jobs` task. Machina was faster on `settings` and `ledger`. Hermes was **2.6x
slower than Machina** in aggregate.

Token accounting (raw prompt is not like-for-like because OpenCode reports
uncached input separately):

| Harness | Prompt tokens | Cache read | Visible completion | Processed prompt |
| --- | ---: | ---: | ---: | ---: |
| Machina | 520,477 | 479,232 | 34,802 | **520,477** |
| OpenCode | 27,480 | 528,768 | 16,371 | 556,248 (`input + cache`) |
| Hermes | 2,497,925 | 2,413,824 | 35,367 | 2,497,925 |

Machina used **6.4% fewer processed prompt tokens than OpenCode** and **79%
fewer than Hermes**. OpenCode separately reported 16,981 reasoning tokens.
These are one-trial totals; no confidence interval is claimed.

The scoped result is: on these three synthetic multi-stage tasks, all three
harnesses solved the hidden tests; Machina had the lowest processed-token
total, OpenCode had the lowest aggregate wall time by a small margin, and
Hermes was much more expensive and slower. That is not evidence of general
superiority.

## Limits

- Synthetic tasks, one trial per harness; not a broad coding benchmark.
- One model and one Windows host.
- Hidden tests are local and were authored for these fixtures.
- Wall time includes provider latency and local load. The three fixtures were
  run while a separate held-out benchmark was active, so absolute wall times
  are indicative rather than a quiet-host measurement; every harness saw the
  same overlap.
- The `settings` and `ledger` tasks are multi-file but not long-running. The
  `jobs` task exercises a background thread and lease/reclaim behavior.
- No pass@k, cost, or p50/p95 claim.
