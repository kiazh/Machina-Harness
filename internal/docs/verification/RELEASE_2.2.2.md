# Machina 2.2.2

Session-workspace validation fix.

## Fixed

- **Plain chat sessions can run harnesses.** `interop._workspace_cwd` required
  an explicit `desktop_workspace_root` pin and raised
  `Session workspace is invalid; select its folder again` for a normal chat
  with no workspace selected. It now uses the configured gateway workspace
  when no pin exists; an explicit absolute pin still wins.
- **Legacy relative pins are normalized.** A relative pinned root resolves
  against the configured workspace before validation instead of failing every
  tool/harness call for the session.
## Verification

- Repository smoke suite: `720 passed, 18 skipped`.
- UI suite unchanged from 2.2.1 (`1,264 passed, 6 skipped`).
- Harness workspace tests cover no-pin fallback, valid pin, relative pin,
  missing absolute pin, and invalid types.

## Compatibility

- No database migration or protocol change. A session with an explicit but
  unavailable pinned folder is still refused (the safe behavior); reselect it
  or start a new chat.
