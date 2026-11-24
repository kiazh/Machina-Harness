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

## Verification

- Repository smoke suite: `721 passed, 18 skipped`.
- UI suite: `1,264 passed, 6 skipped`; typecheck and lint clean.
- Real-database check: `prompt.submit` on a child with
  `harness_platform=hermes` forwarded the reply, emitted
  `message.complete` with the harness answer, and stored both the user and
  assistant chunks in that child session.

## Compatibility

- No database migration. Harness chat jobs remain in-process and do not
  survive a Machina restart; the stored transcript does.
