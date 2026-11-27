# TUI Design Contract

The governing philosophy is [Signal Grid](PHILOSOPHY.md): content is the
interface, one grid, one signal, type before boxes, and a numbered turn rhythm.

This describes the implemented terminal interface: tokens, density,
interaction states and the theme contract. Behaviour is enforced by the suite in
`ui/test/`; run `npm run check --prefix ui` before changing this file.

## Visual language

- **Moonstone** uses plum graphite, porcelain text and mint controls. Chrome,
  list and input surfaces are visibly distinct. Other skins keep their palettes
  and use the same hierarchy. `design.test.ts` enforces WCAG AA contrast for
  every text role against the terminal canvas in all 73 skins.
- Terminal monospace only. No gradients, shadows, large colored panels,
  emoji controls or decorative ASCII art. Chrome labels are single-line:
  embedded newlines flatten, control bytes and bidi overrides are stripped,
  and ANSI-aware truncation never leaves a split escape.
- User turns show a quiet numbered `❯` prefix with continuation lines aligned
  beneath the prompt. The band is filled only when the theme paints backgrounds.
- Assistant responses are unframed prose on a readable 96-column measure,
  introduced by a small `·` text glyph; no heading, card, fill or closing rule.
- Tool activity is one unframed outcome-marked line (`✔`/`✖`/`●`/…) with a
  right-aligned status, capped at 108 columns. Results appear beneath it as `⎿`
  plus at most two preview rows; diff lines keep add/remove colors.
- The composer is an inset, raised **Message** dock. Its label picks up the
  focus color, and **Paste image** sits above the draft when it fits. Draft
  wrapping and click targets use the visible input width. Below 12 rows it
  becomes a plain input line.
- Notices and thinking use the same gutter. Thinking reads
  `✶ Thinking (3s · esc to interrupt)`; the snowflake cycles its four
  six-spoked forms `✶ ✼ ✽ ✻`, which share a cell and a centre. Below 40
  columns, extra gutters disappear.
- Approval evidence stays in a bounded transcript card. The decision lives
  in a raised panel above the composer, with an explicit title and a filled
  current choice.
- The footer keeps model · branch · mode · status, tokens, hints and the
  bottom-right activity square.
- Bold is reserved for brand, selections, approval titles and Markdown
  emphasis. Every state keeps a text marker so meaning survives monochrome
  skins: `❯` for prompts, `●` for live work, `✔`/`✖`/`⊘`/`!` for outcomes.

## Command strip

- Two rows at normal heights, one on short viewports. It is present on chat,
  Activity and Changes, so the three screens are real tabs rather than
  full-viewport replacements.
- Left: the `MACHINA` wordmark and tabs. Counts appear only when nonzero. The
  active tab uses fill and brackets. At wider widths, click **Keys** for the
  shortcut guide.
- Right: session title, project · branch, model and the live state. Context
  drops as width shrinks; the state chip is kept last and truncated when
  necessary. Below 56 columns the wordmark yields to it.
- Tabs are clickable and work through the same screen entry points as
  `/activity`, `/changes` and `Alt+←`/`Alt+→`. `Alt+1/2/3` select the chat,
  editor, or split layout instead. Pending decisions stay docked, but browsing
  pages never traps the user.
- Pages receive the shell's viewport height; their own header row is a quiet
  section title, never a second brand.

## Layout and density

- A fresh workspace opens chat beside the editor where space allows, and
  collapses to chat alone on narrow terminals. The explorer stays a drawer
  until requested. `Alt+1/2/3` or `/workspace chat|editor|split` changes the
  layout.
- One retained transcript `ScrollView` for the shell's lifetime (`AppShell`);
  composer and footer stay docked. Resizes update density before layout measurement,
  without waiting for input or gateway activity. Viewport classes: full (≥16 rows, gaps),
  reduced (9–15, no gaps, composer window 1–4), compact (6–8, one status row,
  composer 1–2), degraded (<6 rows or <20 columns: resize hint, draft kept).
- Full interactive target is 40×9 and up. Every widget renders inside any
  width 0–120 without throwing; pathological widths are rendering inputs,
  not promises of a complete one-column form.
- Follow-end scrolling freezes while scrolled up and resumes on explicit
  return-to-end. Composer caps multiline input around the visible cursor
  (max 8 rows full density); empty and one-line states share a baseline.

## Music bar

- A pinned now-playing bar sits above the transcript in the chat column, as a
  sibling of the retained `ScrollView`, so it never scrolls away or rebuilds
  scroll state. It shows **what is playing on this computer**, not a library of
  its own. Three rows: title, artist/source, transport. Two rows: title and
  transport. One row: `title — artist · elapsed`. Compact and degraded
  densities hide it. A click toggles playback when the source accepts commands.
- Detection is a gateway concern (`nowplaying.get`, `nowplaying.command`):
  local-only and layered from system media APIs to nowplaying-cli, Spotify or
  Music, then a front browser tab. Automation denial is reported with its
  System Settings path; unknown playback stays `unknown`; a missing title is
  never invented. Elapsed time interpolates between polls. `/music` is the
  keyboard surface and `nowplaying_enabled=false` hides the bar.
- Detection reads local app state and never uploads metadata.

## Honest states

- Tools: `running`, `awaiting_approval`, `success`, `error`, `interrupted`,
  `outcome_unknown`, `historical`. Missing completions are never success;
  cancellation preserves completed outcomes; history never infers outcomes.
- Canonical final text reconciles once (withheld tails append; replacements
  replace; duplicates are inert). Cumulative usage adds once per turn,
  including non-visible sessions. The 300 s watchdog warns once and keeps
  the guard; disconnect is offline with local commands intact.
- Approvals resolve `{requestId, sessionId, verdict, grant}` after
  revalidation. Enter confirms Allow once by default; a session grant requires
  selection. Esc denies explicitly, while navigation,
  exit and disconnect invalidate without resolving. Decisions record; only
  later tool events prove execution. Approval evidence is retained in the
  transcript; only vertical decision controls occupy the dock. Queues attach
  evidence on activation, preserving the active request’s evidence in short
  viewports. Long operations/targets remain wrapped and pageable. Late
  management dialogs wait hidden with their drafts intact until the approval
  releases focus. Externally resolved requests advance the queue; terminal
  completion retires unanswered controls without issuing decisions.

## Interaction

- Enter submits, Shift+Enter inserts a newline, double-Esc stops or refills,
  Ctrl+C stops/clears/exits, Ctrl+D exits when idle/empty, Tab completes,
  `?` (empty chat, Activity, Changes), `Alt+H` and **Keys** open shortcuts.
  Safe commands work mid-turn. `Alt+V` or **Paste image** reads the image
  directly when the terminal handles `Ctrl+V` as text paste.
- Menus own a real case-insensitive filter (label/value/description) with
  honest empty states and selection-preserving budgets. Forms validate
  inline, keep drafts on failure and save exactly once. Secrets are masked
  everywhere and redacted from failure text.
- Retained tool output (8 newest, 128 KiB each, 1 MiB total) opens in a
  scrollable viewer; evicted output reads unavailable.

## Changes and review runs

- Click the top tabs or use `Alt+←` / `Alt+→` to move through Activity, Chat and
  Changes. `Esc` returns to Chat. `/changes` opens the current chat's changes;
  a blank chat has none. The command strip stays visible.
- Activity and Changes share an index/evidence layout. On wide terminals the
  index stays at left while evidence scrolls on the right. On narrow terminals,
  a short list around the selection sits above the evidence. Activity rows
  retain status in the index, name shell jobs by their command, and show an
  output preview before metadata. `Space` expands details, `PgUp`/`PgDn` pages
  them, and Enter opens a session or agent when the item has a destination.
- In Changes, a numbered prompt index stays visible while **Files**, **Diff**
  or **Review** shows evidence for the selection. Click a view or press `1/2/3`
  or Tab. `↑/↓` selects prompts, `[`/`]` selects files, `n`/`p` selects a
  finding, and `PgUp`/`PgDn` pages evidence. `Space` opens the full prompt.
  `r` reviews the diff with the deterministic rules and `m` reviews it with
  the session's current model; `x` cancels a review. `Ctrl+C` cancels a running
  reply from either page, and `Ctrl+D` exits when idle.
- The unit is **one prompt**, not a commit. A prompt block keeps its captured
  before/after content, so a later prompt editing the same file does not
  rewrite an earlier block's diff. File rows keep the filename when a path
  cannot fit, and the diff wraps long code lines instead of clipping them.
- Review shows one finding's title, severity, location and explanation in one
  reading column. A short clickable finding list appears above it when space
  permits. `PgUp`/`PgDn` page longer explanations. Reviews never
  start a repair automatically. Press Enter or click **Fix in Chat** to send a
  follow-up to the active chat, or queue it as guidance if a reply is running.
  The action never creates a chat or replaces the composer draft. Sent findings
  stay listed as `Sent` or `Queued` until `c` clears them; cleared entries are
  not offered again. A review rechecks the original snapshot, not the repaired
  working tree, so neither sending a repair nor re-running that review marks it
  fixed.
- Every section has an honest empty state. Binary files don't pretend to have
  a text diff. Viewing evidence never edits files or re-runs the original turn.

## Debugging

- UI logs are off by default. `--log <path>` or `MACHINA_UI_LOG=<path>`
  (or `MACHINA_UI_DEBUG=1` for `<MACHINA_HOME>/logs/ui.log`) writes NDJSON;
  `--log-level` / `MACHINA_UI_LOG_LEVEL` sets the level. Defaults:
  `debug`, 5 MB rotation, secrets redacted by field name.
- Scopes: `main`, `app`, `gateway`, `transcript`, `changes`, `status`, `tool`.
  The app logs every RPC (`rpc.ok`/`rpc.failed`), screen and session changes,
  Changes refresh/diff/review/fix steps, approvals and render failures.
  Session titles, keys/answers/prompts and token-shaped strings are redacted.
- The gateway writes `gateway.log` at DEBUG, including workspace snapshot and
  review summaries.

## Motion

- One `AnimationCoordinator` per App owns at most one timer. One-cell activity
  frames (`·`, `•`, `●`, `•`) advance every 200 ms and keep labels still. The
  corner square keeps one shape (`▪`) while brightness rises and falls at
  the same cadence. Working footer demand survives the thinking indicator
  hiding on text deltas; ready, approval, stopping and cleanup retire it.
  Reduced motion (`MACHINA_UI_REDUCED_MOTION=1|true`) keeps activity static without
  a recurring frame timer. Saved learning uses one 800 ms pulse, coalesced
  to once per 30 s, with no chat notices; idle acknowledgments request their
  first frame immediately. Growth expiry and disposal retire subscriptions.
  Cleanup/stopping markers freeze without claiming completion; exit clears
  motion and watchdog resources.

## Themes

- 73 skins (`SKINS` in `ui/src/design/theme.ts`); `codex` is the fresh-install
  default, explicit saved themes (including legacy `default`) are respected.
  `/theme` previews live with cancel-restore and persists exactly once; failed
  saves report temporary.
- Adding a skin means one entry in `ui/src/design/theme.ts` (validated
  required IDs in `REQUIRED_THEME_IDS`); styles resolve lazily so switches
  never clear content, drafts, scroll or execution bindings.

## MCP inspection

`/mcp` opens a server picker when inventory exists. `/mcp list` and
`/mcp status <id>` inspect the worker's actual shared ConnectorService.
Disabled, disconnected, connecting, connected, degraded, auth_required and
error remain distinct; negotiated protocol is displayed rather than guessed.
Inspection is read-only and does not connect or discover tools. Default
inventory is empty and in-memory. Durable setup, connect/disconnect UI, OAuth,
external app inventory and agent tool dispatch remain outside this feature.

## Feature awareness

Both ordinary and streamed agent calls receive the same compact implementation
brief, including agents with custom prompts and turns where growth preparation
fails. It distinguishes requested persona tone, stored fact interpretations,
emotion-weighted recall, saved learning and runtime RSI. Recalled conversations
are not implementation documentation; empty notes are not missing features.
The guide does not claim model-weight training, application self-rewriting,
expanded permissions or completed MCP remote dispatch.

Current flags are allowlisted; emotion values are recognized topology names or
an explicit neutral-fallback label, and unknown modes remain unknown. The
`harness_status` model tool, when offered, uses existing `context.read` authorization,
verifies active session ownership, reads only that agent's learning state and
redacts output. Oversized notes/persona/trials have explicit omission markers;
an oversized remaining snapshot is refused rather than truncated into invalid
JSON. This mutable, agent-specific result is never cached globally.

See [visual verification](../../internal/docs/verification/TUI_OVERHAUL_REVIEW.md)
and the [renderer capture](../../internal/docs/archive/artifacts/tui-reference.png).
