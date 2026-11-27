# UI controller integration checklist

Status: pending. All controllers are extracted as standalone modules; this is
the single-writer pass that wires them into `ui/src/app.ts` and deletes the
originals.

## Controllers already wired

- `ui/src/app/status.ts` — `StatusController` (U3.1)
- `ui/src/app/changes.ts` — `ChangesController` (U3.2)

## Standalone modules to wire

| Module | Class | Host interface | Original methods to delete after wiring |
|---|---|---|---|
| `ui/src/app/approvals.ts` | `ApprovalController` | `ApprovalHost` | approval/question prompt + dock + resolve/invalidate/clear/restore/rehydrate |
| `ui/src/app/sessions.ts` | `SessionController` | `SessionHost` | session create/switch/update/prune, family, steering, tail replay |
| `ui/src/app/keyboard.ts` | `KeyboardRouter` | `KeyboardHost` | `onInput`, `onKey`, `handleWorkspaceKey`, `matchesExplorerKey`, `normalizeModifierArrow` |
| `ui/src/app/commands.ts` | `CommandController` | `CommandHost` | `submit`, `commandActions`, `commandAction`, skills picker/search/catalog/details/preview/section |
| `ui/src/app/events.ts` | `EventRouter` | `EventHost` | `onEvent` (keep `onChildEvent` until sessions owns it) |
| `ui/src/app/workspace.ts` | `WorkspaceCoordinator` | `WorkspaceHost` | workspace host/focus/completion/save/problem/quick-open methods |
| `ui/src/app/bootstrap.ts` | `AppBootstrap` | `BootstrapHost` | `start`, `exit`, `reviewDirtyExit`, `onGatewayExit`, now-playing start/sync |

## Wiring rules

1. Construct each controller once in the `App` constructor, after the fields
   its host reads exist.
2. Pass a host adapter, not `this`:
   - Prefer an object literal with getters for read-only state and arrow
     functions for calls.
   - `as unknown as XHost` is allowed only at the adapter boundary, with a
     comment, when a private field cannot be exposed without widening the
     class. No `any`.
3. Keep every public `App` method used by components/tests as a thin delegate
   until after the full suite is green. Then delete delegates with no callers.
4. Delete the original method body in the same commit as the delegate, never
   keep two implementations.
5. Remove now-unused imports, fields, and private helpers. `tsconfig` has
   `noUnusedLocals`/`noUnusedParameters`; TypeScript will flag them.
6. Update `dispose`/exit paths to call `keyboard.dispose()`, stop controller
   timers, and clear controller state in the same order as before.
7. Move shared state deliberately:
   - `pendingKeyLeader*`, `lastEscAt`, explorer burst counters → keyboard.
   - image draft/epoch/`lastPrompt*` remain App-owned and are host accessors.
   - `pendingQuestions` map stays App-owned until approvals/sessions prove
     they can own it; expose only the operations.
   - `skillsMenu` state becomes command-owned; remove the App field.
8. Re-export moved pure helpers from `app.ts` (e.g. `commandCarriesSecret`,
   `isKiroQuotaError`) until tests import the new module directly.

## Verification (must all pass)

There is no line-count target for `app.ts`. The goal is that it contains only
composition, host adapters, compatibility facades, and state that genuinely
belongs to the root. Cohesive behavior belongs in a controller.

```sh
npm --prefix ui run typecheck
npm --prefix ui run lint
npm --prefix ui test
npm --prefix ui run check
./.venv/bin/python -m pytest tests -q -p no:cacheprovider
python tools/architecture_report.py --check
```

Golden requirements:

- `ui/test/golden-ui.test.ts` frames and key traces unchanged.
- `tests/test_golden_turns.py` unchanged (no Python edits in this pass).
- Full UI count at least the pre-integration count; no new skips.

## Dead-code cleanup (separate commit)

Run `python tools/dead_code_report.py` before and after. Delete only
zero-reference private members whose call sites are also gone; keep public
facades, extension points, and hooks the external DevTools suite may call.
