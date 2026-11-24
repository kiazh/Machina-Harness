# Machina 2.2.1

Keyboard-freeze hardening for slow commands.

## Fixed

- **`/harnesses` can no longer run in the foreground.** The previous patch
  backgrounded runs only when a session was active; a missing session fell
  back to the old “Consulting Hermes… waiting for its bounded run” path, which
  held `commandRunning` and swallowed every key for up to the full harness
  deadline. The command now refuses without a session and always starts a
  background job.
- **Slow commands release ordinary typing.** A slash command keeps the
  keyboard for its 1.5s dialog window (so a leading `/` cannot leak into the
  composer). After that window, printable keys reach the editor while Enter
  and Escape stay reserved for the command. Any future long-running command
  can no longer make the TUI look frozen.

## Verification

- Repository smoke suite: `720 passed, 18 skipped`.
- UI suite: `1,264 passed, 6 skipped`; typecheck and lint clean.
- External suite: `6,548 passed, 13 skipped, 2 xfailed` on the 2.2.0 tree; the
  2.2.1 deltas are UI-only and covered by the new app/harnesses tests.

## Compatibility

- No database, config, protocol, or CLI change.
