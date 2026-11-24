# Machina 2.4.0

Two-way harness chats.

## Added

- **Harness child sessions accept replies.** A child session with
  `harness_platform` state is no longer inspection-only. Sending a message
  stores it as the child's user turn, starts a background harness job in the
  same session (`reuse_session=True`, no extra child), and streams the
  harness answer back as `message.start` / `message.delta` /
  `message.complete`. The answer is also stored as an `assistant_message`
  chunk, so reopening the child shows the full conversation.
- Only harness-backed sessions get this path. Native delegated children still
  show "Subagent sessions are for inspection."
- `SessionInfo.harnessPlatform` is serialized from session state so the UI can
  make that distinction without a title heuristic.
- Harness chat turns use the same durable `TurnLifecycle` claim as normal
  turns: a second submit while a harness reply is running is fenced with
  `TURN_IN_PROGRESS`.
- **Live harness output.** `run_bounded` accepts an `on_output` callback and
  every `interop.run_task` caller passes it through. Background jobs store the
  last 80 redacted lines, emit `harness.job` updates about once a second, and
  expose `progress` in the snapshot. Activity shows the latest line and the
  last 20 lines; a two-way child chat streams the output as `message.delta`
  before `message.complete`. The capability is platform-neutral and works for
  OpenCode, Codex, Hermes, and any future harness registered in the shared
  runner.

## Verification

- Repository smoke suite: `722 passed, 18 skipped`.
- UI suite: `1,264 passed, 6 skipped`; typecheck and lint clean.
- Real-database check: `prompt.submit` on a child with
  `harness_platform=hermes` forwarded the reply, emitted
  `message.complete` with the harness answer, and stored both the user and
  assistant chunks in that child session.

## Compatibility

- No database migration. Harness chat jobs remain in-process and do not
  survive a Machina restart; the stored transcript does.
