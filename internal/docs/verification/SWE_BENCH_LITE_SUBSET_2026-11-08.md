# SWE-bench Lite subset - harness comparison - 2026-11-08 (updated 2026-11-10)

> **Development-set warning:** these six tasks were used while developing the
> repair protocol, so the 5/6 result is not evidence that Machina generalizes.
> On a pre-selected held-out set, Machina scored **3/6 on Haiku** versus
> **4/6 Kiro and 4/6 Hermes**, and **5/10 on DeepSeek** versus **9/10 OpenCode
> and 6/10 Hermes**. See
> `docs/verification/HELDOUT_SWE_2026-11-10.md` for the held-out methodology,
> validation gates, and correction. Do not use this page as the primary
> comparison.

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
| Kiro/Hermes artifacts | `C:\Users\kiash\AppData\Local\Temp\opencode\swe-bench\runs-20261108-194619` and `runs-20261108-202037` |
| Final Machina artifact | `C:\Users\kiash\AppData\Local\Temp\opencode\swe-bench\runs-20261110-111335` |

All six instances were validated first: the base commit fails the hidden
`FAIL_TO_PASS` tests. Test patches were never visible to the agents. Agent
changes to test files were excluded from the source patch.

## Results

### FAIL_TO_PASS resolution

| Instance | Repository | Kiro | Machina | Hermes |
| --- | --- | --- | --- | --- |
| `pallets__flask-4045` | Flask blueprint dots | FAIL | FAIL | FAIL |
| `pallets__flask-4992` | Flask config TOML file mode | **PASS** | **PASS** | **PASS** |
| `pallets__flask-5063` | Flask routes subdomain/host column | FAIL | **PASS** | FAIL |
| `psf__requests-3362` | Requests streaming decode | **PASS** | **PASS** | FAIL |
| `pytest-dev__pytest-6116` | pytest `--collect-only` shortcut | **PASS** | **PASS** | **PASS** |
| `pytest-dev__pytest-5221` | pytest fixture scope display | FAIL | **PASS** | **PASS** |
| **Total** |  | **3/6 (50%)** | **5/6 (83%)** | **3/6 (50%)** |

Strict full success (both `FAIL_TO_PASS` and `PASS_TO_PASS`) in the final
Machina run was **2/6** (Flask 4992 and Requests 3362); the other three
Machina `FAIL_TO_PASS` passes had Windows-specific pytest collection /
pass-to-pass failures. Kiro was 2/6 strict and Hermes 1/6 strict. The
`FAIL_TO_PASS` table is the primary comparison because the two pytest
pass-to-pass sets are contaminated by Windows-specific py.path, symlink, and
old-pytest collection differences.

The result was variable across batches. An earlier full batch with the same
harness family scored **4/6**; the final batch scored **5/6**. Treat this as a
**4-5 of 6** result, not a deterministic leaderboard score.

### Time and token cost

| Harness | Total wall time, 6 tasks | Average | Recorded tokens |
| --- | ---: | ---: | ---: |
| Kiro | **263.65 s** | **43.9 s** | not exposed by chosen output mode |
| Hermes | **1,010.42 s** | **168.4 s** | not exposed by chosen output mode |
| Machina (final) | **1,269.32 s** | **211.6 s** | not aggregated in final artifact |

Machina's final per-task times were 192.17 s, 190.59 s, 180.48 s, 152.04 s,
235.62 s, and 318.42 s. It was about **4.8x slower than Kiro** and **25.6%
slower than Hermes**. The final run used a 900-second per-task cap; no task
timed out. The earlier report recorded 675,070 Machina tokens for the initial
1/6 run; the final artifact does not contain an aggregate token field, so no
new token total is claimed.

## Failure causes

- **Flask 4045:** all three harnesses added the blueprint-name dot check but
  missed the hidden requirement that `add_url_rule` reject dotted endpoint and
  view-function names with `ValueError`. The hidden test failed at the still
  present `assert "." not in endpoint`. The issue text says that endpoint check
  already exists, so this is the most ambiguous task in the subset.
- **Flask 5063:** the fixed Machina run exposes `Subdomain`/`Host` using the
  route's public attributes rather than inventing a `Domain` column. The hidden
  tests pass; only the contaminated Windows pass-to-pass set fails.
- **Requests 3362:** the fixed Machina run applies the UTF-8 fallback at the
  decoding point of use, so `iter_content(decode_unicode=True)` returns text
  even when `encoding` is `None`.
- **pytest 6116:** Machina adds the `--co` alias; the hidden `FAIL_TO_PASS`
  tests pass.
- **pytest 5221:** Machina shows non-default fixture scopes in both default and
  verbose output; the hidden `FAIL_TO_PASS` tests pass.

## Harness changes behind the result

The improvement from the initial **1/6** to the final **5/6** is not a model
change. It comes from repository-repair harness behavior:

1. `search_files` is recursive. It previously used `Path.glob`, so searches
   for symbols in `src/` silently missed the source tree.
2. The repair protocol is delivered as a protected instruction tail after
   system-prompt truncation. Previously it was appended at the end of a long
   system prompt and could be cut off before reaching the model.
3. Child processes preserve `PYTHONPATH` and
   `PYTEST_DISABLE_PLUGIN_AUTOLOAD`, and Windows launcher resolution uses the
   child PATH, so task tests run in the checkout's environment rather than the
   host Python.
4. The protocol now requires sibling validation auditing, exact exception-type
   normalization, default-vs-verbose output consistency, existing public
   attribute names, and consumer-site fixes for missing/`None` values.

## Interpretation

On this real-issue subset, the fixed Machina resolves **5/6** hidden issues,
versus **3/6** for both Kiro and Hermes. That is a meaningful improvement over
Machina's initial 1/6 and is the first measured result in this project where
Machina leads these two harnesses on issue resolution.

It is not evidence of universal superiority. The result is six tasks, one task
remains unsolved by every harness, the score varies between batches, and
Machina remains substantially slower than Kiro. The evidence supports a
narrower claim: Machina's repository-repair loop is now competitive with and,
on this subset, more effective than Kiro/Hermes, while its wall-time cost is
still a weakness.

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
  Machina's final aggregate token count is not in the artifact.
- The pass-to-pass sets for the pytest tasks have Windows environment failures;
  strict totals are reported separately for that reason.
- Machina's 4/6 -> 5/6 batch variation means the honest summary is a range, not
  a single guaranteed score.
