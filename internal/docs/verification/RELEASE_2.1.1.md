# Machina 2.1.1

The dynamic-tool-loading and Hermes-delegation patch for 2.1.0.

## Fixed

- **`tool_load` now works on the next model step.** Tool calls execute in a
  child `asyncio` task; child tasks copy the context, so assigning a new
  `ContextVar` value inside the tool was invisible to the parent. The agent
  then denied the just-loaded tool as "not in the current tool surface".
  The loaded set is now a shared per-turn mutable value, so a child task's
  `tool_load` widens the parent's next `_tool_defs()` projection. A
  regression test executes `tool_load` through `asyncio.create_task`, the
  exact boundary that lost the state.
- **Hermes harness model retirement.** When `hermes --oneshot=...` exits
  non-zero and the provider names a replacement model
  (`... switch to 'stepfun/step-5-preview'`), `machina harnesses run hermes`
  retries once with `-m <model>`. The final result reports the argv actually
  used; a second failure includes both the retry and the original error.

## Verification

- Repository smoke suite: `709 passed, 18 skipped`.
- UI suite: `1,262 passed, 6 skipped`; typecheck and lint clean.
- External suite: `6,548 passed, 13 skipped, 2 xfailed` on 2.1.0; the 2.1.1
  deltas are covered by the repository regression tests and CI.

## Compatibility

- No database migration.
- No configuration change.
- `tool_load` behavior is now the documented behavior; no API surface change.
