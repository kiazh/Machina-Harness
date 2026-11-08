# Meta-harness routing

Machina now has a deterministic per-prompt router for the external agent
harnesses it already knows how to run: OpenCode, Codex, and Hermes. The
router is local-only. It does not call a provider, spawn a harness, or scan
the workspace.

The route is advisory. A confident external route adds one compact hint to
the turn prompt. Actual execution is the approval-gated `harness_delegate`
tool. Tool effects remain subject to the normal permission broker.

## Feasibility verdict

Real-time prompt routing is feasible without LLM classification. The existing
`TaskDescriptor` already extracts task class, task family, capabilities, and
stack facts with regex and word-set rules. The router adds:

- a fixed capability card per harness,
- local hashed vector embeddings with cosine similarity,
- lexical keyword overlap,
- task-family and capability agreement,
- a small confidence threshold and deterministic tie-break.

The result is computed once per user turn in the same preparation seam that
already builds the task descriptor and skills context. There is no extra
model call and no background process.

Fully automatic external execution is not enabled by default. Running another
agent harness is a delegated execution with its own permissions and side
effects. The router can select it, but the `harness_delegate` tool must pass
through the existing approval path. That preserves Machina's authority model
and avoids hidden work.

## Components

| Piece | Location | Role |
| --- | --- | --- |
| Capability cards | `machina/core/meta_harness.py` | Names, families, classes, capabilities, strengths, keywords, spawn-free availability |
| Local matcher | `machina/core/meta_harness.py` | TaskDescriptor + hashed vectors + lexical overlap + family/capability scoring |
| Turn hook | `BaseReActAgent._prepare_context` | Computes one route per prompt and stores `_last_meta_route`; adds the advisory hint when enabled |
| Execution tool | `machina/tools/meta_harness.py` | Routes when `platform` is omitted, then runs `interop.run_task` under approval |
| Route preview | `meta.route`, `meta.harnesses` gateway RPCs | Call-free inspection for clients and tests |
| Prompt section | `build_system_prompt(..., meta_route=...)` | Appends the bounded hint after existing triage hints; `None` keeps the prompt byte-identical |

## Detection and feature extraction

Availability comes from `machina.interop.state_summary()`, which performs only
a persisted-state read and PATH lookups. It does not run version probes,
process-list scans, or MCP registration checks. Results are cached for 30
seconds.

Feature cards are deliberately data, not prompts. They use the same family and
capability vocabulary as `TaskDescriptor`, so adding a card cannot create a
second taxonomy. The matcher uses all of the following card facts:

- `task_families`: `web-build`, `coding`, `research`, `review`, `task`, `continuation`
- `task_classes`: the existing `machina.core.intent` classes
- `capabilities`: testing, security, UI, research, documentation, and so on
- `strengths`: human-readable best-feature summaries
- `keywords`: deterministic lexical anchors

The current cards are curated starter cards for the three supported headless
CLIs. The scalable path is to add adapters and cards together, then add an
optional cached `--help` probe for new adapters. A new adapter must still have
a literal prompt invocation and a bounded run path before it can be routed.

## Local embedding and scoring

The vector is a signed feature hash over:

- normalized words,
- character trigrams for morphological robustness,
- adjacent word bigrams.

It is deterministic, dependency-free, and local. The score combines the vector
cosine with keyword overlap and descriptor agreement:

```
score = 0.38 * vector + 0.22 * keyword + 0.18 * class + 0.14 * family + 0.08 * capability
```

The route stays internal unless an installed, run-capable external harness
clears the threshold and is separated from the next candidate. Candidate order
is stable: score first, then harness name.

If the prompt contains separable intents (`;`, `then`, `and then`, `and also`,
newlines), up to four clauses are routed independently. The result keeps the
step list and selects the highest-confidence external step as the primary.

## Prompt hint

When `meta_harness_enabled` and `meta_harness_hint` are true, an external route
adds a short, plain-text hint:

```text
Meta-harness route (local, advisory): research -> hermes (0.53); coding -> opencode (0.63). For a bounded second implementation or review, call harness_delegate(platform="opencode", task="<task>"). Approval and tool permissions still apply.
```

The hint is advisory text only. It does not force a tool call, change the tool
allow-list, change the execution mode, or bypass approval. Internal routes add
nothing, so prompts without a confident route remain byte-identical.

## Execution

The model-facing tool is `harness_delegate`:

```text
harness_delegate(task="...", platform="opencode", timeout=120)
```

Omit `platform` to use the local router. Omit `timeout` for the 120-second
default; valid values are 15 to 900 seconds. The tool runs the documented
headless command through `interop.run_task`, bounded and redacted, and returns
JSON with the command, exit code, stdout, stderr, and route metadata.

`harness_delegate` is forbidden for delegated child agents. Children cannot
recursively fan out into external harnesses.

## Configuration

```dotenv
# Turn the local route computation and prompt hint on/off.
MACHINA_META_HARNESS_ENABLED=true

# Keep routing but suppress the prompt hint (for example, for prompt caching).
MACHINA_META_HARNESS_HINT=true
```

The route preview RPCs are still callable when the turn hint is disabled.

## RPC inspection

```text
meta.route       {prompt}  -> {route}
meta.harnesses   {}        -> {harnesses}
```

Both are call-free. They do not use a provider, run a harness, or execute a
tool. They are useful for a future route inspector in the desktop or terminal
UI.

## Scalable path

1. Keep the deterministic baseline as the default. It has no model download,
   no network, and no per-turn LLM cost.
2. Add a harness adapter, a capability card, and a literal-run test together.
   Do not add a card for a harness that cannot be executed safely.
3. Optionally add a local embedding backend (model2vec or ONNX fastembed)
   behind the same `route_prompt` interface. Keep the hashed path as fallback.
4. Build a small labeled route set per repository and task family. Tune the
   weights and threshold against that set, not against anecdotal prompts.
5. Add outcome feedback later using the existing RSI/learning store. Do not
   make routing depend on the feedback before the evaluation set exists.
6. Add a route inspector to `/harnesses` or the desktop command palette using
   `meta.route` and `meta.harnesses`.

## Current limits

- Only OpenCode, Codex, and Hermes have run adapters today.
- Feature cards are curated, not scraped from live CLI help.
- External execution is approval-gated and not auto-started.
- The route hint consumes a small number of prompt tokens only when a
  confident external route exists.
- The matcher is deterministic by design; it does not learn from outcomes yet.

## Verification

```sh
pytest tests/test_meta_harness.py -q
ruff check machina/core/meta_harness.py machina/tools/meta_harness.py
```

The tests cover coding, research, multi-intent, no-harness, conversational,
determinism, registry availability, explicit and routed execution, and the
approval effect declaration.
