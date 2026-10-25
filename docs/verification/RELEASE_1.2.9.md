# Machina 1.2.9

Launch updates, unified subscription auth, and model-catalog fixes.

## Auto-update

When the interactive UI launches from a git checkout, Machina checks
`origin/main`. If the remote version is newer and the worktree is clean, it
fast-forwards, refreshes packaging metadata when needed, and re-executes into
the new code. Dirty checkouts are never overwritten. Disable with
`MACHINA_AUTO_UPDATE=0`.

## Unified `/auth`

`/auth` now lists and manages all three subscription providers:

- ChatGPT — browser PKCE, Codex import, status, sign-out
- Grok — xAI device flow, status, sign-out
- GitHub Copilot — GitHub device flow, `gh` import, status, sign-out

Tokens never cross the gateway wire. A successful sign-in activates that
provider and a model from its catalog for the current session and future
launches, so the next agent turn no longer stays pinned to the previous
provider.

## Models

OpenAI's GPT-6.1 release is Sol; Luna remains `gpt-6-luna`. The picker keeps
that real id and does not offer a non-existent `gpt-6.1-luna`.

## Verification

- Backend auth/update/catalog tests pass.
- UI typecheck and 1,161 UI tests pass.
- Backend, UI, and Docker CI jobs pass.
- Wheel and source distribution checks pass.
