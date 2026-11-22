# Internal engineering material

This tree holds repository history, verification evidence, audits, benchmark
tooling, and evaluation gates that are useful to maintainers but are **not part
of the installed product**.

- `docs/verification/` — proofs, release-gate evidence, review reports, and
  benchmark records
- `docs/audits/` — full-repository audits
- `docs/archive/` — superseded plans and historical handoffs
- `docs/*.md` — internal audits, parity notes, and research plans
- `evals/` — optional Promptfoo, Ragas, and DeepEval gates
- `research/` — identity-evaluation fixtures used by research tests
- `FEATURE_LEDGER.md` — implementation tracking ledger

None of this directory is included in the wheel or source distribution. User
documentation lives in [`docs/`](../docs/) and the repository
[`README.md`](../README.md).
