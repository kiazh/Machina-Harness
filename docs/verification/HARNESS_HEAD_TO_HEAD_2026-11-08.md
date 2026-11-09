# Harness head-to-head benchmark - 2026-11-08

Scope: same model, same seven hidden-test tasks, isolated temporary
workspaces. This is a local harness comparison, not a Terminal-Bench result.

## Setup

| Item | Value |
| --- | --- |
| Model | `claude-haiku-5.5` |
| Machina | v1.2.14, headless CLI, full-host mode, normal agent stack |
| Kiro harness | `kiro-cli` 3.1.0, default engine, tools trusted |
| Hermes | v0.22.5, one-shot, safe mode, custom Kiro endpoint |
| Tasks | 5 basic implementation tasks + ledger validation + CLI/file report |
| Scoring | Hidden `unittest` files applied after each run; tests are never visible to the agent |
| Artifacts | `C:\Users\kiash\AppData\Local\Temp\opencode\harness-bench` (outside the repo) |

Each harness received the same prompt, working directory contents, model, and
timeout (240 seconds per task). A task passed only if the hidden tests passed
after the harness exited. Machina and Hermes used the local Kiro gateway at
`http://127.0.0.1:8000/v1`; Kiro CLI used its native Kiro route. All requests
resolved to `claude-haiku-5.5`.

## Results

| Harness | Passed | Total | Average |
| --- | ---: | ---: | ---: |
| Kiro CLI | **7/7** | **124.34 s** | **17.76 s** |
| Machina | **7/7** | **375.59 s** | **53.66 s** |
| Hermes | **7/7** | **454.84 s** | **64.98 s** |

Per task:

| Task | Machina | Kiro | Hermes |
| --- | ---: | ---: | ---: |
| slugify | 67.4 s | 14.2 s | 73.3 s |
| chunk | 49.4 s | 19.0 s | 57.7 s |
| duration | 61.9 s | 21.9 s | 64.8 s |
| intervals | 42.7 s | 15.7 s | 54.7 s |
| top_words | 34.8 s | 14.4 s | 54.6 s |
| ledger | 45.3 s | 15.4 s | 66.2 s |
| report_cli | 74.2 s | 23.7 s | 83.6 s |

Machina recorded **167,366 total tokens** across the seven runs in its audit
logs. Kiro and Hermes did not expose comparable per-run token totals in the
chosen output modes, so no token-efficiency ranking is claimed.

Kiro account usage after the benchmark: **42.32 / 1,000 credits** used
(**957.68 remaining**). This includes the other Kiro calls made during this
work, so it is not an isolated benchmark cost.

## Interpretation

- Correctness was tied on this suite: every harness passed every hidden task.
  The suite therefore has a ceiling and does **not** rank general coding
  quality or hard-task capability.
- Kiro CLI was the fastest harness on every task, about **3.0x faster than
  Machina** and **3.7x faster than Hermes** in total wall time.
- Machina's extra time is consistent with its larger runtime path: audit,
  memory/context assembly, routing, and its normal agent/tool loop. This run
  does not isolate which component dominates.
- Hermes was the slowest here; its one-shot startup and loop overhead dominate
  these small tasks.

## Limits

- One run per harness/task; timing is descriptive, not a confidence interval.
- The seven tasks are small and local. No Docker was available on the host or
  in WSL, so Terminal-Bench could not be run; this is not a substitute for it.
- Configuration surfaces differ by harness: Kiro ran with trusted tools, Hermes
  with `--safe-mode --yolo`, Machina with full-host mode and its normal agent
  stack. Those settings are disclosed rather than normalized away.
- The benchmark runner and raw results are outside the repository and are not
  committed.

## Reproduction

The runner and raw results are under
`C:\Users\kiash\AppData\Local\Temp\opencode\harness-bench`:

```powershell
python run_harness_bench.py   # five basic tasks
python run_hard_bench.py --tasks ledger,report_cli   # two harder tasks
python summarize.py
```

The machine-readable summary is `final-summary.json` in that directory.
