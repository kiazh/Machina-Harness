# Machina product context

## Register
Product: a local agent workspace with an editable code workspace, available as
a terminal client and a native desktop application.

## Users and purpose
The primary user gives the agent tasks, inspects progress, steers its work, and
opens code to inspect or edit alongside it on Windows and macOS. The desktop is
agent-centric: conversation and execution occupy the main area by default.
The user clarified this with a Hermes desktop reference on 2026-10-29.

## Character and references
Calm, direct, capable. Familiar editor layouts and the readable execution state
of Codex, OpenCode, and Hermes are anchors from the user's stated preferences.
Keep the existing selectable themes and semantic color roles.

## Principles
- Give agent conversation the main workspace; open the editor alongside it on demand.
- Keep file editing, undo and buffers intact when the editor pane is hidden.
- Show actual queued, running, completed, failed, and interrupted states.
- Enter queues steering during work; Escape interrupts immediately.
- Keep optional identity motion isolated from work rendering; never perform
  full-history work on keystrokes and animation frames.
- Accessible keyboard controls on Windows and macOS; no daily F-key defaults.
- Respect reduced motion and maintain meaning without color alone.

## Anti-goals
No metrics dashboard, nested cards, permanent verbose status panels,
or speculative success messages. Performance must be measured with real sessions.
