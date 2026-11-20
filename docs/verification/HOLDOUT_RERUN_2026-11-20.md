# Held-out SWE-bench Lite rerun - 2026-11-20

## Verdict

OpenCode solved **10/10**, Machina **9/10**, and Hermes **7/10** on this
one-trial held-out rerun. Machina used **46% fewer processed prompt tokens**
than OpenCode but was **20.6% slower**. Machina improved from 5/10 on the
2026-11-10 baseline to 9/10 on the current build, but this is still **not an
accuracy win**; OpenCode remains the accuracy and latency leader.

## Why this run uses DeepSeek

Qwen4 Max was reachable as `qwen/qwen4-max:free` through the local Hermes/Nous
proxy, but the free route returned HTTP 429 fair-share rate limits
(`retry-after` 60-69 seconds) during smoke runs. This machine has no paid
Qwen4 Max route: the OpenRouter key is rejected with HTTP 401, the Nous Portal
account has zero credits, and Qwen OAuth is not signed in. The user selected
DeepSeek V4.1 Flash for this rerun, so no Qwen4 Max result is claimed.

## Method

| Item | Value |
| --- | --- |
| Model | DeepSeek V4.1 Flash (`deepseek-v4-flash`) |
| Tasks | The ten held-out SWE-bench Lite instances from 2026-11-10 |
| Gold gate | Applied test patch and gold patch on a pristine clone; FAIL_TO_PASS |
| Trials | One per task/harness |
| Timeout | 600 seconds per harness |
| Verification | Hidden test patch applied to a pristine copy; agent test edits discarded |
| Harnesses | Machina, OpenCode, Hermes |
| Host | Windows 11, Python 3.12 / per-task Python 3.9 or 3.12 |

All ten tasks passed the local gold gate:

| Task | Gold FAIL_TO_PASS |
| --- | --- |
| `psf__requests-1963` | 7 passed |
| `pytest-dev__pytest-11143` | 1 passed |
| `pytest-dev__pytest-11148` | 2 passed |
| `pytest-dev__pytest-5103` | 1 passed |
| `pytest-dev__pytest-5227` | 1 passed |
| `pytest-dev__pytest-5413` | 1 passed |
| `pytest-dev__pytest-7168` | 11 passed |
| `pytest-dev__pytest-7220` | 1 passed |
| `pytest-dev__pytest-7373` | 1 passed |
| `pytest-dev__pytest-7432` | 1 passed |

The two 2019 pytest tasks needed `setuptools<81` so their source tree could use
`pkg_resources` under Python 3.9; no product code was changed for this.

## Results

Times are seconds. `T` marks a run that exceeded the 600-second cap but still
left a patch that passed the hidden tests.

| Instance | Machina | OpenCode | Hermes |
| --- | ---: | ---: | ---: |
| `psf__requests-1963` | PASS 151.5 | PASS 28.5 | PASS 214.3 |
| `pytest-dev__pytest-11143` | PASS 54.3 | PASS 49.7 | PASS 187.4 |
| `pytest-dev__pytest-11148` | PASS 223.5 | PASS 67.7 | PASS 600.0 T |
| `pytest-dev__pytest-5103` | PASS 455.4 | PASS 723.1 T | FAIL 600.1 T |
| `pytest-dev__pytest-5227` | PASS 127.0 | PASS 65.4 | PASS 412.0 |
| `pytest-dev__pytest-5413` | FAIL 77.7 | PASS 70.7 | FAIL 526.2 |
| `pytest-dev__pytest-7168` | PASS 88.7 | PASS 40.9 | PASS 331.6 |
| `pytest-dev__pytest-7220` | PASS 343.7 | PASS 237.8 | FAIL 600.2 T |
| `pytest-dev__pytest-7373` | PASS 75.1 | PASS 48.5 | PASS 239.8 |
| `pytest-dev__pytest-7432` | PASS 134.8 | PASS 43.4 | PASS 119.4 |
| **Verified patch total** | **9/10** | **10/10** | **7/10** |
| **Total wall** | 1,731.6 s | **1,375.9 s** | 3,831.2 s |
| **Timeout cap (600 s)** | 0 | 1 | 3 |

Token accounting:

| Harness | Prompt tokens | Cache read | Visible completion | Processed prompt |
| --- | ---: | ---: | ---: | ---: |
| Machina | 5,112,509 | 4,795,904 | 99,081 | **5,112,509** |
| OpenCode | 297,983 | 9,221,376 | 31,428 | 9,519,359 (`input + cache`) |
| Hermes | 8,207,627 | 7,952,768 | 48,386 | 8,207,627 (incomplete; 3 tasks have no usage file) |

Machina used **46% fewer processed prompt tokens than OpenCode** on this
10-task run. OpenCode was **20.6% faster in wall time** and solved one more
task. Hermes is not comparable on tokens because its timed-out runs did not
emit usage files; its recorded wall time was 2.2x Machina's.

The result is a real improvement for Machina over the 2026-11-10 run (5/10 to
9/10 on the same ten tasks), but it is **not** an accuracy win: OpenCode solved
all ten. The remaining Machina failure is `pytest-5413`, the same exception
repr task that failed in the earlier run.

### Anomalies and corrections

- `pytest-7168` OpenCode first exited immediately because the full issue
  exceeded the Windows command-line limit. The clean rerun attached the exact
  prompt as `PROMPT.txt`; it passed in 40.9 s. The table uses the rerun.
- `pytest-5103` OpenCode from the first runner took 723.1 s because that runner
  did not kill a detached child at the nominal timeout. The patch was verified.
  The corrected runner now kills the process tree; the result is marked `T`.
- `pytest-5103` Machina produced a verified patch but its CLI exited 1.
  External tests are authoritative here; it is counted as a pass.
- `pytest-11148` Hermes hit the 600 s cap but left a verified patch; counted as
  a pass and marked `T`.
- `pytest-5103` Hermes and `pytest-7220` Hermes timed out with no passing patch
  and are counted as failures.
- One `pytest-5103` OpenCode rerun was skipped because an old child process
  held the directory; the verified 723.1 s run is retained.

## Limits

- One trial per task/harness; no pass@k or confidence interval.
- Native runner, not the official SWE-bench Docker harness.
- FAIL_TO_PASS only; PASS_TO_PASS is contaminated by Windows environment
  failures, as in the earlier report.
- Ten tasks from one local selection; no leaderboard position is claimed.
- OpenCode's 10/10 includes one run over the 600-second cap; within the cap it
  is 9/10. Machina is 9/10 with no timeouts. Hermes is 7/10 with three timeouts.
- Raw JSON and logs:
  `C:\Users\kiash\AppData\Local\Temp\opencode\swe-bench-qwen4\`.
