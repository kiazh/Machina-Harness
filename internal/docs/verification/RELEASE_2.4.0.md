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
- **Live "thinking/doing" log.** A two-way harness chat keeps the streamed
  log and appends the final answer; the same text is stored on the child
  session, so reopening it shows what the harness did. Activity shows the
  latest line plus the last 20.
- **Structured Hermes progress.** Hermes runs as
  `hermes chat --query-file - --format stream-json` (prompt on stdin) and its
  events render as `model: …`, `→ tool: …`, `← tool: …`, with the final
  answer taken from the structured `result` event.

## Fixed

- **Streaming was buffered until exit.** `run_bounded` read through
  `TextIOWrapper.read(8192)`, which blocks until the buffer fills or the child
  exits. It now reads raw bytes with `read1`, so every harness streams live.
- **Hermes model recovery.** On an exhausted-credit failure Machina now skips
  the provider's suggested paid model, tries live free models fastest-first,
  refuses to start an attempt with under 12 seconds of budget, and reuses the
  last model that worked for the rest of the process. First run recovers in
  about 30 seconds; later runs start on the working model.
- Retried-attempt provider prose and `session_id:` lines no longer appear in
  the chat or Activity output, so a recovered retry does not read as a
  failure.
- A job's terminal status is published only after its final progress flush and
  transcript store.

## Verification

- Repository smoke suite: `722 passed, 18 skipped`.
- UI suite: `1,264 passed, 6 skipped`; typecheck and lint clean.
- Real-database check: `prompt.submit` on a child with
  `harness_platform=hermes` streamed deltas at 3.6s / 19.6s / 25.1s / 25.4s /
  28.4s (`model`, `model`, `→ tool`, `← tool`, answer), emitted
  `message.complete` with that full log plus the answer, and stored both the
  user and assistant chunks in that child session.
- Real harness runs: OpenCode completed a delegated task in 7s; Hermes
  recovered from its broken default model and completed in 22–31s (first run)
  and about 8s afterwards.

## Compatibility

- No database migration. Harness chat jobs remain in-process and do not
  survive a Machina restart; the stored transcript does.
