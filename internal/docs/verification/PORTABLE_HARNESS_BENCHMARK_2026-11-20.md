# Portable harness microbenchmark - 2026-11-20

## Verdict

This benchmark measures **prompt overhead and a trivial repair loop**, not
general coding ability. On these two tasks:

- All harnesses produced the correct answer and passed the external pytest
  verifier.
- Machina used **39% fewer processed prompt tokens than OpenCode** on the code
  task and **59% fewer** on the exact-answer task.
- Machina was fastest on the exact-answer task, but **13% slower than OpenCode**
  on code repair.
- The result therefore supports a **token-efficiency** claim, not a general
  "Machina is better" claim.

The broader held-out SWE-bench Lite result is negative for Machina:
[`HELDOUT_SWE_2026-11-10.md`](HELDOUT_SWE_2026-11-10.md). Read that report
before quoting this one.

## Method

| Item | Value |
| --- | --- |
| Date | 2026-11-20 |
| Model | DeepSeek V4.1 Flash (`deepseek/deepseek-v4-flash`) |
| Host | Windows 11, Python 3.12, local PostgreSQL where used |
| Workspace | Fresh temporary Git repository per trial |
| Exact trials | 3 per harness |
| Code-repair trials | Machina 5; OpenCode 3; Hermes 3 |
| Code verification | External `python -m pytest -q` after the harness exits |
| Machina config | `MACHINA_EXECUTION_MODE=full`, memory and RAG disabled |
| OpenCode config | `--standalone --auto --format json` |
| Hermes config | `--provider deepseek --model deepseek-v4-flash --reasoning none -z` |

The prompts were identical across harnesses:

```text
Reply with exactly: OK
```

```text
You are in a small Python repository. Fix the bug in calc.py so test_calc.py
would pass. Do not edit tests. Make the smallest source edit only.
Reply with exactly one line: TEST: PASS or TEST: FAIL.
```

The code fixture was:

```python
# calc.py
def add(a, b):
    return a - b
```

```python
# test_calc.py
from calc import add


def test_add():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
```

The prompt forbade test edits. This runner did **not** hash the test tree; a
separate held-out runner does. Treat this as a harness-overhead probe, not a
security or correctness proof.

## Raw trials

### Exact answer

| Harness | Wall (s) | Prompt tokens | Cache reads | Visible completion |
| --- | --- | ---: | ---: | ---: |
| Machina | 3.468 / 2.981 / 3.259 | 2,923 / 2,923 / 2,923 | 2,688 / 2,688 / 2,688 | 2 / 2 / 2 |
| OpenCode | 3.011 / 3.505 / 3.658 | 3,653 / 3,820 / 3,820 uncached; 2,944 / 3,328 / 3,328 cache | 2,944 / 3,328 / 3,328 | 2 / 8 / 8 |
| Hermes | 5.193 / 5.107 / 4.837 | 16,754 / 16,754 / 16,754 | 4,736 / 4,736 / 4,736 | 1 / 1 / 1 |

OpenCode's processed prompt is `uncached input + cache read`:
6,597 / 7,148 / 7,148. The median is 7,148.

### Code repair

| Harness | Wall (s) | Prompt tokens | Cache reads | Visible completion |
| --- | --- | ---: | ---: | ---: |
| Machina | 11.970 / 20.205 / 12.456 / 10.866 / 12.118 | 17,481 / 32,761 / 17,647 / 17,107 / 17,325 | 13,568 / 30,592 / 16,896 / 16,384 / 16,512 | 721 / 1,037 / 745 / 442 / 567 |
| OpenCode | 11.294 / 10.625 / 10.683 | 4,562 / 4,543 / 4,783 uncached; 23,936 / 23,936 / 30,848 cache | 23,936 / 23,936 / 30,848 | 351 / 357 / 420 |
| Hermes | 20.423 / 17.962 / 17.744 | 108,137 / 90,165 / 90,305 | 91,904 / 74,112 / 74,368 | 311 / 355 / 440 |

OpenCode's processed prompt is 28,498 / 28,479 / 35,631. The median is 28,498.
OpenCode additionally reported reasoning tokens: 218 / 71 / 103; these are not
in the prompt or visible-completion columns.

## Medians used in the README

| Task | Harness | Pass | Wall | Processed prompt | Visible completion |
| --- | --- | ---: | ---: | ---: | ---: |
| Exact | Machina | 3/3 | 3.26 s | 2,923 | 2 |
| Exact | OpenCode | 3/3 | 3.51 s | 7,148 | 8 |
| Exact | Hermes | 3/3 | 5.11 s | 16,754 | 1 |
| Code | Machina | 5/5 | 12.12 s | 17,481 | 721 |
| Code | OpenCode | 3/3 | 10.68 s | 28,498 | 357 |
| Code | Hermes | 3/3 | 17.96 s | 90,305 | 355 |

Prompt and completion medians are computed independently. They are not
same-trial sums. Cache reads are medians. Completion excludes separately
reported reasoning tokens.

## Limits

- Two synthetic tasks. The code task is a one-character arithmetic bug.
- One model, one Windows host, one run sequence; no confidence intervals.
- External harnesses ran their defaults; permissions and network policy were
  not identical in a formal sense.
- Provider cache accounting differs. OpenCode reports uncached input and cache
  reads separately; Machina and Hermes include cache reads in input.
- Prompt caching, model routing, and memory were not exercised as task-solving
  features. Machina memory and RAG were disabled.
- The runner did not hash the test file after the run.
- No official SWE-bench, Terminal-Bench, OSWorld, or tau3 score is claimed.
- Raw artifacts are outside the repository under
  `C:\Users\kiash\AppData\Local\Temp\opencode\harness-bench`; the runner is
  `bench_harnesses.py` in the same directory. The numbers are reproducible only
  on a similar host and provider route.

## What a real claim would require

The protocol in [`docs/BENCHMARKING.md`](../BENCHMARKING.md) is the gate:
fresh held-out tasks, at least three trials, identical model and policy,
external verifiers, pass@1 and pass^k, tokens per resolved task, cache ratio,
known cost, p50/p95 latency, timeout rate, and verifier integrity. Until that
runs and wins, the README must say "token-efficient on measured tasks", not
"better overall".
