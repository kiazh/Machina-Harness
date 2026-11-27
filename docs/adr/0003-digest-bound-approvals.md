# ADR 0003: Approvals bind a canonical action digest

- Status: accepted
- Date: 2026-10-10 (recorded 2026-11-27)

## Context

A tool name is not a security boundary. "Approve `terminal`" or "approve
`write_file`" can authorize a different command or different bytes than the
human saw, especially across a long wait, a retry, or a delegated child.

## Decision

Before an approval is shown, canonicalize the action and hash every material
field into a versioned digest: mode, backend, operation, tool, sorted
capabilities, argv, shell payload, cwd, canonical targets, content hash, file
base revision, network destinations, timeout, opaque-network flag, and
workspace root.

- The durable approval row stores the digest, session, agent, and turn
  separately.
- Resolution is human-only; agents cannot approve their own actions.
- Execution claims the approval atomically, once, with the exact digest.
- Execution revalidates the digest and the target's current bytes before the
  effect runs.
- Changed arguments or changed target bytes require a fresh approval.

## Consequences

- Approvals are not reusable tokens; session grants are a separate, narrower
  concept.
- Every tool must supply a faithful action mapping, and new fields are
  security-relevant by default.
- Long waits are safe: what executes is what was reviewed, or it is refused.
