# Desktop CLI parity

Updated 2026-11-02. The desktop and terminal CLI are separate frontends and builds.
They share the Python engine and generated, allowlisted RPC contracts. Neither
frontend imports the other frontend. “Implemented” means real local/backend controls
exist; automated fixtures do not establish real-provider or macOS acceptance.

| Capability | Desktop implementation | Outstanding acceptance or scope |
| --- | --- | --- |
| Prompt, cancellation, steering | Enter queues at the tool boundary; configurable interrupt; queue edit/remove; protected in-flight session transitions | Actual-provider long task acceptance |
| Conversations | Create, resume, remote search/pagination, rename, fork, delete, goal and export | Full paged/searchable history is connected; further native and provider acceptance remains |
| Context and recall | Budget, chunks, compression, recall/evidence windows, family | Richer evidence navigation |
| Models and routing | Session-aware catalog, provenance/fetch errors, off-list IDs, effort controls | Real account/catalog availability remains provider-dependent |
| Providers, keys and accounts | Provider add/edit/remove, transient masked key entry, ChatGPT/Copilot/Grok login/import/logout/status controls | Real-account sign-in and turn acceptance |
| Skills marketplace | Featured discovery, popular skills.sh listings, SkillsMP development listings, multi-catalog search/filter/deduplication, exact source/revision/files/digest review, confirmed installation | Real search/review/staging accepted; no third-party skill installed into the user's registry during verification |
| Installed skills and learning | Search/filter/show/remove, repository import, section reads, routing preview, proposals, approvals/reverts, health | Real model-backed learning acceptance |
| Memory | Scoped list/search/add/share/confirmed forget and installation-wide memory approvals | Real model-backed retrieval quality |
| Scheduled jobs | Interval/cron/heartbeat creation, optional scripts/model overrides, enable/disable/resume/delete | No job edit endpoint exists in the current CLI engine; edit-style recreation can be designed separately |
| Agents | Definition CRUD, protected built-ins, multi-step sequential/parallel delegation, scoped results and history; tracked background runs retain status across page navigation and offer Stop | Manual approval pauses expose details; the existing engine cannot resume a settled manual workflow, so rerunning repeats its steps |
| Live subagents | Inspect live activity; close/reopen active tabs; retire finished tabs | Actual-provider parallel workload acceptance |
| Agent shell jobs | Background, output, stop and restore on conversation resume | Actual-agent long-running command acceptance |
| Interactive terminal | Native PTY, tabs, keyboard input, resize, bounded ANSI output, process cleanup; retained when hidden | Actual Windows PowerShell passed; native macOS interaction pending |
| Code workspace | CodeMirror, Quick Open, file tree, protected Save/Save As/close, undo retention, disk-change reload, crash recovery | Explicit selected-code draft attachment, image picker/paste/drop and failed-send retention are connected; images are limited to 4 MiB total by the desktop transport |
| Workspace authority | Folder selection pins current/future desktop conversations; children inherit; execution and approval use the pin; global CLI root unchanged | Further multi-window/live-session acceptance |
| Code intelligence | Versioned LSP completion, ghost suffix and Tab acceptance, configurable trigger; server lifecycle and diagnostics/symbol/hover/definition/reference inspection | Clickable problems/symbols/definitions/references with source-version guards are connected; rename needs a new engine API, and additional language profiles remain optional expansion |
| MCP | Configuration, transport-aware edit, remove, exact connection review, connect/disconnect, tool inspection, OAuth review/lifecycle | Real external server/OAuth acceptance |
| Profiles, personality and growth | Profile selection/create/preferences; teach/forget, notes, feedback, policy review/rollback | Real model-backed learning acceptance |
| Permissions | Execution mode, live approvals, default selection, policy review and revocation | Real-provider approval/resume acceptance |
| Harness integrations | Discovery, registration review, enable/remove, tracked task execution with owned process cancellation and session-selected cwd | Acceptance against locally installed external harnesses |
| Music | System media status and playback controls; unavailable state when unsupported | Native media-provider-specific acceptance |
| Appearance and layouts | Nine persisted palettes, wallpaper/glass/typography/motion, layout presets, pane placement/dimensions, density and configurable keyboard shortcuts (including punctuation/international keys) | Shared keyboard-accessible dropdowns, consistent selection and compact settings search are connected; fonts and layout remain separately adjustable |
| Availability | Offline connection action, advertised method checks, explicit errors; failed inventories do not claim to be empty | Some specialized advanced controls still need broader unavailable-state acceptance |
| Slash commands | Local feature navigation and selected direct operations; unknown/unsupported arguments retain draft and do not reach the model | Complete CLI argument/subcommand shorthand parity |
| Windows native | Engine transport, folder/list/read, unsaved LSP completion and real PTY tests passed | Signed distributable, clean-machine installation, updater/release pipeline |
| macOS native | Shared macOS code paths and a new CI build/test matrix | The CI job is configured but has not run here; actual macOS acceptance pending |

This is a working migration, not a claim that every advanced CLI workflow or
platform has been accepted. Current evidence is recorded in
[the reliability report](../verification/DESKTOP_RELIABILITY_20261102.md).
