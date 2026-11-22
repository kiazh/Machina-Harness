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
| `machina acp` | Agent Client Protocol over stdio for editor clients such as Zed |
| `machina service start/status/stop/restart` | Detached HTTP service with health-verified status and foreground-serve detection |
| `machina completions bash|zsh|fish` | Completion script generated from the installed lazy command table |
| `machina plugin list/loaded` | Installed entry points and process-loaded plugins |
| `machina update` / `upgrade` | Explicit source-checkout fast-forward; `--rollback` resets a clean checkout to the commit recorded before the last update |
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

Limitation: clean-Git terminal coverage is additive and narrow. When the turn
has no prior file-tool write and the worktree is clean, the first terminal
command records `HEAD`; changed, deleted, and new files are then added to the
same manifest and restored by `undo`. Files above the 8 MiB backup cap are
disclosed as ignored and left changed rather than risking a destructive undo.
Paths with `core.autocrlf=true/input`, eol/text conversion, filter, or
working-tree-encoding attributes are also disclosed as ignored: restoring a raw
blob would either produce the wrong bytes or require executing a repository
filter. Git-ignored files are outside coverage. Dirty worktrees, non-Git workspaces,
writes after the turn ends (for example a still-running background command),
and writes outside the workspace remain outside the guarantee. The file tool is
the normal covered path; do not describe snapshots as a filesystem sandbox.

### Plugins

Entry-point plugins use the existing `agent_harness.plugins` group and the
same lifecycle hooks as the gateway. They are opt-in through `MACHINA_PLUGINS`;
no repository file can load code implicitly. CLI and headless agent turns now
initialize the plugin registry through the shared agent factory.

### Background service

`machina service start` launches `machina serve` as a detached child process,
writes a log under the Machina home directory, and waits for the API health
endpoint. Status and stop use the detached-start metadata PID and require a
healthy `/health` response; a native-PostgreSQL or `MACHINA_DB_MANAGE=0` install
still works through that metadata. Foreground `serve` leases are detected but
are never killed by `service stop`. `stop --force` is required for an unhealthy
recorded PID. Restart reuses the recorded host and port when no alternatives are
supplied.

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

### Update rollback

A successful source-checkout update records the previous and updated commits.
`machina update --rollback` resets only when the checkout is clean, the current
HEAD is still the recorded updated commit, and the previous object still
exists. It does not reinstall dependencies, does not run on a timer, and never
overwrites local changes.

### Shell completions

`machina completions bash|zsh|fish` prints a Click completion script derived
from the installed lazy command table. Pipe it to the shell's completion
directory or load it from the shell profile.

### ACP editor bridge

`machina acp` speaks ACP over stdin/stdout. It creates one durable Machina
session per editor session, advertises the `ask`, `workspace`, `sandbox`, and
`full` modes, streams text and tool-call updates, and translates approval cards
into `session/request_permission` requests. ACP `allow_once` decisions use the
normal one-time approval store; `allow_always` saves a session grant through the
same permission store as the HTTP API.

Example Zed configuration:

```json title="~/.config/zed/settings.json"
{
  "agent_servers": {
    "Machina": {
      "command": "machina",
      "args": ["acp"]
    }
  }
}
```

Known limitations: ACP `session/load` restores the session identity and mode but
does not replay transcript history into the editor yet; per-session MCP servers
and additional workspace roots are reported as unsupported notices; image and
audio content blocks are not accepted; and no live Zed smoke test was run in
this checkout. Use `--mode full` or `--auto` when a client cannot answer approval
prompts; workspace and sandbox still request approval for sensitive actions.

## Gaps still open

| Gap | Why it matters | Acceptance gate |
|---|---|---|
| Plugin install/update and TUI plugins | Entry-point loading exists, but OpenCode's install/update and GUI plugin surfaces do not | Install/update commands, TUI/GUI hooks, and isolation tests |
| Full TUI keybind/theme parity | OpenCode exposes a deeply configurable terminal UI | Exportable theme/keybind config and visual regression coverage |
| Signed/versioned upgrade path | Source fast-forward and one-step rollback exist; OpenCode manages installed versions | Signed release artifacts and cross-version rollback |
| Snapshot coverage for dirty/non-Git terminal edits | Clean-Git terminal edits are covered, but dirty worktrees and non-Git workspaces are explicitly skipped | Bounded worktree/index strategy for dirty trees with explicit untracked-file semantics |
| Reference repositories | OpenCode can attach named local/Git references | Bounded read-only references with path/Git revision validation |

## Test evidence

- `tests/test_snapshots.py`: restore, new-file deletion, conflict refusal,
  sensitive-file exclusion, outside-workspace exclusion.
- `tests/test_terminal_snapshots.py`: clean-Git terminal restore, dirty-tree
  refusal, and file-tool-after-terminal baseline ordering.
- `tests/test_formatting.py`: disabled, successful, and failed formatter paths.
- `tests/test_cli_parity.py`: new command/help surface.
- `tests/test_cli_command_set.py`: registration order.
- `tests/test_acp_bridge.py`: ACP prompt extraction, mode advertisement,
  initialization capabilities, and approval mapping.
- `tests/test_service_cli.py`: lease filtering, metadata fallback, and health
  URL normalization.
- `tests/test_cli_completions.py`: bash/fish generation and unsupported-shell
  refusal.
- `tests/test_update.py`: fast-forward, dirty/local-commit refusal, launch
  cache, and clean-only one-step rollback.
- Full local suite: **686 passed, 9 skipped**.

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
