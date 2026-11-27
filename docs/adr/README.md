# Architecture decision records

Short, immutable records of decisions that shape the codebase. Supersede an
entry with a new ADR rather than rewriting history.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-harness-as-state-machine.md) | Treat the harness as a state-management system, not prompt engineering | Accepted |
| [0002](0002-single-core-many-surfaces.md) | One turn engine behind every surface | Accepted |
| [0003](0003-digest-bound-approvals.md) | Bind approvals to a canonical action digest and revalidate at execution | Accepted |
| [0004](0004-god-object-decomposition.md) | Decompose the god objects by seams, keeping the public facade | Accepted |
