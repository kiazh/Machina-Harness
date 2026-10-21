# Machina 1.0.0

Open-source, self-hosted release.

## Install

See the main [README](../../README.md#get-started). Machina requires Python 3.11+,
Node.js 22.19+ for the terminal UI, and PostgreSQL with pgvector. Bring your own
provider account and API key.

## What this release is

A local-first agent harness with a terminal UI, CLI, HTTP API, MCP, persistent
context and memory, permissions, jobs, skills, and provider integrations. Core
workflows are implemented and tested; provider and integration behavior depends
on the account and endpoint you configure.

## Validation

- Backend: 6,502 passed, 2 skipped.
- UI: 1,165 passed, 0 skipped; typecheck and ESLint clean.
- GitHub Actions: backend, UI, and Docker jobs pass.
- Docker image build and containerized CLI smoke test pass.
- Wheel/sdist isolated imports and bundled resource checks pass.
- Ruff, formatting, Bandit (0 medium/high), `uv lock --check`, token-counter,
  and E2E-gate sanity checks pass.

## Limits

This is not a hosted service and does not include a production SLA, managed
backup/restore, or data-recovery guarantee. Provider APIs and subscriptions can
change or charge for usage. Some integrations remain experimental and may use
provider-specific behavior. Back up your PostgreSQL database and configuration
before upgrading.

Known hardening work that is not a core-workflow blocker: installer least
privilege and helper trust, LSP epoch-edge fencing, complete E2E revocation
assertions, ECC capsule/receipt protections, release-artifact path hygiene, and
historical proof-script false-green cleanup. These are tracked for follow-up and
do not change the validation above.
