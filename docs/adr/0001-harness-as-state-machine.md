# ADR 0001: The harness is a state-management system

- Status: accepted
- Date: 2026-10-07 (recorded 2026-11-27)

## Context

The model is stateless and forgets everything between calls. A chat wrapper
resends the transcript until the provider truncates it, keeps no durable
knowledge, and treats a tool name as if it were a permission boundary. That
fails at the first crash, long approval wait, or large tool result.

## Decision

Treat durable state as the product and the prompt as a projection of it:

- PostgreSQL is the source of truth for sessions, typed context events,
  archives, memories, approvals, jobs, usage, and artifacts.
- A turn owns a leased, fenced claim; no transaction is held across a stream or
  a human wait.
- Context is assembled and fitted per request; old events are compacted into a
  checkpoint and archived, and large results become paged artifacts.
- Memory is a lifecycle of durable claims with provenance, correction, and
  bounded growth.
- Every side effect is canonicalized, digest-bound, approved by a human when
  required, and revalidated at execution.

## Consequences

- Every surface shares one turn engine and one persistence model.
- Recovery is a first-class path: crashed claims expire, approvals survive
  restarts, jobs are at-least-once, and compaction is reversible via the
  archive.
- More machinery than a chat loop: leases, projections, reducers, and durable
  approvals all need their own tests and operational limits.
- Documentation must describe failure semantics, not only happy paths.
