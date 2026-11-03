# Task usability pass

## What this evidence means

This is a heuristic review and automated task walkthrough, not a human-participant
study. No participant feedback, preference rating or satisfaction score is claimed.
The product is not yet feature-complete; the CLI parity ledger remains authoritative.

## Tasks exercised

| Task | Evidence and result |
| --- | --- |
| Create and save code | Browser flow creates a buffer, types code, opens Save As with Ctrl+S, saves under a workspace-relative path, verifies saved tab and retained text |
| Prevent accidental overwrite | Unit test verifies missing-file compare-and-swap refuses an existing file and retains the original buffer |
| Close a dirty tab safely | Browser flow cancels discard and verifies edits remain |
| Discard and reopen | Editor integration verifies discarded contents are not resurrected from the tab cache |
| Type during delayed close | Regression verifies new edits cancel tab closure and remain dirty/recoverable |
| Steer ongoing work | Browser flow queues a prompt, edits it inline, and verifies the main composer stays untouched |
| Move among features | Existing workflows verify drafts and edited code survive capabilities/model/settings navigation |
| Work in long sessions | Existing 1,500-message streaming and typing case retains a bounded DOM and approximately 7.1 ms rAF p95 on this host |

## Changes

New-file Save As uses the existing permission-checked workspace.save endpoint with
expectedRevision null, so it cannot silently replace an existing file. Saving
retains editor state/history and reconfigures language highlighting for the saved
extension. File tabs have distinct selection/close controls and arrow/Home/End
keyboard navigation. Server document mirrors are closed with tabs. Recovery stays
intact until a close operation safely completes. Queue editing uses the engine's
existing prompt.steering replacement contract.

## Checks and limits

44 unit/integration checks pass. Browser suite now includes 14 workflows. Windows
native and frontend builds are checked separately. Native macOS testing, real
provider task acceptance, interactive PTYs, and remaining advanced CLI controls
are pending. Human feedback should cover first-use navigation, control labeling,
comfort in a 30-minute coding session and whether activity details interrupt focus.
