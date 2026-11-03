# Desktop connectors, editor feedback and workflow polish

Implemented the approved agent-first workspace without changing the independent
CLI. The existing nine palettes, custom wallpapers, editor buffers and drafts
remain supported.

## Result

- Marketplace titles and review actions work without an open conversation.
  Installation-owned consent stays in the authenticated gateway; session-owned
  reviews still require the same conversation and agent. Exact revision, file
  digests and explicit installation confirmation remain required.
- The default connector catalog offers Gmail, Google Drive, Google Calendar,
  GitHub, self-hosted Firecrawl, custom MCP and Machina's outgoing harness bridge.
  Setup is browsable without a conversation. Saving and connection/OAuth review
  retain the existing session ownership. Configured and connected are separate
  states derived from the actual engine inventory.
- Connector setup scrolls its own pane and focuses the first editable field.
  The application header and sidebar stay in place.
- Harness integrations has a Communications tab for actual incoming/outgoing MCP
  and external CLI exchanges, paired details, source/state/search filters, pause
  and refresh. Completed requests inherit their observed exchange settlement in
  the list instead of appearing permanently active.
- Communications are redacted before local SQLite persistence. Retention is
  bounded to 1,000 events, 4 MiB logical payload and a 5 MiB main database cap.
  Reads use a readonly connection. Writes run off the protocol event loop with a
  25 ms SQLite lock budget, and cancellation joins the writer. Recording failure
  does not change a tool's result. The UI retains at most 200 events and polls
  only while its tab/document is visible and updates are enabled.
- Python/JavaScript/TypeScript parser errors receive inline squiggles and gutter
  markers. Live language-server diagnostics use exact buffer/workspace versions;
  old responses cannot replace newer feedback. Reads are debounced 500 ms and
  cancelled logically on edits, document changes and unmount. Actual diagnostic
  notifications refresh the active file; there is no editor diagnostic polling.
- Existing LSP completion displays ghost text and accepts with Tab. Ordinary
  indentation, read-only buffers, composition and document version checks remain.
- Memory approvals distinguishes a failed load from an empty queue. Model search
  offers no-match feedback and clear search. Navigation scrolls at 720 × 480 while
  Settings remains reachable.
- The built-in wallpaper is now a generated blue/silver astrolabe engraving.
  Fresh appearance settings use built-in art; explicit custom/none choices stay.

## Verification

- `npm run desktop:check`: artifact tests 3, contract tests 4, desktop tests 210
  passed, including TypeScript.
- Active Python suite with a disposable local PostgreSQL database: 178 passed.
  The runner creates and removes its own database; it never uses user history
  as a mutable test fixture. Archived failure demonstrations are not active tests.
- Edge browser suite: 45 passed, 1 optional private-history performance test
  skipped. All nine palettes and minimum-size navigation are covered.
- Additional targeted browser pass after connector focus/scroll refinement:
  5 passed. Targeted MCP/catalog/communications integration checks: 23 passed.
- Native Rust bridge suite: 17 passed, including real PTY input/resize/exit,
  real workspace reads, and actual Python completion **and versioned diagnostics**
  against an unsaved editor mirror.
- CLI: 1,188 passed, 10 skipped, with TypeScript and ESLint passing.
- Ruff checks and format checks pass; generated RPC contracts match the registry.
- Synthetic long-chat browser check: approximately 7.2 ms p95 animation-frame
  interval while typing/streaming/scrolling, with 10 transcript rows mounted.
  This is a local browser measurement, not a guarantee for every machine.
- Final telemetry regressions cover independent process writers, real MCP
  success/error/cancel, redaction on disk, readonly reads, actual SQLite lock
  contention, worker isolation and cancellation during a request recording.
- Final Windows engineering preview: `machina-preview-1793684089648.exe` built
  and opened successfully. Native UI acceptance found all default connector
  actions, fetched the live marketplaces, and opened an actual `frontend-design`
  repository review with installation enabled and no conversation open. No skill
  was installed. The preview remains open on marketplace discovery.

Synthetic render captures: [Communications](20261103-harness-communications.png)
and [self-hosted Firecrawl setup](20261103-selfhosted-firecrawl.png). Native
captures remain ignored locally because they contain real conversation titles.

## Setup and platform limits

Firecrawl is intentionally **self-hosted**. The starter is
`http://127.0.0.1:3000/mcp`, an editable MCP bridge URL. It is not the Firecrawl
REST API URL. Configure `FIRECRAWL_API_URL` on that bridge to point to the existing
self-hosted REST backend. No cloud fallback, Docker installation or local server
startup was performed. This Windows checkout currently has no confirmed running
Firecrawl stack or configured endpoint.

Google's official Workspace MCP services require Developer Preview access,
enabled APIs and a registered OAuth client. The form exposes client ID and
credential-reference setup; catalog availability does not mean the account has
been connected. Third-party OAuth, email operations and skill installation were
not exercised against user accounts. No real provider prompts were submitted.

This acceptance run is on Windows. The implementation uses shared browser/Rust/
Python paths and preserves macOS shortcuts, but no native macOS run or signed
release/updater acceptance was performed here.

Primary setup references: [Google Workspace MCP](https://developers.google.com/workspace/mcp),
[GitHub MCP server](https://github.com/github/github-mcp-server),
[Firecrawl MCP bridge](https://github.com/firecrawl/firecrawl-mcp-server).

## Generated wallpaper

Built-in image generation tool; saved to
`apps/desktop/public/art/afterimage.png` (3,495,581 bytes). Original retained in
the Codex generated-images directory. No image editing script was used.

Prompt: an ultrawide Deus Ex Machina desktop wallpaper, antique copperplate
engraving combining medieval celestial craftsmanship and precise cybernetic
mechanisms; cropped armillary sphere and astrolabe in the right third; fine
orbital arcs and mechanical details; cool silver ink, cobalt and blue-gray on
pale blue silver paper; refined, mysterious, asymmetrical tactile printmaking;
left 65% calm negative space for software content; no words, logos, faces,
robots, neon, mockup frames or noisy decoration.

[Exact generation prompt](../design/wallpaper-20261103.prompt.txt).
