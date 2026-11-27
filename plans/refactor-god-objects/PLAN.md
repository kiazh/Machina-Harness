# God-object decomposition plan

Status: proposed
Scope: `machina/core/agent.py` (3,067 lines), `_react_loop` (555),
`_execute_tool_calls_stream` (596), `ui/src/app.ts` (5,399).
Goal: **locality and testability**, not a line-count target. Public behavior,
event ordering, protocol frames, and durable state must not change.

This is deliberately staged: every task is independently mergeable and
reversible, and no task mixes a code move with a behavior change.

## Progress (2026-11-27)

| Task | State | Evidence |
|---|---|---|
| R0.1 metrics + ratchet | done | `tools/architecture_report.py`, baseline, CI step |
| R0.2 Python golden traces | done | `tests/test_golden_turns.py`, 12 traces in `tests/golden/turns` |
| R0.3 UI golden traces | done | `ui/test/golden-ui.test.ts`, 36 frames + 14 key steps |
| R0.4 ADR + seams | done | `docs/adr/0004-god-object-decomposition.md` |
| P1.1 `agent_support.py` | done | 168-line module; `agent.py` re-exports |
| P1.2 `context_prep.py` | done | 411-line preparer; `agent.py` 318-line method removed |
| P1.3 `llm_calls.py` | done | retry/settlement seam; `agent.py` delegates |
| P1.4 `post_turn.py` | done | consolidation/cursor/learning seam |
| P2.2 `tool_results.py` | done | recording/artifacts/approval retirement |
| P2.3a `tool_batch.py` | done (pure move) | 606-line executor; constants moved |
| P2.4a `react_loop.py` | done (pure move) | 555-line loop module |
| P2.1 `turn_state.py` | done | 188-line `TurnState`/`TurnBudget`; loop reads state; stop/counter table tests |
| P2.3b explicit tool interface | done | `ToolBatchHost` Protocol; 4 phase methods |
| P2.4b phase split | not started | loop still one function |
| U3.1 status controller | done | `ui/src/app/status.ts` (569) |
| U3.2 changes controller | done | `ui/src/app/changes.ts` (438) |
| U3.3 approvals | done | `ui/src/app/approvals.ts` (420); wired |
| U3.4 sessions | done | `ui/src/app/sessions.ts` (435); wired |
| U4.1–U4.5 standalone modules | done | events/keyboard/commands/workspace/bootstrap + tests |
| U4 integration into App | in progress | single-writer pass |
| P5 final ratchet/docs | pending | — |

Measured after the Python tranche:

| File | Before | Now |
|---|---:|---:|
| `core/agent.py` | 3,067 | **698** |
| `core/tool_batch.py` | — | 973 (method split; `execute_stream` 68) |
| `core/react_loop.py` | — | 583 (533-line method; phase split in progress) |
| `core/turn_state.py` | — | 188 |
| `core/context_prep.py` | — | 411 |
| `core/turn_phases.py` | — | 404 |
| `ui/src/app.ts` | 5,399 | 3,982 (before U4 integration) |
| `ui/src/app/status.ts` | — | 569 |
| `ui/src/app/changes.ts` | — | 438 |
| `ui/src/app/approvals.ts` | — | 420 |
| `ui/src/app/sessions.ts` | — | 435 |
| `ui/src/app/events.ts` | — | 442 |
| `ui/src/app/keyboard.ts` | — | 648 |
| `ui/src/app/commands.ts` | — | 422 |
| `ui/src/app/workspace.ts` | — | 1,153 |
| `ui/src/app/bootstrap.ts` | — | 520 |

Both safety nets (R0.2/R0.3) are in place. The remaining work is method-level:
split `react_loop` into phases, then continue the UI controllers (approvals,
sessions, events, keyboard, commands, workspace, bootstrap).

---

## 0. Design position

Use the deep-module vocabulary throughout:

- **Module**: a cohesive unit with a small interface and a large implementation.
- **Interface**: signatures plus invariants, ordering, error modes, configuration,
  and performance facts a caller must know.
- **Seam**: the place where a module's interface lives.
- **Depth**: leverage at the interface, not a line ratio.
- **Deletion test**: if deleting the module moves no complexity, it was a
  pass-through and should not exist.

Two rules drive the whole plan:

1. **The existing public interface survives the refactor.** `ReActAgent.run` /
   `run_stream`, the `StreamEvent` / `_LoopTerminal` sequence, and the `App`
   methods used by components and tests stay in place as thin delegators until
   every caller has moved. Tests and transports are not rewritten to chase the
   refactor.
2. **Move first, design second.** A pure move lands with no logic edits, then a
   separate task introduces the new interface and explicit dependencies. This
   keeps `git blame`, `git bisect`, and review tractable.

### Target shape

Python:

```
core/agent.py            # class wiring + run/run_stream adapters        < 700 lines
core/context_prep.py     # ContextPreparer.prepare()                     ~350
core/llm_calls.py        # LLMCaller.stream/complete + usage settlement  ~220
core/tool_batch.py       # ToolBatchExecutor.execute()                   ~450
core/tool_results.py     # result recording, artifacts, approvals        ~220
core/turn_state.py       # TurnState + budget/stop invariants            ~250
core/react_phases.py     # prepare/llm/tool/answer/terminal phases       ~400
core/react_loop.py       # ~100-line phase orchestrator                  ~120
core/post_turn.py        # consolidation, learning, growth               ~300
core/agent_support.py    # instrumentation + small pure helpers          ~150
```

UI:

```
ui/src/app.ts                  # composition root + facade                < 900 lines
ui/src/app/bootstrap.ts        # start, workspace host, now-playing       ~300
ui/src/app/sessions.ts         # create/switch/family/steering            ~380
ui/src/app/status.ts           # Activity snapshot/refresh/inspect        ~380
ui/src/app/changes.ts          # Changes + review + fix handoff           ~450
ui/src/app/approvals.ts        # approval dock + resolve/rehydrate        ~280
ui/src/app/events.ts           # gateway/child event router               ~380
ui/src/app/keyboard.ts         # onInput/onKey/workspace key routing      ~350
ui/src/app/workspace.ts        # workspace host/coordinator              ~1,000
ui/src/app/commands.ts         # submit, slash dispatch, menus            ~550
ui/src/app/contracts.ts        # narrow host interfaces per controller    ~120
```

`app.ts` keeps every existing method as a one-line delegation during the
transition. Controllers receive **focused host interfaces**, never the whole
`App`.

---

## 1. Guardrails

- No behavior change: no protocol fields, event order, terminal shape, DB row,
  timeout, concurrency bound, or error string changes inside a move task.
- One file, one owner at a time. `agent.py` and `app.ts` are single-writer files
  for the duration of the refactor.
- Each task changes at most one of: (a) file location, (b) interface, (c)
  behavior. Never more than one.
- Every task lowers or holds a ratchet: file length, method length, import
  cycles. A ratchet never rises.
- New modules get a docstring stating their interface and invariants.
- Tests cross the same seam as callers. Do not add tests that reach into a
  private method of a new module unless it is a deliberate internal seam.
- Rollback is a revert of one task, not a coordinated multi-file rollback.

---

## 2. Milestone 0 — safety net (blocks everything)

### R0.1 Architecture metrics and ratchet (S, 0.5–1 day)

Add `tools/architecture_report.py`:

- per-file line count;
- per-function/method line count;
- max nesting depth per function;
- internal import edges and cycles;
- top fan-in/fan-out modules.

Emit JSON and a human table. Add `--check` with a checked-in baseline file
(`tools/architecture_baseline.json`). CI fails when a tracked file or method
grows past its baseline, and the baseline is only lowered in a task that
actually reduces it.

Acceptance:

- `python tools/architecture_report.py --check` passes at HEAD.
- Baseline records `agent.py`, `app.ts`, `_react_loop`,
  `_execute_tool_calls_stream`, `_prepare_context`.
- No runtime imports from `tools/`.

### R0.2 Python turn characterization harness (M, 1.5–2 days)

Add `tests/golden/` and `tests/test_golden_turns.py`.

A scripted fake provider drives `_react_loop` through:

1. plain answer;
2. one tool call then answer;
3. three concurrent tool calls with out-of-order completion;
4. approval pause and resume;
5. cancellation mid-stream;
6. provider 502 then success (one retry);
7. provider 401 (actionable failure terminal);
8. empty response recovery;
9. repair edit + verification + forced answer;
10. auto-parallel plan;
11. delegated child result;
12. budget exceeded.

Record a stable trace: event `type`, text length + SHA-256 (never raw text),
tool names and order, usage totals, terminal `outcome`, terminal content hash,
and the sequence index. IDs and timestamps are normalized. A golden update
requires `--update-golden` and a reviewed diff.

Acceptance:

- All 12 traces are byte-stable across two runs.
- Deliberately reordering two events fails the trace test.
- The harness runs in under 10 seconds.

### R0.3 UI behavior characterization harness (M, 1.5–2 days)

Extend `ui/test/support/reference.ts` with:

- deterministic gateway event scripts for Chat, Activity, Changes, approvals,
  editor open/save/conflict, quick open, and now-playing;
- render snapshots at 20/40/56/80/120 columns and 6/8/15/40 rows;
- key-sequence traces for navigation, approval decisions, Changes, and editor
  focus.

Normalize timings, cursor blink, and animation frames; keep a reduced-motion
trace as the canonical render.

Acceptance:

- Chat/Activity/Changes snapshots are stable.
- An injected extra render row fails the width invariant test.
- Key traces assert the same selected region, screen, and draft after replay.

### R0.4 ADR and seam review (S, 0.5 day)

Write `docs/adr/0004-god-object-decomposition.md` with:

- the seams above and why each is a real seam (varying implementation or
  independently testable behavior);
- the facade/delegation rule;
- the move-then-design rule;
- the rejected alternatives (e.g. one `AppContext` object, mixins, splitting by
  line count).

Acceptance: ADR reviewed and linked from each task.

---

## 3. Milestone 1 — Python leaf extractions (parallelizable)

All tasks are pure moves plus import rewiring. No class behavior changes.

### P1.1 `core/agent_support.py` (S, 0.5 day)

Move module-level `instrument_run`, `instrument_stream`, growth listeners,
`_notify_growth`, `_is_rate_limit_error`, `_is_transient_error`, `_tool_outcome`,
`_assistant_chunk`, `_LoopTerminal`.

Acceptance: `agent.py` re-exports the names it still uses; golden traces green;
no logic diff.

### P1.2 `core/context_prep.py` (M, 1–2 days)

Extract `_prepare_context` (318 lines). Interface:

```python
class ContextPreparer:
    async def prepare(
        self, session_id, user_message, *,
        include_memory: bool,
        pending_chunks: list,
        images: list[dict[str, str]] | None,
    ) -> tuple[Session, list[dict[str, Any]]]
```

The module owns: fresh session read, user chunk persistence, continuity,
memory/RAG retrieval, projection, assembler construction, and image handling.
`agent._prepare_context` delegates. Dependencies (`session_manager`,
`context_manager`, retriever, RAG pipeline, config) are constructor arguments,
not module globals.

Acceptance: golden traces green; a test injects a failing RAG pipeline and a
missing session and asserts the same fallback and error as before.

### P1.3 `core/llm_calls.py` (M, 1 day)

Move `_call_llm_with_retry`, `_stream_llm_with_retry`, `_reserve_usage`,
`_finish_usage`. Interface:

```python
class LLMCaller:
    def stream(self, messages, tools, session_id) -> AsyncIterator[StreamEvent]
    async def complete(self, messages, tools, session_id) -> LLMResponse
```

Owns: single transient retry, no retry after first delta, usage reservation and
settlement, provider failure mapping. `agent.py` delegates.

Acceptance: retry/truncation/failure golden scenarios unchanged; a test asserts
exactly one retry on 502 and zero on 429.

### P1.4 `core/post_turn.py` (M, 1–2 days)

Move consolidation cursor/scheduling, `_consolidate_memories`, learning review,
growth completion. Interface:

```python
class PostTurn:
    def schedule(self, session_id) -> None
    async def consolidate(self, session_id) -> None
```

Preserve advisory-lock and in-flight behavior exactly; the scheduler stays
fire-and-forget and failure-isolated.

Acceptance: golden turn traces show no added latency in the turn path; a test
proves a consolidation failure does not change the terminal response.

Milestone 1 exit: `agent.py` ≤ ~2,200 lines; no new import cycles.

---

## 4. Milestone 2 — Python core (sequential; highest risk)

### P2.1 `core/turn_state.py` (M, 1–2 days)

Introduce `TurnState` and `TurnBudget`:

```python
@dataclass
class TurnState:
    session_id: UUID
    messages: list[dict]
    pending_chunks: list[dict]
    total_tokens: int
    iteration: int
    tool_calls_made: list[dict]
    task_run: TaskRunPolicy
    turn_snapshot: object | None
    def record_usage(...) -> None
    def stop_reason(...) -> str | None
    def flush(self) -> Awaitable[None]
```

Initially it only wraps existing counters; the loop reads/writes it, but the
control flow is unchanged. This is the seam the phase functions will share.

Acceptance: identity of every stop reason and counter transition is covered by
a table test.

### P2.2 `core/tool_results.py` (M, 1 day)

Move `_tool_entry`, `_record_tool_action*`, `_ensure_artifact_view`,
`_retire_tool_approval`, `_finalize_completed_tools*`, `_append_tool_messages`.
Interface: `record(tool_call, result, outcome) -> None` and
`finalize(pending) -> None`.

Acceptance: artifact handles, approval retirement, and cancellation cleanup
tests unchanged.

### P2.3 `core/tool_batch.py` (L, 3–4 days, split into two commits)

**P2.3a pure move**: move `_execute_tool_calls_stream` (596) and
`_execute_tool_calls` (34) into `ToolBatchExecutor` in the same file, preserving
phase order, ContextVar publication, semaphores, and event order. `agent.py`
keeps a one-line delegate.

**P2.3b explicit interface**:

```python
class ToolBatchExecutor:
    async def execute(
        self, response, *,
        allowed_tools: list[str] | None,
        session_id: UUID,
        tool_calls_made: list[dict],
    ) -> AsyncIterator[StreamEvent]
```

Dependencies: registry, broker, permission context resolver, artifact store,
audit/metrics, delegate capacity, task-run policy. No `agent` parameter.
ContextVars are published by the executor and reset in `finally`.

Acceptance:

- concurrent ordering test (starts in request order, completions as-ready,
  records replayed in provider order);
- approval pause/resume and denial;
- cancellation mid-batch leaves completed results durable;
- DB failure during recording produces the same terminal failure;
- golden traces byte-identical.

### P2.4 `core/react_phases.py` + `core/react_loop.py` (L, 3–4 days)

Extract phases in this order, each a separate commit:

1. `prepare_turn` — advisory, snapshot, context, user persist, auto-parallel.
2. `llm_step` — gate, stream/non-stream, usage, provider failure.
3. `tool_step` — delegate to `ToolBatchExecutor`.
4. `answer_step` — no-tool path, empty recovery, repair nudges, completion.
5. `terminal_step` — stopped/failed shapes, flush, consolidation schedule.

`_react_loop` becomes an orchestrator of those five calls plus the while loop.
`agent.py` keeps `BaseReActAgent` as the dependency carrier/factory and a thin
`run`/`run_stream` adapter.

Acceptance:

- `_react_loop` ≤ 150 lines; `agent.py` ≤ 700;
- every golden trace identical;
- cancellation, deadline, budget, provider failure, and empty-response tests
  unchanged;
- DevTools suite green (authoritative).

Milestone 2 exit: no module in `core/` over 900 lines; no new import cycles.

---

## 5. Milestone 3 — UI leaf extractions (parallelizable)

Every controller gets a focused host interface in `ui/src/app/contracts.ts`:
only the client/transcript/footer/request/render methods it actually calls.
`app.ts` methods delegate until the end.

### U3.1 `app/status.ts` (S, 1 day)

Move `statusSnapshot`, `refreshStatusData`, `inspectActivity`,
`inspectShellJob`, `harnessJobRows`, nav counts. Interface:
`StatusController` with `snapshot()`, `refresh()`, `inspect()`.

Acceptance: Activity golden render + 3-second refresh trace unchanged.

### U3.2 `app/changes.ts` (M, 2 days)

Move changes snapshot/refresh, diff loading, review start/cancel, fix-in-chat,
handled findings, failure prompts. Interface: `ChangesController`.

Acceptance: per-prompt isolation, late-response fencing, review hydration,
`Fix in Chat` queueing, and rendered Files/Diff/Review snapshots unchanged.

### U3.3 `app/approvals.ts` (M, 1.5 days)

Move `promptApproval`, dock, `resolveApproval`, invalidate/clear/restore/
rehydrate. Interface: `ApprovalController` with `prompt()`, `resolve()`,
`invalidate()`, `restore()`.

Acceptance: approval golden traces (allow once/session/deny, expiry, dead card,
delegated child binding) unchanged. This is security-relevant; no behavior edits.

### U3.4 `app/sessions.ts` (M, 1.5 days)

Move session creation/switch/update/prune, family rows, steering queue/flush,
tail replay. Interface: `SessionController`.

Acceptance: session-switch trace, stale-response fencing, family cycling, and
`SessionTailStore` bounds unchanged.

Milestone 3 exit: `app.ts` ≤ ~3,600 lines.

---

## 6. Milestone 4 — UI core (sequential on `app.ts`)

### U4.1 `app/events.ts` (M, 2 days)

Move `onEvent` (309) and `onChildEvent` (46). Interface:
`EventRouter.route(event)`; it binds turns, filters stale frames, routes
workspace events separately, and feeds transcript/activity/turn state.

Acceptance: malformed-frame, stale-turn, hidden-session tail, and child-agent
golden traces unchanged. This is the highest-risk UI move.

### U4.2 `app/keyboard.ts` (M, 1.5 days)

Move `onInput`, `onKey`, `handleWorkspaceKey`, `matchesExplorerKey`. Interface:
`KeyboardRouter.handle(key) -> boolean`. Precedence order stays in one place:
overlay → approval → page → workspace → chat.

Acceptance: key-sequence goldens for every documented binding; editor text keys
still do not trigger navigation.

### U4.3 `app/commands.ts` (M, 2 days)

Move `submit`, command action dispatch, skills pickers/search, menus that are
command-shaped. Interface: `CommandController.submit(text)`,
`dispatch(action)`.

Acceptance: submit/steering/queueing, `/changes`, `/activity`, `/skills`,
`/theme`, and secret-bearing command guards unchanged.

### U4.4 `app/workspace.ts` (L, 3–4 days)

Move the workspace host surface, focus/region logic, completion/intelligence
orchestration, save/conflict, problems, quick open. Interface:
`WorkspaceCoordinator`; `App` keeps the few methods components call.

Acceptance: workspace controller/layout/focus/persistence/quick-open/editor
tests unchanged; editor buffer and draft survival across layout/theme changes.

### U4.5 `app/bootstrap.ts` (M, 1 day)

Move `start`, `workspaceSurfaceHost`, now-playing startup, gateway exit
handling. Interface: `bootstrap(app)`.

Acceptance: boot trace, workspace-disabled path, gateway-exit notice, and
now-playing startup unchanged.

Milestone 4 exit: `app.ts` ≤ 900 lines; no method over 80 lines.

---

## 7. Milestone 5 — close the ratchet

### P5.1 Remove transitional stubs (S, 0.5 day)

Delete delegating methods with no remaining callers, collapse imports, update
`docs/adr/0004` and the module map in README Appendix I.

### P5.2 Tighten CI (S, 0.5 day)

- `agent.py` ≤ 700, `_react_loop` ≤ 150.
- No duplicate controller implementations in `app.ts`.
- No method over 80 lines in the extracted modules.
- Import-cycle allowlist decremented to the pre-refactor count or lower.
- Golden trace suites required.

Acceptance: ratchet fails when a file is padded past its ceiling.

---

## 8. Task table

| ID | Task | Depends | Size | Risk |
|---|---|---|---|---|
| R0.1 | Architecture metrics + ratchet | — | S | low |
| R0.2 | Python turn golden harness | — | M | low |
| R0.3 | UI render/key golden harness | — | M | low |
| R0.4 | ADR + seam review | R0.1–R0.3 | S | low |
| P1.1 | `agent_support.py` | R0.4 | S | low |
| P1.2 | `context_prep.py` | R0.4 | M | medium |
| P1.3 | `llm_calls.py` | R0.4 | M | medium |
| P1.4 | `post_turn.py` | R0.4 | M | medium |
| P2.1 | `turn_state.py` | P1.* | M | medium |
| P2.2 | `tool_results.py` | P2.1 | M | medium |
| P2.3a | pure move tool batch | P2.2 | L | high |
| P2.3b | tool batch interface | P2.3a | M | high |
| P2.4 | react phases + loop | P2.3b | L | high |
| U3.1 | `app/status.ts` | R0.4 | S | low |
| U3.2 | `app/changes.ts` | R0.4 | M | medium |
| U3.3 | `app/approvals.ts` | R0.4 | M | high |
| U3.4 | `app/sessions.ts` | R0.4 | M | medium |
| U4.1 | `app/events.ts` | U3.* | M | high |
| U4.2 | `app/keyboard.ts` | U4.1 | M | medium |
| U4.3 | `app/commands.ts` | U4.2 | M | medium |
| U4.4 | `app/workspace.ts` | U4.2 | L | high |
| U4.5 | `app/bootstrap.ts` | U4.3, U4.4 | M | medium |
| P5.1 | remove stubs + docs | all | S | low |
| P5.2 | tighten ratchet | P5.1 | S | low |

Rough effort: **~4–5 weeks single engineer**, **~2.5–3 weeks with two**
(file ownership: one on Python, one on UI; `agent.py`/`app.ts` never edited by
both at once).

---

## 9. Parallelization rules

- R0.1–R0.3 can run in parallel.
- P1.1 must land first; P1.2/P1.3/P1.4 can then run in parallel in separate
  worktrees, each touching only its new module plus a small `agent.py` delegate.
- P2.* and P3.* are sequential within Python because they share `TurnState` and
  the loop.
- U3.1–U3.4 can run in parallel; each adds a controller and a delegate method.
- U4.* are sequential because they rewrite the `App` event/key/command spine.
- Merge order follows dependencies, not task number. Rebase daily on the
  integration branch; never merge a red golden suite.

---

## 10. Risk register

| Risk | Mitigation | Detection |
|---|---|---|
| Hidden coupling through `agent.self` / ContextVars | Explicit constructor deps; no `agent` parameter in new interfaces; ContextVars set/reset in one module | Golden traces + fault injection |
| Event order or terminal shape changes | R0.2/R0.3 traces; assert sequence index, not just content | Trace diff |
| Cancellation/cleanup regression | Fault injection for cancel at each phase; claim/approval assertions | `test_golden_turns`, DevTools |
| Test doubles reach private methods | Keep facade delegates; add tests only at the new interface | Import/method census |
| Review fatigue from huge diffs | One move per PR, no logic edits, `.git-blame-ignore-revs` | Diff size ratchet |
| Merge conflicts in `app.ts`/`agent.py` | Single-writer rule; short-lived worktrees | Daily rebase |
| Performance regression | Keep assembly/latency benchmarks; compare before/after | DevTools perf gates |
| Behavior change smuggled into a move | Reviewer checklist; separate interface task | Golden diff |

---

## 11. Per-task checklist

Every task PR states:

- **Scope**: files/modules moved or introduced.
- **Interface**: signature, invariants, error modes, ordering.
- **Non-goals**: explicitly no behavior change.
- **Proof**: golden traces, targeted tests, full local suite, DevTools suite.
- **Ratchet**: file/method/cycle delta.
- **Rollback**: single revert.
- **Reviewer focus**: the diff should be mechanically explainable by the move.

## 12. Definition of done

- `agent.py` ≤ 700; `app.ts` contains no duplicate controller implementations (no line target); `_react_loop` ≤ 150; no method > 80 lines in
  the touched files.
- Golden traces for 12 turn scenarios and the UI screen/key matrix pass.
- Local suite and the pinned DevTools suite pass.
- No new import cycles; no public method deleted while callers exist.
- `docs/adr/0004` and README Appendix I reflect the new module map.
- A revert of any single task leaves a green tree.
