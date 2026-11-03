# Paste into OpenCode

Use this after reviewing the proposed plan. It does not authorize a database
migration, a remote service, a public release or restoring the rejected terminal styles.

```text
Work on Machina's graphical desktop migration.

Read these two files completely before making changes:
1. docs/plans/MACHINA_DESKTOP_SPEC.md
2. docs/plans/MACHINA_DESKTOP_OPENCODE.md

Follow applicable AGENTS.md instructions. Preserve all current user changes.
The desktop plan was written against HEAD 86dd783 plus uncommitted steering,
background jobs and visible command controls. Inspect the current working tree;
do not reset to that old commit. The Paper/Blueprint/Signal/Classic terminal
style feature was explicitly reverted at the user's request. Do not revive it.

The proposed stack is Tauri 2 + React/TypeScript + CodeMirror 6, with the existing
Python harness behind an authenticated Rust-owned stdio bridge. Keep the current
terminal client operational. Do not rewrite the harness or swap the database
as part of the initial desktop implementation.

Begin with Task 0 and Task 1: verify baseline behavior, inspect actual gateway
and workspace contracts, and measure a real native packaging/engine spike.
Resolve and lock compatible dependencies rather than copying moving ranges.
Make the client/engine/OS-prerequisite size breakdown explicit. A <=10 MiB
desktop client is a target; it is not a promise about the full installation.

Prepare Task 2's visibly different graphical directions and an interactive
prototype. Get approval of the actual design before production visual work.
Continue independent protocol, lifecycle, packaging and performance research
while that visual choice is pending.

Once the foundation/design gates are approved, implement the preview tasks in
dependency order through a working real-engine vertical slice. Use native
execution unless I authorize delegation. Maintain docs/plans/desktop-progress.md
with completed tasks, commands/results, measured evidence, decisions and next work.

Performance is a release requirement. Verify long-chat typing and scrolling,
eight-agent streaming, noisy commands and repeated tab/settings cycles. Keep
the editor locally responsive; no per-token whole-app rerenders or full-history
Markdown parsing. Measure release builds on Windows and macOS. Browser tests
alone do not prove native platform behavior.

Preserve Enter-to-queue, contextual Escape, real Background/Output/Stop controls,
safe provider/auth settings, actual LSP/ghost completion and temporary subagent
inspection with safe report transfer before child retirement. Do not promise
hallucination-free models or manufacture progress/test success.

Use tests that reproduce the required behavior, inspect actual native screens,
and fix failures before claiming completion. Keep toolchain/build test plugins
out of production artifacts. Do not publish, push, delete user data, port storage
or build the later remote/multi-tenant roadmap without separate authorization.
```
