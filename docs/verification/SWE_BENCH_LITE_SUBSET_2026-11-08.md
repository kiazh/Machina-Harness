# SWE-bench Lite subset - harness comparison - 2026-11-08

Scope: six real issue-resolution tasks from the SWE-bench Lite test split,
run with the same model and hidden-test protocol across three harnesses.

This is **not** an official SWE-bench leaderboard score: it is a local
six-instance subset evaluated against the dataset's hidden test patches.

## Setup

| Item | Value |
| --- | --- |
| Model | `claude-haiku-5.5` |
| Machina | v1.2.14, headless CLI, full-host mode, normal agent stack |
| Kiro harness | `kiro-cli` 3.1.0, default engine, tools trusted |
| Hermes | v0.22.5, one-shot, safe mode, custom Kiro endpoint |
| Dataset | `princeton-nlp/SWE-bench_Lite` test split |
| Instances | 3 Flask, 1 Requests, 2 pytest |
| Scoring | Fresh repo at `base_commit`; hidden `test_patch` applied after the agent; source diff applied to a clean checkout; `FAIL_TO_PASS` run |
| Raw artifacts | `C:\Users\kiash\AppData\Local\Temp\opencode\swe-bench\runs-20261108-194619` and `runs-20261108-202037` |

All six instances were validated first: the base commit fails the hidden
`FAIL_TO_PASS` tests. Test patches were never visible to the agents. Agent
changes to test files were excluded from the source patch.

## Results

### FAIL_TO_PASS resolution

| Instance | Repository | Kiro | Machina | Hermes |
| --- | --- | --- | --- | --- |
| `pallets__flask-4045` | Flask blueprint dots | FAIL | FAIL | FAIL |
| `pallets__flask-4992` | Flask config TOML file mode | **PASS** | **PASS** | **PASS** |
| `pallets__flask-5063` | Flask routes subdomain/host column | FAIL | FAIL | FAIL |
| `psf__requests-3362` | Requests streaming decode | **PASS** | FAIL | FAIL |
| `pytest-dev__pytest-6116` | pytest `--collect-only` shortcut | **PASS** | FAIL | **PASS** |
| `pytest-dev__pytest-5221` | pytest fixture scope display | FAIL | FAIL | **PASS** |
| **Total** |  | **3/6 (50%)** | **1/6 (17%)** | **3/6 (50%)** |

Strict full success (both `FAIL_TO_PASS` and `PASS_TO_PASS`) was **2/6 Kiro**,
**1/6 Machina**, and **1/6 Hermes**. The two pytest pass-to-pass sets are
contaminated by Windows-specific py.path/symlink and old-pytest collection
failures, so the `FAIL_TO_PASS` table is the primary comparison; Flask and
Requests successful tasks also passed their pass-to-pass sets.

### Time and token cost

| Harness | Total wall time, 6 tasks | Average | Recorded tokens |
| --- | ---: | ---: | ---: |
| Kiro | **263.65 s** | **43.9 s** | not exposed by chosen output mode |
| Hermes | **1,010.42 s** | **168.4 s** | not exposed by chosen output mode |
| Machina | **1,333.33 s** | **222.2 s** | **675,070** |

Machina was about **5.1x slower than Kiro** and **31.5% slower than Hermes**
on this subset. One Machina run (`pytest-dev__pytest-5221`) timed out at the
300-second per-task limit.

## Failure causes

- **Flask 4045:** all three harnesses added the blueprint-name dot check but
  missed the hidden requirement that `add_url_rule` reject dotted endpoint and
  view-function names with `ValueError`. The hidden test failed at the still
  present `assert "." not in endpoint`.
- **Flask 5063:** all three implemented a `Domain` column or an opt-in
  `--domain` flag. The hidden tests require existing route output to expose
  `Subdomain` or `Host` by default. The agents did not infer the expected
  column name/semantics.
- **Requests 3362:** Kiro changed `stream_decode_response_unicode` and passed.
  Machina and Hermes changed `get_encoding_from_headers` instead; the hidden
  test still failed in the streaming decoder.
- **pytest 6116:** Kiro and Hermes added the `--co` alias. Machina made no
  source changes and failed.
- **pytest 5221:** Hermes added fixture-scope output and passed. Kiro's change
  was incomplete. Machina changed `_pytest/python.py` but timed out at 300 s
  and failed.

## Interpretation

On this real-issue subset, the evidence does **not** support a claim that
Machina is better than modern harnesses. Machina solved the fewest hidden
issues (1/6 instead of 3/6), used substantially more wall time, and consumed
675k recorded tokens on six small tasks. Kiro and Hermes were both more
effective and far cheaper in wall time on this sample.

This is a six-task subset and not a general quality verdict. It does, however,
directly contradict a broad "better than every modern harness" claim and
identifies concrete harness-level gaps:

1. Machina often starts from a broad interpretation and does not inspect the
   repository's existing tests or expected API semantics before editing.
2. It spends many iterations and tokens without a reliable stop condition for
   small, well-specified issues.
3. It sometimes changes a plausible adjacent helper instead of the failing
   code path.
4. Its 300-second timeout was reached on a task Kiro solved in 47 seconds and
   Hermes solved in 252 seconds.

## Reproduction

The benchmark runner and raw per-task JSON are outside the repository:

`C:\Users\kiash\AppData\Local\Temp\opencode\swe-bench`

```powershell
python run_swe_bench.py --baseline --harnesses ""
python run_swe_bench.py --tasks pallets__flask-4045,pallets__flask-4992,pallets__flask-5063,psf__requests-3362,pytest-dev__pytest-6116,pytest-dev__pytest-5221
```

Docker and Harbor were installed in WSL, but a fair Machina-in-Terminal-Bench
adapter would need a Python runtime and a PostgreSQL sidecar in each task
container. The SWE-bench subset runs the agents natively against the same repo
and the official hidden test patches, which is the more direct comparison for
this codebase.

## Limits

- Six tasks only; confidence intervals are wide and no leaderboard position is
  claimed.
- Local evaluator, not the official SWE-bench Docker harness.
- Kiro and Hermes token usage was not exposed in the selected output modes;
  only Machina's audit-log total is directly comparable.
- The pass-to-pass sets for the two pytest tasks have Windows environment
  failures; the strict totals above are reported separately for that reason.
