# Machina 2.3.0

Harness jobs become real child chats.

## Added

- **Each background harness run creates a child chat session** parented to the
  owner session. The task and result are stored as `user_message` /
  `assistant_message` chunks, so `session.resume` can render the run and
  Activity can open it.
- **Activity navigation.** A harness job row (`harness-job:<id>`) opens its
  child session on Enter. The child also appears in the Agents section after
  the family refresh, so the existing agent-row navigation works too.
- **“Harness is asking” notifications.** A completion whose output looks like
  a request for direction (question mark, “which one”, “need more direction”,
  “can you clarify”, …) is flagged `needs_input`. The visible session gets a
  warning notice with the first line, and the Activity row shows
  `needs input`.
- `harness.job` snapshots carry `owner_session_id`, `session_id`, and
  `needs_input`; the job record is still filtered by its owner session.

## Changed

- `harness_jobs.start` is now async so it can create the child session before
  the run starts. Both the `harness_delegate` tool and `interop.run`
  background path await it; the harness process still starts immediately.

## Verification

- Repository smoke suite: `721 passed, 18 skipped`.
- UI suite: `1,264 passed, 6 skipped`; typecheck and lint clean.
- Real-database check: a mocked harness run created a child session with the
  correct parent, stored the task and “Which plane do you want?” result, set
  `harness_status=complete`/`harness_needs_input=true`, and was listed only
  for its owner session.

## Compatibility

- No database migration; the child session uses existing tables and state
  fields. Jobs remain in-process and do not survive a Machina restart.
