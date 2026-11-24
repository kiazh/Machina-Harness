# Machina 2.4.1

Harness chats keep the conversation — and stop garbling the answer.

## Fixed

- **One word per line.** Hermes streams token-level `text` events. Machina
  rendered every event as its own line, so an answer arrived as
  `I / don't have any / prior context ...`. Token text is now concatenated
  verbatim; tool and system events keep their own lines.
- **Duplicated final answer.** The completion kept the live log and then
  appended the answer when a raw substring check failed across line breaks.
  Transcript and stored message now compare normalized text and append only
  what is missing.
- **No conversation continuity.** Every turn used to start a fresh harness
  session. Machina now persists the harness session id on the child session
  and resumes it on the next turn:
  - Hermes: `hermes chat --resume <session_id> ...`
  - OpenCode: `opencode run --session <session_id> ...`
  The id is stored as soon as it appears, so an interrupted turn still leaves
  a resumable conversation behind.
- **Headless prompts could stall a run.** Hermes now runs with
  `--oneshot --yolo --accept-hooks`; OpenCode with `--format json --auto`.
  Without a TTY an approval or clarify prompt previously blocked until the
  deadline killed the process.
- **Research turns timed out at 5 minutes.** Harness chat turns are now bounded
  at 10 minutes; delegated one-shot runs keep their own bound.

## Changed

- **DeepSeek-only model policy.** Automatic model recovery never crosses to
  another vendor: the replacement candidates are filtered to DeepSeek ids, and
  a provider-suggested model is used only when it is DeepSeek. A run's model
  can be pinned with `MACHINA_HARNESS_MODEL` or
  `MACHINA_HARNESS_MODEL_<PLATFORM>` (for example
  `MACHINA_HARNESS_MODEL_OPENCODE=opencode/deepseek-v4.1-flash-free`).
- OpenCode runs use `--format json`, so the session id is captured and tool
  events render as `→ tool: input` / `← output`; the answer is the
  concatenated text events.

### Activity screen

- New header (`Machina · Activity`, live/idle, freshness), filled filter pills,
  uppercase section bands with counts, aligned `STATUS` / `UPDATED` columns,
  relative timestamps, and a `❯` selection marker.
- The inspector shows title, state/section/age chips, project/detail/runtime/
  queue rows, and a count-labelled log block; one hint line says whether Enter
  opens the conversation or toggles logs.
- `1–7` switches filters, the footer lists the keys, and narrow terminals get
  a list plus an inline Inspector instead of a single scrolling column.
- Empty sections and inspectors explain what they are waiting for.

## Verification

- Repository suite: `726 passed, 18 skipped`; ruff check and format clean.
- UI suite: `1,266 passed, 6 skipped`; typecheck and lint clean.
- Architecture ratchet: 26 passed (deferred-import cap unchanged).
- Real OpenCode run with a DeepSeek model (`opencode/deepseek-v4.1-flash-free`):
  turn 1 streamed `→ shell: echo hi` / `← hi` and answered `done` in 21.5s;
  the follow-up resumed the same session and answered `hi` in 17s.
- Hermes session resume was verified directly against `hermes chat --resume`
  (`Zara, turbofan engines` recalled on the second turn). Hermes runs on this
  machine require credits for DeepSeek models; Machina reports that error
  instead of switching to another vendor.

## Compatibility

- No database migration. Harness session ids live in session state; existing
  child sessions start a new harness conversation on their next turn.
- Harness jobs remain in-process; a restart cancels running jobs but keeps the
  stored transcripts and session ids.
