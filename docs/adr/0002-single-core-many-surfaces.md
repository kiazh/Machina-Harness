# ADR 0002: One turn engine behind every surface

- Status: accepted
- Date: 2026-10-08 (recorded 2026-11-27)

## Context

The terminal UI, CLI, HTTP API, MCP server, ACP bridge, and external harnesses
all need turns, tools, approvals, and events. Reimplementing the loop per
surface produces subtly different harnesses and duplicated security bugs.

## Decision

Keep one transport-agnostic turn engine (`TurnLifecycle` → `TurnEngine` →
`ReActAgent`) and one gateway RPC registry. Surfaces adapt transport and
presentation only:

- stdio JSON-RPC for the TUI,
- HTTP+SSE and `/rpc` for remote clients,
- MCP stdio for other agents,
- ACP for editor clients,
- bounded child processes for interop.

Approval behavior is a transport policy (`interactive` vs `headless`) over the
same durable rows, not a second code path.

## Consequences

- Protocol drift is a real risk; method and event contracts are generated and
  pinned by tests.
- A bug fixed in the engine reaches every surface, and a regression does too.
- Surfaces must agree on capability ceilings; the MCP server and HTTP surface
  deliberately expose less than the local TUI.
