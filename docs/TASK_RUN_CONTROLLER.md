# TaskRun controller

`machina/core/task_run.py` is the deterministic controller for repository-repair
turns. It does not replace the model's plan or the tool loop. It keeps the
run bounded and records whether the required repair behavior actually happened.

## State

The controller tracks:

- **phase**: `orient`, `reproduce`, `edit`, or `verify`
- turns and tool calls
- whether a source edit was observed
- whether a verification command was run
- whether verification succeeded
- repeated failed actions by tool, arguments, exit code, and result preview
- repeated successful actions by tool and exact arguments
- a directory-exploration budget for terminal listing calls and `list_files`
- a session-recall budget for `session_recall` / `session_recall_window`
- a one-shot no-edit nudge
- a lean repair tool schema: file, terminal, code-intelligence, and artifact
  tools only; web research, memory management, delegation, and skill
  installation are not advertised on every repair request. Set
  `MACHINA_REPAIR_LEAN_TOOLS=0` to restore the full tool catalog when a repair
  task genuinely needs those tools.

Phase is derived from evidence, not from model prose:

- verification passed -> `verify`
- source edited -> `edit`
- verification attempted -> `reproduce`
- otherwise -> `orient`

## Budgets and stop conditions

| Control | Default | Applies to | Effect |
|---|---:|---|---|
| `MACHINA_TASK_RUN_MAX_TURNS` | `60` | repair turns | Stop before another model request |
| `MACHINA_TASK_RUN_MAX_TOOL_CALLS` | `150` | repair turns | Stop before another model request |
| Repeated failed action | 3 times | repair turns | Stop and require a changed approach |
| Repeated successful action | 3 times | every turn | Stop when the same tool + exact args repeat without an edit |
| Directory exploration | 10 calls | every turn | Stop repeated terminal listing / `list_files` without reading or editing |
| Session recall | 12 calls | every turn | Stop a past-conversation recall chain |

`0` disables the corresponding turn/tool limit. Repair-only controls still
apply only to prompts with explicit repair intent. The repeated-success,
exploration and recall guards apply to ordinary chat too, because a successful
listing or recall loop can burn a full turn without a repair prompt. Reopening
the same `read_file` target and status polling are exempt from the exact-action
guard; `list_files` is exempt from exact repeats but counted by the exploration
budget. Identical `search_files` calls are guarded.

An observed edit resets the exact-action counter, so edit/verify retries remain
allowed. A loop stop returns a typed failure response and never claims success.

The controller stops with a typed response and preserves the working tree for
the verifier. It never edits tests or fabricates success.

## No-edit nudge

After ten completed tool batches without a source edit, the model receives one
short continuation:

> Stop widening the search; use the failure evidence you already have, make the
> smallest source-only change, and run the narrowest relevant verification.

The nudge is emitted at most once per turn. An observed edit suppresses it.

## Process-loop nudges

The controller also emits one bounded nudge for three other observed loops:

- the same retained artifact is paged six times;
- a background job is polled six times;
- a source edit and successful targeted verification are already present.

The finalize nudge is important for token and latency efficiency: it stops the
model from expanding into unrelated tests or cleanup after the repair is
already verified. A successful direct behavior check (`python` with
`assert`/`callequal` and no failure) counts as verification evidence.

## Shell availability

On Windows, POSIX shell mode adds Git's `usr/bin` directory to the child
`PATH`, and the terminal preflight checks that child path rather than the
host process path. Without this, Git Bash launches but `grep`, `sed`, and
`awk` are rejected even though they are installed.

## Relationship to other gates

The controller is one layer:

1. wall-clock deadline (`MACHINA_REPAIR_DEADLINE_SECONDS`)
2. cumulative token budget (`MACHINA_REPAIR_TOKEN_BUDGET`)
3. TaskRun turn/tool/repeat limits
4. no-change repair continuation
5. verification-evidence continuation
6. external task verifier

All layers preserve the patch and report a failure reason rather than silently
grading an incomplete repair.

## Tests

`tests/test_task_run.py` covers inactive chat turns, phase transitions, repeated
failures, repeated successful terminal actions, cycling directory listings,
the recall budget, unbounded read-only file exploration, edit resets,
independent caps, the no-edit/artifact/job/finalize nudges, the lean repair
tool schema, environment configuration, and the integrated agent stop path. `tests/test_repair_gate.py` covers direct behavior-check evidence, and
`tests/test_swe_repair_protocol.py` covers the Git Bash child path.
