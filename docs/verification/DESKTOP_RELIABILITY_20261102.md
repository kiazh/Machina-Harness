# Desktop reliability and organization acceptance

Date: 2026-11-02. Host: Windows. This is engineering acceptance, not a human
participant study or a claim of complete CLI parity.

## Behavior implemented and corrected

- Grouped, searchable settings with direct query handoff to engine preferences.
  The header action is named Open settings. Appearance previews show each preset's
  own surface/accent. Scrolling surfaces use thin theme-aware scrollbars.
- Narrow editor layouts temporarily hide navigation without overwriting the user's
  saved layout. Widening restores it. Editor buffers remain mounted across pages.
- Offline feature pages explain the connection requirement and offer Connect engine.
  Advertised engine methods gate unsupported pages/actions. Failed inventories do
  not falsely report an empty collection; stale conversation data is cleared.
- Folder selection binds the editor and agent to one project. Desktop conversations
  retain a durable workspace pin, including child sessions and approval continuations.
  No global cwd/config change redirects unrelated CLI sessions or scheduled jobs.
  Reusing a binding preserves mirrors/servers. Reconnect binds the current session.
- Recovered buffers retain their project root, unsaved text and original disk revision.
  File reads, mirror acknowledgements, reloads and concurrent session transitions are
  fenced. Changing to another project cannot silently discard dirty saved files.
- Pending session creation, prompt steering/removal, fork completion and resumed
  history cannot pull the user into an older conversation or install its queue.
  Startup prevents folder selection until the engine has finished connecting.
- Reviewed skill staging is immutable per full digest. A repeated review repairs a
  damaged cache after checking recorded file hashes and tracked paths. Windows
  read-only Git objects can be cleaned within the installer's owned staging tree.
- Both generated RPC allowlists are checked against the literal Python registry.
  Desktop checks include shared contracts and preview-launcher tests. Windows/macOS
  CI is configured for frontend checks/build and native shell/lifecycle tests.
- A Windows lock on an older native executable no longer prevents an engineering
  preview build: only the specific final Cargo copy failure can use a fresh linked
  MZ executable, newer than the frontend output. Compiler errors/stale links stay
  failures. The latest validated artifact is selected by desktop:preview.

## Additional workflow port

- Selected saved or unsaved code can be attached through the composer, /attach or
  its configurable Ctrl/Command+Shift+A shortcut. Only the selected text enters
  the visible draft; nothing is submitted automatically. File/range/unsaved scope
  is explicit, size bounded, and code fences cannot be broken by selected ticks.
- Image picker, paste and drop connect to the existing prompt images contract.
  Five images and 4 MiB total stay below the desktop transport bound. Byte types
  are checked; failed submissions retain draft/images. Busy turns keep images for
  the next turn rather than silently discarding them from text-only steering.
  Encoded image data stays out of transcript/recovery. Closing warns about held
  unsent images because they are deliberately not persisted to the recovery store.
- Manual delegation supports up to 16 sequential or independent parallel steps,
  retained failure drafts, scoped results, approval details and lazy history.
  The existing backend cannot resume a returned paused manual workflow. The UI
  states that limitation and explains that rerunning repeats submitted steps.
- Language-server problems, symbols, hover, definitions and references have
  readable results and clickable locations. Stale/pending/unavailable and outside
  workspace locations cannot navigate. Captured source text/version/workspace are
  checked again after asynchronous target reads. Older loads can finish in the
  background without stealing focus from a newer choice. Navigation leaves
  settings and places the cursor in the actual code editor.
- Rich message formatting is deferred until needed. The minified main frontend
  bundle fell from 506.59 KB to 358.82 KB (151.50 KB to 106.90 KB gzip). The separate
  117.91 KB parser chunk is still part of the app and loads on demand.
- Shortcut capture now preserves printable punctuation/international keys and
  combined Control+Command chords. Default daily shortcuts still use no F keys.

## Complete history and long operations

- Conversation tools and `/history` open a searchable, read-only history dialog.
  Older/Newer/Latest navigation uses session/query-bound cursors. Rendering stays
  virtualized, and unreadable legacy candidates are counted as uncertain rather
  than falsely promising a complete negative search. Export includes delegated
  descendants and excludes separate forks.
- Archive reads retain original event sequence and known null timestamps across
  compaction, including old-style writers. Schema migration is serialized, and
  repeat initialization avoids rewriting archive rows. History response accounting
  is incremental and includes cursor/uncertainty overhead within its byte budget.
- Manual agent and external-harness runs start through short background-operation
  RPCs. A controller-owned tracker survives page unmounts, retains bounded results,
  prevents conflicting session changes, participates in close review, and polls
  without submitting the task again. Caller UUIDs recover lost start replies.
- Cancellation stays pending until the actual worker settles. External runs now
  propagate cancellation into the real process-tree runner and retain session
  ownership through thread cleanup, including repeated loop-retirement cancellation.
  A real harmless Python child-process test verifies this path on Windows.
- External harnesses use the selected session workspace. Missing, relative or
  unavailable workspace pins fail before launch; sessionless CLI calls retain
  their existing default. Diagnostic redaction fails closed.

## Verification

| Check | Result |
| --- | --- |
| Desktop TypeScript and Vitest | 185 passed across 37 files |
| Shared frontend/native contracts | 4 passed |
| Preview artifact/launcher guards | 3 passed |
| Local Python tests | 145 passed with disposable PostgreSQL |
| Browser workflows (Edge/Playwright) | 32 passed; 1 optional private-history case skipped because no input was retained |
| Native Rust with real Python engine/workspace/LSP enabled | 17 passed, including real PowerShell input/cwd/resize/exit/cleanup |
| Existing CLI check | Typecheck and lint passed; 1,188 tests passed, 10 skipped |
| Portable DevTools installer/gateway checks | 61 passed; 2 POSIX directory-fsync fault-injection cases excluded on Windows |
| Portable DevTools authority/session/approval checks | 28 passed |
| Portable DevTools context/compaction/family-export checks | 59 passed |
| Portable DevTools harness interop | 50 passed; 1 skipped, 2 POSIX-specific cases excluded on Windows |
| Ruff | All checks passed; all 325 Python source/tool/test files formatted |
| Native Windows engineering build | Passed, including publication beside the locked older executable |

External DevTools checks ran against fresh, UUID-named disposable local PostgreSQL
DBs, dropped after each run. No application database was used as a test target.
The Unix Bash/Makefile runner was not run on Windows. Two excluded fsync tests
expect a POSIX directory file descriptor; Windows intentionally uses best-effort
synchronization for directories. Harness tests additionally exclude a pgrep-only
process-list fixture and a POSIX 0600 mode assertion. Windows-specific durability
and actual process cancellation checks passed.

Latest synthetic 1,500-message scrolling/typing/streaming test measured 7.1 ms
rAF p95 on this host with 10 rendered rows. This is a local test result, not an
OS-wide frame-rate guarantee. Transcript previews currently retain at most 1,000
rows and 65,536 characters per message. The separate history dialog pages and
searches the complete stored conversation without expanding the main transcript.

Two additional current-source replays used redacted real recorded history read
inside a read-only local DB transaction: Monster Hunter (51 stored chunks,
24 transcript entries) and the largest archived chat (1,229 stored chunks,
1,000 transcript entries, 255,936 characters). Simultaneous typing, scrolling and
stream append measured 865/879 ms typing time, 7.2/7.4 ms frame p95 and 9/25 mounted
rows, respectively. Both passed. Private JSON inputs were deleted as each test
loaded them; no transcripts or user titles are committed. The 32-workflow suite
leaves this optional private-input case skipped; these two separate replay runs
exercise it with real local data. This remains browser acceptance, not a native
WebView frame-rate guarantee or a claim about a different machine.

The actual native window was opened and visually inspected with its local engine
connected. Ctrl+, opened settings. The terminal shortcut opened a real PowerShell
prompt at the selected workspace. UI Automation exercised the shell-close review
and confirmation and closed that idle acceptance shell. Native captures remain
in ignored local acceptance storage because they contain local conversation titles.
Browser fixtures are explicitly synthetic and excluded from production builds.

Independent read-only review reproduced queue-removal, fork-completion and startup
folder-selection races. Each received a failing regression test before its fix.
The reviewer found no additional editor data-loss or workspace/approval-authority
blocker in that pass. A second review reproduced stale-result navigation and old
file loads stealing newer focus; source snapshot and navigation generation tests
now cover both. Reconnect received an additional session-binding regression.

## Remaining migration and release work

The [parity ledger](../plans/desktop-cli-parity.md) is the current scope record.
Complete CLI argument shorthand remains to port.
Rename requires an additional engine API, rather than an existing CLI endpoint. Real provider accounts, external MCP OAuth,
learning flows and external harnesses need integration acceptance with those
services; fixtures prove the interface, not service availability.

The macOS CI job has been added but has not run in this workspace. No actual macOS
host was exercised here. Signed packaging, clean-machine installs and automatic
updates require a verified release pipeline and are not claimed complete. The
approximately 6.6 MB Windows executable does not include the Python dependencies.

No pushes, real provider prompts or third-party installs into the user's
skill registry were performed in this acceptance pass.
