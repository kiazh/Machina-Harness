# Rune desktop shell — 2026-11-12

## Scope

Replaced the React/Tauri desktop client at `apps/desktop` with a native Rust
application built on Rune. The shell uses Rune's own renderer and text stack,
the `rune-kit` desktop shell, and the OpenCode design language from
`rune-theme-opencode`.

The first milestone is the visual shell and chat/workspace surfaces:

- session sidebar with workspace metadata and selectable sessions;
- chat timeline with Markdown assistant replies and tool activity;
- multiline composer with draft clearing and keyboard/button submit;
- resizable workspace inspector with changed files and run status;
- dark and light appearances;
- headless scene rendering for both appearances.

The gateway is intentionally not wired into this shell yet. Session loading,
prompts, approvals, jobs, memory, and settings are the next port.

## Pinned dependencies

- Rune `dev` revision `7dfaa8fb1fa8281e6e2ccc92568d95262e9c6f3c`
- Rust `1.99.1`
- `apps/desktop/Cargo.lock` committed

Rune is not published as a TypeScript package. This is a Rust application, not
an npm framework dependency.

## Verification

| Check | Result |
|---|---|
| `cargo test --locked` in `apps/desktop` | 2 passed |
| `cargo fmt --check` | clean |
| `cargo clippy --locked --all-targets -- -D warnings` | clean |
| Dark headless render | `apps/desktop/docs/rune-shell-dark.png` |
| Light headless render | `apps/desktop/docs/rune-shell-light.png` |
| Root Python smoke suite | 347 passed, 9 skipped (pre-migration run) |
| Contracts | 4 passed |

The shell tests build both appearances headlessly and drive the send button
through Rune's virtual pointer events.

## Remaining work

- Connect JSON-RPC gateway sessions and prompt streaming.
- Port approvals, jobs, memory, settings, MCP, skills, and editor surfaces.
- Add native packaging, signing, and updater policy.
- Re-run the root Python/UI/backend suites after the desktop removal is merged.
