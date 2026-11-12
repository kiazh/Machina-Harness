# Benchmarking contract

Machina's benchmark evidence is stored outside the product runtime and treated
as immutable input. The canonical pieces live in `machina/bench`.

## Task identity

`TaskSpec` records the suite, repository, base commit, prompt hash, and hidden
test-patch hash. `TaskLockbox` freezes those identities in a JSON manifest and
rejects missing, extra, or changed tasks before a run starts.

Never put prompt bodies, hidden tests, gold patches, or credentials in a
lockbox. The manifest contains hashes and identity metadata only.

## Run evidence

`Ledger` appends one canonical JSON line per trial. A `RunRecord` carries:

- task, suite, harness, model, commit, reasoning effort, trial, seed;
- pass/fail and failure class;
- wall time, input/output tokens, cache-read/cache-write tokens, request and
  tool-result tokens, turns, peak context tokens, known cost, tool calls;
- verifier-integrity status and artifact paths.

The ledger reports per-suite/harness/model `pass_at_1`, `pass_pow_k`, known cost,
wall time, total tokens, **tokens per resolved task**, cache-read ratio, tokens
per turn, and p95 context tokens. Do not report a single trial as a benchmark
result, and do not report accuracy without the corresponding token/cost
numbers.

## Evaluation policy

- Compare harnesses at the same model, reasoning effort, tool policy, and
  network policy.
- Run at least three trials per task for reliability claims.
- Keep a frozen development split and a locked test split.
- Do not tune prompts, controller logic, or skills on the locked split.
- Record verifier-integrity failures instead of dropping them.
- Report accuracy and cost as a Pareto frontier. A pass-rate gain that costs
  more than 1.5x the best same-model harness's tokens per resolved task is not
  a competitive gain.
- Raw trajectories and task manifests remain available with each headline
  result.

## Current suite choices

Use Harbor-Index and Terminal-Bench 4.1 for broad agentic/terminal coverage;
SWE-bench Pro for long-horizon software engineering; OSWorld 2.0 for computer
use; tau3 for tool/user interaction. SWE-bench Verified, GAIA, WebArena, and
OSWorld-Verified are regression checks, not primary ranking claims.
