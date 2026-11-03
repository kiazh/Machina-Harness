# Desktop CLI port, first connected slice

## Implemented

The approved Atelier composition now runs in `apps/desktop`. Sessions, prompting,
steering, approvals, live subagents, file editing/save/recovery and LSP completion
keep their existing engine integration. The welcome uses the full Deus Ex Machina
name. Porcelain and Ink are new appearance presets; explicit legacy selections
remain available.

New real gateway surfaces cover installed skills, skill instruction viewing/import,
session-aware model catalogs with fallback provenance, providers and masked key
management, ChatGPT sign-in/import/logout, and read views for memory, context,
scheduled jobs, agents, connectors, status and usage. Conversation tools connect
rename/fork/delete/goal/export/compression. The terminal dock displays real agent
command output and lifecycle controls; it does not claim interactive PTY support.

Slash commands open local features. Unsupported commands fail before session
creation or prompt submission. This is intentionally not full CLI argument parity.

## Checks

- `npm run desktop:check`: TypeScript and 40 unit/integration tests pass.
- `npx playwright test --config apps/desktop/playwright.config.ts`: eight workflows
  pass in isolated Edge on Windows. Includes draft/buffer retention across feature
  pages, real gateway contract fixtures, model session targeting, steering, jobs,
  approvals, persisted wallpaper/motion and IndexedDB recovery.
- The 1,500-message synthetic typing/streaming case retains 10 rendered message
  rows and reports approximately 7.2–7.3 ms frame interval at p95 on this machine.
  This is synthetic browser evidence, not a claim about every real session.
- `.venv/Scripts/python.exe -m pytest tests/test_desktop_runtime.py tests/test_desktop_bootstrap.py -q`:
  six tests pass. Local pytest cache directory is not writable; tests themselves pass.
- `npm run desktop:native`: Windows release engineering binary builds successfully.

## Review and regressions

Independent review identified two important issues. Both were reproduced with
failing tests, corrected, and verified: delayed default-model updates must not
replace the displayed route of a resumed session; resuming a session must reload
surviving shell jobs so their output/stop controls remain available.

Transcript resize following now schedules writes on animation frames rather than
inside ResizeObserver delivery, preventing warnings during hidden-page switches.

## Limits

See `docs/plans/desktop-cli-parity.md`. Advanced feature mutations, complete CLI
subcommand handling, PTY terminals, external skill-shop discovery, full editor/LSP
navigation, native macOS acceptance, live-provider task acceptance, installers and
updaters remain migration work. No real account, secret, skill or session mutation
was performed by these fixture tests. No push or release was made.
