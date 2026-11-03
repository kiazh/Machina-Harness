# Desktop migration progress

2026-10-29: user asked Codex to continue implementation directly instead of
handing the work to OpenCode. Tasks 0–23 of MACHINA_DESKTOP_OPENCODE.md are the
implementation roadmap; storage/remote scale projects remain separately gated.

## Workspace and baseline

- Working in the existing checkout to preserve the pending steering/background
  changes. Desktop code will be isolated under apps/desktop and packages/contracts.
- HEAD 86dd783; numerous pending harness/UI changes already present. No reset,
  stash, broad cleanup or incidental commit of those changes is authorized.
- Node 24.15.0, npm 12.2.0, Python 3.12.10 available.
- Rust/cargo and Visual C++ Build Tools not found. Native installation permission
  requested asynchronously; engine/contracts/prototype work continues meanwhile.
- WebView2 runtime installed: 156.0.4314.50.
- Native macOS runtime unavailable in this workspace; macOS verification will
  require a native runner and will not be claimed from Windows checks.
- Fresh terminal UI and Python baseline checks started; results recorded below
  when the commands finish.

Baseline results: npm run check passed (1188 tests passed, 10 skipped); pytest
tests passed (39 tests). New desktop bootstrap tests then ran RED (missing
module) to GREEN (4 tests including a real authenticated stdio subprocess).
Runtime capability tests ran RED (missing method) to GREEN (2 tests).

Native tools: user approved installation; Rust 1.99.1 installed with no global
PATH modification. Microsoft-signed C++ installer started with the VCTools
workload; MSVC 14.44.35207 is present. First Cargo compilation is running.

Design: user selected direction A from the concrete generated desktop board.
See docs/design/DESKTOP_BRIEF.md. Visual gate is satisfied for that base workspace;
do not implement a style collection or seed its illustrative outcomes as real state.

## Active work

Task 0 baseline; Task 1 toolchain/engine feasibility; Task 2 visual probes;
Task 5's independently testable Python bootstrap before native host compilation.
Direction A is approved; the production graphical shell now follows that brief.

## 2026-10-29 implementation evidence

- Rust 1.99.1 and MSVC 14.44.35207 installed with the user's authorization.
  Native Tauri compilation and real authenticated Python stdio requests work.
- Shared pure RPC/workspace contracts extracted to packages/contracts; old UI
  imports remain compatible. Legacy UI still passes 1188 tests / 10 skipped.
- Native bridge owns a Windows Job Object (Unix process group), gates Python
  startup until ownership exists, correlates bounded requests and rejects pending
  work on disconnect. Credentials remain inside the host. Development engine
  selection is explicit; managed engine packaging is not implemented yet.
- React/CodeMirror shell implements the approved editor + right harness layout,
  explorer, local buffer tabs/undo, revision-protected save, isolated streaming
  message subscriptions, virtual transcript, queue, approvals and job controls.
- First feasibility executable: 3,121,664 bytes (2.98 MiB), before full features.
  This is NOT total installation size or a finished client measurement.
- New desktop unit/integration suite: 19 passed, including mirror version refusal,
  UTF-16 edit ordering, save conflict and edits during in-flight save.
- Native suite: 8 passed with MACHINA_PYTHON set (real subprocess tests ran).
  Additional real initialization + workspace listing smoke passed with existing
  PostgreSQL; no model prompts or project writes were made.
- Edge browser: 3 Playwright scenarios passed: compact/desktop pane toggles,
  queued Enter + same-job Background/Output/Stop, and a 1500-row synthetic chat
  capped to 1000 retained rows during streaming + code typing. 10 message rows
  mounted; sampled rAF p95 7.2 ms. This is synthetic browser evidence, not native
  macOS or a sustained all-feature performance guarantee.
- Built-in browser connection failed with missing sandboxPolicy; isolated local
  Edge/Playwright is the fallback. Fixtures live outside the production entry.
- Language completion now flushes actual buffer changes, fences epoch/version,
  and rejects unsupported snippets/invalid/overlapping edits. Actual language
  server completion and native keyboard acceptance are still pending.

Ruling: keep implementation in the existing checkout and avoid broad commits —
the authorized in-progress harness changes must not be lost or accidentally
included in unrelated commits; isolation is by new desktop/package directories.

Remaining gates: durable drafts and dirty-close recovery; runtime snapshot and
idempotency; complete LSP/ghost flow; model/auth/settings/commands; PTY and export;
host byte budgets/event coalescing; native window/engine lifecycle; packaging,
updates and macOS acceptance. Do not describe the desktop migration as complete.

## Decisions

## Latest continuation (2026-10-29)

## Clean revision (2026-10-30)

User rejected eight design studies as clunky, approved a simpler revision, then
asked for stronger blue/white character. Implemented the cleaner layout with an
ink-blue Afterimage sidebar and ivory text, blue-white Chrome palette, small serif
identity and animated glyph, reduced borders/status duplication, and connection
recovery plus explicit Stop beside the composer. Existing buffers, drafts,
wallpapers, motion and typography controls remain intact.

Assistant messages now render Markdown headings/lists/code safely. Parsing is
limited to mounted rows and sampled every 125ms while streaming; completion
immediately renders the final response. Raw HTML is skipped, image URLs do not
auto-fetch, and links target a separate browsing context. Appearance migration
refreshes only the former factory accent once, preserving custom user settings.

32 unit/integration tests passed; 6 Edge scenarios passed, including visible
cancel awaiting the terminal event, recovery, customization and long-chat typing.
Sampled rAF p95 was 13.8ms during the concurrent native build. Fresh independent
review found no Important/Critical regressions. Latest actual-renderer screenshots
are design-previews/09-blue-white-working.png and 10-blue-white-empty.png; working
content is a synthetic fixture. The final native artifact rebuild is pending after
the last Markdown link/image safety change; do not confuse previous build with it.

Final native rebuild completed successfully after that safety change. The actual
Windows release client connected and opened README.md in the code companion.
No Important/Critical findings in the focused independent review. Latest palette
defaults to blue/white and migrates the former factory accent once, retaining user
overrides and wallpaper/font settings.

- User changed the desktop hierarchy to agent-first, then approved all three
  concrete graphical probes: Volt / Afterimage / Chrome. Reviewed all images in
  Downloads/New folder, including Nous Portal, blue halftone, engraved hands and
  editorial Hermes references. Those supersede the warm restrained direction A.
- Added original generated engraved artwork (public/art/afterimage.png), local
  self-hosted Oswald/Bodoni Moda/Manrope fonts, independent UI/code typography,
  custom local wallpaper stored as a Blob, opacity/blur/accent and motion controls.
  Three presets have different display/navigation type, composition and materials.
- Agent conversation occupies the main area; optional editor opens on file actions
  and remains mounted after hiding. Its bundle is deferred until first opening.
  Initial JS now 326 kB minified (100 kB gzip), editor deferred 513 kB (177 gzip).
- Recovery now persists code and composer drafts via bounded/debounced IndexedDB.
  Native close waits for recovery and asks in-product before stopping live work.
- Real Windows native window startup found a validator bug rejecting underscore
  in skills.preview_route. Added generated-registry regression RED→GREEN; actual
  native UI then connected, listed 30 project entries and opened README.md.
- Rust: 12 passed including real engine initialize/workspace read and actual
  Pyright completion on an unsaved mirror. Incoming events now batch at 16 ms,
  coalesce only adjacent same-session/same-turn deltas, and use a 4 MiB byte budget.
  Native outgoing requests are also byte-budgeted. No model prompts were submitted.
- Browser: agent-first responsiveness, queue/job controls, long transcript typing,
  IndexedDB recovery and wallpaper/style/motion persistence checked. Latest full
  four-scenario run passed; targeted fifth personalization scenario passed after
  correcting its fixture path. Full combined rerun remains to be recorded.
- Fresh reviewer found two Important races: typing during mirror reopen and
  recovery deletion after a successful save. Reproduced both RED, fixed with a
  preinstalled opening buffer and generation-fenced recovery removal; suite GREEN
  at 27 tests. Reviewer made no edits. No Critical findings.
- Final: minor (deferred): controller retains the full current assistant response
  separately from the capped visible transcript for canonical reconciliation.
- Optimized native build with final artwork/features is running. Prior 2.98 MiB
  executable is obsolete size evidence for the final preview.

Final verification: native build and preview launcher passed; executable is
6,302,208 bytes (6.01 MiB), client only. Native wallpaper CSP, all three presets,
bundled font and real file opening passed. Window close ended Python and launcher.
Full browser suite 5 passed; desktop 27; contracts 3; Python 6; native Rust 12;
legacy UI 1188 passed / 10 skipped with lint. See
docs/verification/DESKTOP_PREVIEW_20261029.md for scope and remaining gates.

Ruling: this is an engineering preview, not completed desktop parity. Ship no
installer/update claim before managed engine packaging, recovery/idempotency,
auth/model/command parity and native macOS acceptance are finished.

- The rejected terminal style feature stays reverted. Preserve queue steering,
  shell job lifecycle and visible Background/Output/Stop controls.
- Keep the existing PostgreSQL profile for the engineering preview.
- No promise of a 10 MiB full installation; client and engine sizes are separate.
