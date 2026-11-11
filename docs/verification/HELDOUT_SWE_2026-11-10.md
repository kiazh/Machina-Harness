# Held-out SWE-bench Lite evaluation - 2026-11-10

This report corrects the earlier six-task result. The six tasks in
`SWE_BENCH_LITE_SUBSET_2026-11-08.md` were used while developing the repair
protocol; their 5/6 score is a development-set result, not evidence that the
harness generalizes. This report uses tasks that were selected before the
held-out runs and were never used for harness changes.

The headline result is negative for Machina: it does **not** beat the other
harnesses on these held-out tasks, and it is substantially slower.

## Method

- Source: the local `princeton-nlp/SWE-bench_Lite` test split
  (`swe-bench/test.parquet`, 300 instances).
- Held-out set: after removing the six development tasks, the remaining
  Requests and pytest instances were sorted by instance ID; the first ten were
  selected. No task-level prompt or source changes were made after selection.
- Validity gate per task:
  1. the base commit must fail the hidden `FAIL_TO_PASS` tests; and
  2. the dataset's gold `patch` must pass the same tests locally.
- A task that failed either gate was excluded from scoring. Three Requests
  tasks (`2148`, `2317`, `2674`) failed the gold gate because their hidden
  tests depend on live httpbin/live-network behavior in this environment.
  `psf__requests-863` passed the hidden test on the base commit and was also
  excluded. No invalid task is counted as a harness failure.
- Hidden tests were never shown to an agent. Agent edits to test files were
  excluded from the scored source patch.
- Scoring is `FAIL_TO_PASS` only. The older pytest pass-to-pass sets have
  Windows-specific py.path/symlink/collection failures; those are reported
  separately and not used to rank the harnesses.

## Model tracks

| Track | Model | Harnesses | Valid held-out tasks |
| --- | --- | --- | --- |
| Haiku | `claude-haiku-5.5` through the local Kiro gateway | Machina, Kiro CLI, Hermes | 6 |
| DeepSeek | `deepseek-chat` / DeepSeek V4.1 Flash, direct API | Machina, OpenCode, Hermes | 10 |

The OpenCode harness was not usable on the Haiku track because it was configured
for the Kiro/Anthropic route; Kiro CLI was not usable on the DeepSeek track
because it is tied to the Kiro account. Each track is compared only against
harnesses running the **same model**.

## Results

### Haiku 5.5, six valid held-out tasks

| Instance | Machina | Kiro | Hermes |
| --- | --- | --- | --- |
| `psf__requests-1963` | FAIL | **PASS** | **PASS** |
| `pytest-dev__pytest-11143` | **PASS** | **PASS** | **PASS** |
| `pytest-dev__pytest-11148` | FAIL | FAIL | FAIL |
| `pytest-dev__pytest-5103` | **PASS** | **PASS** | **PASS** |
| `pytest-dev__pytest-5227` | **PASS** | **PASS** | **PASS** |
| `pytest-dev__pytest-5413` | FAIL | FAIL | FAIL |
| **Total** | **3/6** | **4/6** | **4/6** |

### DeepSeek V4.1 Flash, ten valid held-out tasks

| Instance | Machina | OpenCode | Hermes |
| --- | --- | --- | --- |
| `psf__requests-1963` | **PASS** | **PASS** | **PASS** |
| `pytest-dev__pytest-11143` | **PASS** | **PASS** | **PASS** |
| `pytest-dev__pytest-11148` | **PASS** | **PASS** | **PASS** |
| `pytest-dev__pytest-5103` | FAIL | **PASS** | **PASS** |
| `pytest-dev__pytest-5227` | **PASS** | FAIL | FAIL |
| `pytest-dev__pytest-5413` | FAIL | **PASS** | FAIL |
| `pytest-dev__pytest-7168` | FAIL | **PASS** | FAIL |
| `pytest-dev__pytest-7220` | FAIL | **PASS** | FAIL |
| `pytest-dev__pytest-7373` | FAIL | **PASS** | **PASS** |
| `pytest-dev__pytest-7432` | **PASS** | **PASS** | **PASS** |
| **Total** | **5/10** | **9/10** | **6/10** |

### Wall time

| Track | Harness | Total wall time | Mean | 900 s timeouts |
| --- | --- | ---: | ---: | ---: |
| Haiku | Machina | **1,494.19 s** | 249.0 s | 0 |
| Haiku | Kiro | **474.01 s** | 79.0 s | 0 |
| Haiku | Hermes | **1,375.34 s** | 229.2 s | 0 |
| DeepSeek | Machina | **6,877.94 s** | 687.8 s | **5** |
| DeepSeek | OpenCode | **582.08 s** | 58.2 s | 0 |
| DeepSeek | Hermes | **2,850.03 s** | 285.0 s | 0 |

Machina was the slowest harness in both tracks. On DeepSeek it consumed the
900-second per-task cap on five tasks; two of those still produced a passing
patch, but the time cost is not competitive.

## Interpretation

The held-out evidence does not support a claim that Machina is better than
modern harnesses:

- On Haiku, Machina solves 3/6 versus Kiro 4/6 and Hermes 4/6.
- On DeepSeek, Machina solves 5/10 versus OpenCode 9/10 and Hermes 6/10.
- Machina is slower than every comparator and repeatedly exhausts the task
  timeout.
- The earlier development-set 5/6 was not reproduced on unseen tasks.

The harness changes made during development are real: recursive source search,
protected repair instructions, child-environment preservation, and Windows
task-venv resolution. They improved the development subset from 1/6 to 5/6.
They did not produce a general advantage on the held-out sets.

## Post-fix repair-loop probe

After the held-out report, two general harness fixes were added:

1. Windows terminal commands can opt into Git Bash (`MACHINA_TERMINAL_SHELL=bash`),
   avoiding `cmd.exe` quote failures and giving the agent POSIX semantics.
2. A repair turn that ends with a clean git tree gets one continuation instead
   of being accepted as complete.

Targeted DeepSeek reruns against the same hidden tests:

| Instance | Before | After |
| --- | --- | --- |
| `pytest-dev__pytest-7373` | FAIL, 707.57 s, no source change | **PASS, 171.93 s** |
| `pytest-dev__pytest-5413` | FAIL, 901.20 s, no source change | FAIL, 411.90 s, source changed |
| `pytest-dev__pytest-7168` | FAIL, 287.80 s | FAIL, 192.22 s, source changed |

This is a targeted probe, not a full rerun. It shows the intended effects
(fewer no-change completions, much less time spent on shell quoting), but it
does not turn Machina into the best harness on its own.

## Fresh post-change validation

Two untouched pytest tasks were gold-validated and then run with the same
DeepSeek model across Machina, OpenCode, and Hermes:

| Instance | Machina | OpenCode | Hermes |
| --- | --- | --- | --- |
| `pytest-dev__pytest-7490` | FAIL | FAIL | FAIL |
| `pytest-dev__pytest-8365` | FAIL | **PASS** | FAIL |
| **Total** | **1/2** | **2/2** | **1/2** |

Wall time: Machina 332.90 s, OpenCode 108.68 s, Hermes 306.88 s. This is a
small fresh sample, but it confirms that the shell/nudge improvements did not
make Machina the best harness.

## Harbor Terminal-Bench adapter

`integrations/harbor/machina_agent.py` now provides a real Harbor adapter. It
installs a bundled Machina wheel, PostgreSQL, and pgvector in the task
container, initializes Machina, runs the full-mode CLI, and lets Harbor score
the task.

Verified on the Terminal-Bench `data-anonymization` task on 2026-11-10:

- 1 trial completed
- 0 infrastructure exceptions
- reward **0.0**
- runtime 6m 35s

The adapter path is operational, but Machina did not solve that task. It is not
yet a general Terminal-Bench score.

## Terminal-Bench attempt

Terminal-Bench 2.0 was installed through Harbor (66 tasks downloaded) and an
Oracle run on `data-anonymization` passed (1.0). A fair agent comparison could
not be completed in this session:

- OpenCode installation inside the task container failed because npm selected
  the musl binary on a glibc image.
- Hermes installation inside the task container failed while cloning its
  repository because of container egress resets.
- Machina has no Harbor adapter and needs a PostgreSQL service inside the task
  container; installing and starting one is additional adapter work.

The Oracle result validates the task environment, but no Terminal-Bench agent
score is claimed.

## Raw artifacts

All raw JSON is outside the repository under:

`C:\Users\kiash\AppData\Local\Temp\opencode\swe-bench\`

Relevant run roots:

- Haiku: `runs-20261110-123608`, `runs-20261110-144252`,
  `runs-20261110-151354`, `runs-20261110-152058`, `runs-20261110-152555`
- DeepSeek: `runs-20261110-161624`, `runs-20261110-163117`,
  `runs-20261110-165830`, `runs-20261110-174909`,
  `runs-20261110-182012`, `runs-20261110-185242`

The runner and task files are outside the repository:

- `run_swe_bench.py`
- `tasks_holdout.json`
- `tasks_holdout2.json`
- `test.parquet`

## Limits

- Six Haiku tasks and ten DeepSeek tasks are small samples; no leaderboard
  position is claimed.
- The evaluator is local, not the official SWE-bench Docker harness.
- Some old Requests tests depend on live network/httpbin behavior and were
  excluded by the gold gate rather than counted.
- Pytest pass-to-pass sets remain contaminated by Windows-specific behavior;
  the ranking above is `FAIL_TO_PASS` only.
