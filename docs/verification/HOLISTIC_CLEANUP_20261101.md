# Holistic workspace cleanup

Independent source reviews identified conflicting navigation state, inconsistent
reading widths, duplicated worker/command controls and ineffective personalization.
The cleanup retains the approved agent-first blue/white composition.

Implemented: Files/Activity return to the workspace; opening files reveals the
editor; the Code toggle opens a previously hidden workspace reliably; connection
settings no longer duplicate the More menu; Escape interrupts outside the composer
while preserving modal/editor dismissal; feature pages retain a visible running or
approval affordance. Font, accent, artwork and blur settings affect their intended
surfaces. Chat, tool rows, command status and composer share one reading axis.
Command controls occupy the terminal when it is open rather than duplicating in
chat. Worker inspection has one set of tabs. Scrolled transcripts offer Jump to
latest. File pagination failures preserve already-loaded entries. Model changes
show pending state and prevent repeated selection.

Verification: 40 unit/integration checks and 12 browser workflows pass. Regression
tests reproduced the Files navigation and font failures before their fixes. The
1,500-message synthetic streaming/typing check remains around 7.1 ms frame p95
with 10 rendered rows. Screenshot: `20261101-clean-workspace.png`.

Remaining lifecycle work includes full save-as/new-file support, tab close actions,
interactive PTY sessions and the feature-parity items already recorded in the CLI
migration ledger. This cleanup does not claim those features are implemented.
