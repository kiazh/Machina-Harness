# OpenCode daily-driver parity

This is the working gap list against OpenCode V2. It records only implemented
behavior and explicit remaining gaps; it is not a claim of full parity.

## Implemented in this tranche

### Headless and CLI commands

| Command | Behavior |
|---|---|
| `machina run "<prompt>"` | One-shot headless turn, no interactive UI |
| `machina models [provider]` | Top-level model catalog |
| `machina session list/search/show/export/fork/delete/usage/compress` | Session management under one namespace |
| `machina snapshots list/diff/undo` | Inspect and restore per-turn workspace snapshots |
| `machina undo` | Restore the latest snapshot |
| `machina session diff/undo` | Session-scoped snapshot operations |
| `machina mcp list/show/add/remove` | Human MCP server administration |
| `machina debug paths/info` | Path and runtime diagnostics without starting the agent |
| `machina api METHOD PATH` | Authenticated local HTTP API request with `--data`, `--header`, and `--url` |
| `machina plugin list/loaded` | Installed entry points and process-loaded plugins |
| `machina update` / `upgrade` | Explicit source-checkout fast-forward with local-change refusal |
| `machina --help` / lazy command table | New commands are registered in the installed CLI, not only the eager test app |

Existing commands (`chat`, `doctor`, `init`, `serve`, `auth`, `providers`,
`skills`, `harnesses`, `host`, `config`, memory commands) remain available.

### Workspace snapshots

Machina now records the previous bytes of files changed by the `write_file`
tool during a turn.

- The snapshot store is outside the workspace under `machina_home/snapshots`.
- Sensitive names such as `.env`, key files, and credentials are never copied.
- Paths outside the workspace and ignored directories are skipped.
- At turn end, post-write hashes are recorded.
- `undo` refuses to overwrite files changed after the turn unless `--force` is used.
- New files created by the agent are deleted on undo.
- `diff` shows a unified before/after patch.
- Empty turns leave no snapshot behind.

Limitation: writes made by an opaque terminal child process are outside this
guarantee. The file tool is the covered path; do not describe snapshots as a
filesystem sandbox.

### Plugins

Entry-point plugins use the existing `agent_harness.plugins` group and the
same lifecycle hooks as the gateway. They are opt-in through `MACHINA_PLUGINS`;
no repository file can load code implicitly. CLI and headless agent turns now
initialize the plugin registry through the shared agent factory.

### Project config

A repository may add `machina.json` or `.machina.json`. The nearest file is
applied at launch, before the UI or CLI resolves a model. Accepted keys are
limited to non-authority behavior: model, provider, reasoning effort,
formatter, snapshots, compaction, output policy, meta-harness hint, skill
query expansion, retrieval budget, memory/RAG, and browser verification.

Project files cannot set execution mode, permissions, credentials, or unknown
keys; those are rejected. `MACHINA_PROJECT_CONFIG=1` disables the layer.

### Formatters

Set `formatter: auto` with `machina config-set formatter auto` to run a detected
formatter after `write_file`:

- Python: `ruff format` or `black`
- JS/TS/JSON/CSS/Markdown/YAML: `prettier`
- Go: `gofmt`
- Rust: `rustfmt`
- C/C++: `clang-format`
- Shell: `shfmt`

Formatting is opt-in (`off` by default), never rolls back the write, and reports
missing or failed formatters instead of failing the turn.

## Gaps still open

| Gap | Why it matters | Acceptance gate |
|---|---|---|
| ACP server | Editor/IDE clients need Agent Client Protocol | `machina acp` handshake plus a real editor smoke test |
| Background service lifecycle | OpenCode has `service start/status/restart` | Start/stop/restart are idempotent and leave no stale PID |
| Plugin install/update and TUI plugins | Entry-point loading exists, but OpenCode's install/update and GUI plugin surfaces do not | Install/update commands, TUI/GUI hooks, and isolation tests |
| Full TUI keybind/theme parity | OpenCode exposes a deeply configurable terminal UI | Exportable theme/keybind config and visual regression coverage |
| Shell completions | OpenCode provides bash/zsh/fish/sh | Generated completions match the lazy command table |
| Upgrade/rollback command | OpenCode manages upgrades without source edits | Signed/versioned install path; rollback is tested |
| Snapshot coverage for terminal edits | Current snapshots cover file-tool writes only | Git/worktree snapshot strategy with explicit untracked-file semantics |
| Reference repositories | OpenCode can attach named local/Git references | Bounded read-only references with path/Git revision validation |

## Test evidence

- `tests/test_snapshots.py`: restore, new-file deletion, conflict refusal,
  sensitive-file exclusion, outside-workspace exclusion.
- `tests/test_formatting.py`: disabled, successful, and failed formatter paths.
- `tests/test_cli_parity.py`: new command/help surface.
- `tests/test_cli_command_set.py`: registration order.
- Full local suite: **655 passed, 9 skipped**.

### Plugins

Entry-point plugins use the existing `agent_harness.plugins` group and the
same lifecycle hooks as the gateway. They are opt-in through `MACHINA_PLUGINS`;
no repository file can load code implicitly. CLI and headless agent turns now
initialize the plugin registry through the shared agent factory.

### Project config example

```json
{
  "model": "deepseek/deepseek-v4-flash",
  "provider": "deepseek",
  "formatter": "auto",
  "snapshots_enabled": true
}
```

The current loader validates the allow-list and value types directly.

No benchmark result is claimed by this document.
