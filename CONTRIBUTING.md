# Contributing

Thanks for improving Machina. Keep changes small, testable, and explicit about
their failure behavior.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
npm ci --ignore-scripts --prefix ui
```

The local `tests/` suite is a fast repository smoke suite. The full backend,
PostgreSQL, integration, and architecture suite lives in
[Machina-DevTools](https://github.com/kiazh/Machina-DevTools); CI runs it
against this checkout.

## Required checks

Before opening a pull request:

```bash
python -m pytest tests -q -p no:cacheprovider
python -m ruff check machina tests tools integrations
python -m compileall -q machina tests tools integrations
git diff --check
```

For terminal UI changes, also run:

```bash
npm ci --ignore-scripts --prefix ui
npm run check --prefix ui
```

For desktop changes, run from `apps/desktop` (so the pinned toolchain file is
picked up):

```bash
cargo test --locked
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
```

For changes touching the agent loop, tools, context, permissions, providers,
or database behavior, run the relevant DevTools tests as well. State which
tests were run and paste the exact pass/fail counts in the PR.

## Change rules

- Keep behavior deterministic where the model is not the thing being tested.
- Do not edit or read hidden grading tests in benchmark tasks.
- Do not add benchmark-specific prompts, task IDs, or hardcoded patches to the
  product.
- Preserve recovery paths: large outputs should remain recoverable, and a stop
  should preserve the working tree.
- Add or update tests for every behavioral change.
- Do not commit secrets, local databases, generated caches, or machine-local
  configuration.

## Benchmark changes

Use the frozen task manifest and record model, reasoning effort, harness,
trials, tokens, cost, latency, and verifier integrity. Do not tune on the
locked test split. See `docs/BENCHMARKING.md` and
`docs/verification/SWE_HOLDOUT_2026-11-12.md`.

## Reporting security issues

Do not open a public issue for a suspected vulnerability. Follow
[SECURITY.md](SECURITY.md).
