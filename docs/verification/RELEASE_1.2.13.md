# Machina 1.2.13

Re-authentication and clearer failure classes.

## `/auth`

Provider menus now always include **Sign in**, even when a token is already
present. For a signed-in provider the menu offers:

- Sign in again (replace the stored token)
- Sign out
- Status
- Import (ChatGPT Codex / Copilot gh)

This means an imported Codex credential can be refreshed or replaced without
signing out first.

## Turn failures

Typed provider and validation errors continue to show their redacted message.
Untyped failures now add only the exception class name, for example
`agent turn failed (RuntimeError)`, so the generic message still carries a
diagnostic clue without exposing arbitrary exception text.

## Verification

- UI typecheck, lint, and 1,162 tests pass.
- Gateway failure and architecture tests pass.
- Backend and Docker CI jobs pass.
