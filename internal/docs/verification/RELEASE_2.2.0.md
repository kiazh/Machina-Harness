# Machina 2.2.0

Background harness subagents and Activity navigation.

## Added

- **Activity opens agents.** The full-page status view now opens the selected
  agent or persisted session on Enter (through `session.resume`), then returns
  to chat. Space remains the explicit expand/collapse key for retained logs;
  the footer documents both.
- **Harnesses as background subagents.**
  - `harness_delegate(..., background=true)` (the default when a session is
    active) starts the external OpenCode/Codex/Hermes run in the background and
    returns `{status: "started", job_id, platform}` immediately.
  - `harness_job` lists this session's jobs, reads one, or waits up to 300s.
  - Jobs are bounded (64 records, 8 running per session, capped output), shown
    in Activity under Harnesses, and stream a `harness.job` gateway event on
    start and completion. A terminal event adds a short completion notice to
    the visible session's transcript.
  - `/harnesses` → Run a task uses the same background path, so a hung external
    CLI can no longer block the UI until the 315s client deadline.
- **`/delegate` no longer freezes input.** The slash command returns
  immediately and prints the validated child result when `agents.run` finishes;
  previously it held `commandRunning` for the entire delegation, and every key
  was consumed as if the keyboard had stopped working.

## Fixed

- Harness retries share one overall deadline. The first attempt and the
  suggested/free-model retries used to each receive the full timeout, so a
  background job could outlive its requested budget by 3×.

## Verification

- Repository smoke suite: `720 passed, 18 skipped`.
- UI suite: `1,263 passed, 6 skipped`; typecheck and lint clean.
- Manual: background `harness_delegate` starts and reaches a terminal state;
  timeout is enforced at the overall deadline; `/delegate` returns before the
  child completes; Activity Enter callback is unit-covered.

## Compatibility

- No database migration.
- `harness_delegate` returns a start payload by default in an active session;
  callers that need the old inline behavior pass `background=false`.
