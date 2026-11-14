# Connected-model routing and parallel delegation

Machina does not assume one global model ranking. It builds a role-specific
inventory from the providers you have actually connected, then chooses routes
deterministically. No model call is made to choose a model.

## Inspect

```text
machina routes
machina routes --json
machina route "Fix the parser and run its tests" --mode multi_model --workers 4
machina route "..." --json
```

Inside chat, use `route_explain` for one prompt and `model_inventory` for the
connected catalog. The terminal UI also exposes `/routing <prompt>`.

## Modes

`Multi Auto` is the default and is now a first-class choice in `/models`
(alias `/model`), listed after the concrete model rows. Selecting it sets
`multi_model` + `model_selection=auto` + automatic explicit-parallel planning.
The currently selected model remains the session/planner anchor; delegated and
parallel work is routed automatically. `/routing <prompt>` is only the
explanation surface, not the switch.

- `multi_model` selects a planner, a supervisor, and a bounded worker pool.
  On high-risk work, the supervisor prefers a different model family from the
  planner.
- `single_model` copies the effective session route to every role and preserves
  explicit provider/model choices. Set `single_model_auto_parallel=true` to let
  explicit parallel prompts fan out with that same route.

Set them with the normal config path:

```text
machina config-set orchestration_mode multi_model
machina config-set model_selection auto        # or pinned
machina config-set worker_count 20             # 0=auto, 64=max
machina config-set max_worker_routes 4
machina config-set max_delegate_concurrency 8  # active workers, max 64
machina config-set cost_tier balanced          # low|balanced|high
```

`model_selection=pinned` uses the parent/session route for every worker and
does not auto-rewrite it. A definition-level provider/model pin always wins.

## Deterministic parallel work

When the user explicitly asks for parallel or independent tracks **and**
`orchestration_mode=multi_model`, Machina plans once, validates the JSON plan,
and executes the first dependency wave concurrently. The model does not get to
decide whether to fan out by emitting or omitting `delegate` calls.

Plan validation rejects:

- fewer than two tracks for a parallel request
- duplicate task IDs
- cycles and unknown dependencies
- duplicate artifact ownership
- more than the configured task cap (64 hard cap)

Workers reuse at most `max_worker_routes` distinct routes so 20 workers do not
invalidate every cache prefix. `worker_count=20` is supported; active
concurrency remains bounded by `max_delegate_concurrency`. After the workers
finish, the selected supervisor route (when multi-model and native/proxy) does
one bounded synthesis of their reports; if it fails, the deterministic report
summary is returned instead.

Example:

```text
machina --mode workspace chat --provider deepseek --model deepseek-v4-pro \
  "Create the frontend page and the API tests in parallel; use workspace-relative paths."
```

## Transports

- `worker_transport=native` runs child sessions in-process.
- `worker_transport=proxy` uses the OpenAI-compatible route when the selected
  inventory entry is proxy-backed.
- `worker_transport=mcp` runs each planned task through an installed external
  harness (OpenCode, Codex, or Hermes) using `interop.run_task`. This requires
  the harness to be installed; use `machina harnesses status` and
  `harness_features` to inspect it.

## OpenCode free models

Direct OpenCode Zen requests for free models are rejected outside OpenCode.
Machina's `opencode_session` provider uses OpenCode's local v2 session API
(`session create` → `prompt` → `messages`) instead:

```text
machina chat --provider opencode_session --model opencode/big-pickle "Reply OK"
```

The bridge is text-only. It ignores Machina tool schemas and reports that fact;
the route inventory marks it `tools=False`, so it can plan but never replace a
tool-using worker or supervisor. For local clients that need an OpenAI-shaped
endpoint:

```text
machina opencode-proxy --port 8765
```

Then point an OpenAI-compatible client at `http://127.0.0.1:8765/v1`.

## Verification and cost

- `browser_verify` runs real headless Chrome/Edge checks against a loopback URL.
- `web_verify` starts a temporary static server for an artifact directory and
  runs the same checks. Successful browser evidence counts for web-build
  verification gating.
- `browser_verification=off` disables the automatic web-build nudge.
- `machina usage` separates cache capability from cache counters. Kiro is
  `unsupported` (not reported); OpenCode session reports real cache read/write
  categories. Estimated route cost is visible in `machina route`.
- Explicit parallel requests avoid the old failure mode where one model
  serialized or hand-rolled the fan-out; the scheduler records each child
  session, route, token count, and approval state.
