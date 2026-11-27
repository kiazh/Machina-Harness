# ADR 0004: Decompose the god objects by seams, not by line count

- Status: accepted
- Date: 2026-11-27
- Plan: `plans/refactor-god-objects/PLAN.md`

## Context

`machina/core/agent.py` (3,067 lines) and `ui/src/app.ts` (5,399) concentrate
the turn policy, tool execution, persistence, approvals, event routing,
keyboard precedence, workspace commands, and screen state. The suites cover
the behavior well, but a change to one concern has to be reasoned about in the
presence of all the others, and review diffs are no longer explainable.

The obvious split by line count ("agent_helpers", "app_utils") would produce
shallow modules: large arbitrary interfaces that move code without creating
leverage or locality.

## Decision

Extract **deep modules at real seams**, with the existing public interface kept
as a facade until every caller has moved.

- Python: `ContextPreparer`, `LLMCaller`, `ToolBatchExecutor`, `TurnState`,
  `TurnPhases`, `PostTurn`.
- UI: `StatusController`, `ChangesController`, `ApprovalController`,
  `SessionController`, `EventRouter`, `KeyboardRouter`, `CommandController`,
  `WorkspaceCoordinator`, `bootstrap`.
- Each controller receives a **focused host interface** with only the calls it
  needs. No `agent` parameter, no whole-`App` context object.
- A move task contains no behavior change. An interface task contains no
  move. Behavior changes get their own task and their own proof.
- The public facade (`ReActAgent.run/run_stream`, `App` methods used by
  components/tests) delegates until the end, so tests and transports are not
  rewritten to chase the refactor.

## Rejected alternatives

- **One `AppContext` object passed everywhere.** It recreates the god object
  behind a new name and makes every controller depend on every field.
- **Mixins.** They share `self` and preserve hidden coupling; the deletion test
  fails because deleting the mixin moves no interface.
- **Split by line count.** It produces shallow modules and arbitrary seams.
- **Rewrite the UI on a new framework.** The behavior and test surface are the
  asset; this refactor is about locality, not a platform change.

## Consequences

- More files, each with a small interface and explicit dependencies.
- Security-sensitive paths (tool batch, approvals, digest revalidation) move
  only after characterization traces exist.
- `agent.py` is a thin facade (≤700 lines), `_react_loop` is a phase orchestrator
  (≤150), and the tool batch exposes phase helpers rather than one 600-line
  method. `app.ts` is a composition root: there is no line target, only the
  rule that no extracted implementation remains duplicated there. The ratchet
  holds the current size and drops when a natural extraction appears.
- Every task is independently revertible; a move PR should be mechanically
  explainable without reading the implementation.
