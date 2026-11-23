# Machina 2.0.0

The daily-driver release.

Machina 2.0.0 rolls up the post-1.2.14 work into a single major release. The
largest change is the OpenCode-parity tranche: a source checkout can now update
itself safely, snapshot and restore edits, run opt-in formatters, expose ACP to
editors, and manage MCP servers with a provider-first flow. The runtime,
provider, memory, and UI surfaces also received substantial changes.

## Highlights

### OpenCode-parity workflow

- Per-turn file snapshots and clean-Git terminal snapshots with unified diffs,
  conflict-aware restore, and `machina undo`.
- Opt-in post-write formatters that never roll back a write.
- `machina.json` / `.machina.json` project defaults for model, provider,
  formatter, snapshots, compaction, and retrieval. Project config is an
  allowlist, not an authority or secret store.
- Authenticated `machina api` client, shell completions, explicit update
  commands, and update rollback.
- ACP editor bridge (`machina acp`) with streaming updates, modes, and approval
  cards.
- Background service lifecycle with health-verified metadata, a process start
  marker, and foreground-serve protection.
- Opt-in plugin entry points through `MACHINA_PLUGINS`.

### Runtime, memory, and growth

- Compact capability prompts and on-demand tool schemas keep ordinary turns
  small; the full catalog remains available through `tool_load` and
  `harness_catalog`.
- Persistent temporal/entity memory graph with bounded expansion.
- Agent-owned growth notes, human feedback, operational evidence, reversible
  revisions, persona trials, skill ranking, tool ordering, and retrieval-policy
  adaptation. The model's weights are not trained.
- Deterministic connected-model routing, Multi Auto defaults, validated parallel
  delegation, and native/proxy/MCP worker transports.
- Steering, `ask_user`, repair-controller caps, and verification-gated final
  answers.

### Providers, MCP, and integrations

- Provider-first `/mcp` with **Authorize** and **Remove server** as the complete
  human cleanup surface.
- Hosted MCP OAuth, protected HTTP transport, exact approval binding, cached
  catalogs, and connector-aware tool labels.
- OIDC resource-server authentication, OpenAPI 3.2.1 docs, A2A 1.0, and
  CloudEvents SSE.
- Optional OTLP/HTTP trace export and a CycloneDX 1.7 release SBOM.
- ChatGPT, Grok, GitHub Copilot, and Kiro account management, plus API-key
  providers and local models.

### UI and operations

- Full-page Status tab, retained-output inspection, workspace/editor/LSP
  surfaces, and responsive keyboard fallbacks.
- 73 terminal themes with dark-first additions and a deduplicated registry.
- Command reports render as parsed Markdown: headings, lists, tables, inline
  code, and wrapped lesson text instead of literal source dumps.
- Windows, macOS, and Linux installers; Windows host setup; Unix host service;
  shell completions.
- Safer release artifacts: internal evidence and evaluation material are
  excluded from wheel, sdist, and Docker contexts.

## Breaking and compatibility changes

- Removed the built-in `opencode_session` provider and `opencode-proxy`
  command. Tool-using work stays on native, proxy, or MCP routes.
- Multi Auto is the default routing mode. Existing explicit model/provider pins
  remain authoritative for the session.
- Removed the `onedark` alias (`one-dark` remains) and the
  `catppuccin-macchiato` variant; duplicate themes were consolidated.
- Moved internal evidence, audits, parity notes, and evaluation tooling under
  `internal/`. They are not shipped in release artifacts.
- Source-checkout auto-update now unshallows an installer clone before comparing
  histories, so the default `install.sh` checkout can fast-forward on launch.

## Upgrading

For a source checkout:

```sh
machina update
```

Interactive launches also check `origin/main` at most once every 15 minutes and
fast-forward a clean checkout. The updater:

- refuses dirty worktrees;
- refuses to overwrite local commits;
- handles shallow installer clones;
- refreshes dependencies when `pyproject.toml` or `uv.lock` changed;
- re-executes into the updated code after a successful update;
- records the previous commit for `machina update --rollback`.

Set `MACHINA_AUTO_UPDATE=0` to disable launch checks. Wheel/package installs do
not update themselves; reinstall the new wheel or rerun the installer.

Back up your PostgreSQL database and configuration before upgrading. Database
migrations are additive, but provider credentials and local configuration remain
your responsibility.

## Validation

- Backend: 687 passed, 9 skipped.
- UI: 1,266 tests, 1,256 passed, 10 environment-gated skips; typecheck and lint
  pass.
- Contracts: 3 passed.
- Release artifacts: wheel and sdist build; isolated-import, bundled-resource,
  and source-distribution checks pass.
- Auto-update: clean, shallow, dirty, current, local-commit, cached, rollback,
  and re-exec paths covered by backend tests.
- Theme registry: 73 palettes, contrast checks, distinct surfaces, and
  selected-row checks pass.

## Limits

This is a self-hosted open-source release with no hosted service, production
SLA, managed backup/restore, or data-recovery guarantee. Provider APIs,
subscriptions, models, and external tool behavior can change. Some integrations
remain provider-specific or experimental. The external Machina-DevTools suite is
maintained separately; its pinned snapshot can lag this repository's internal
API refactors.
