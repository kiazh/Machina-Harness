# Golden turn traces (R0.2)

`tests/test_golden_turns.py` drives the real
`machina.core.react_loop.react_loop` through twelve scripted scenarios and
compares the normalized trace with the JSON in `turns/`.

## What a trace records

Per turn:

- `events`: ordered loop events with a sequence index. Text is a length and
  SHA-256; tool arguments, results, and approval payloads are hashes.
- `terminal`: outcome, content length/hash, token/iteration counters, tool
  names in provider order, and normalized approval request ids.
- `provider_attempts`: each scripted provider attempt (kind, ok/error/hang,
  HTTP status, message and tool counts) so retries are visible.
- `gates`: each request-gate decision input (message/tool/total-token/counter
  counts).
- `tool_batches`: requested, completion, and recorded tool order.
- `flushes`: pending-chunk persistence order, with chunk types and payload
  hashes only.
- `markers`: the cancellation marker written after the interrupted flush.
- `audits`, `token_metrics`, `condense`, `snapshots`, `completions`,
  `consolidations`, `learning_reviews`.

No raw prompt/completion text, UUID, timestamp, or path is stored.

## Scenarios

| Golden | Shape |
|---|---|
| `plain-answer` | one streamed answer, no tools |
| `one-tool` | tool call, batch, answer |
| `concurrent-tools` | three calls; completions out of request order, records in provider order |
| `approval-pause-resume` | paused batch terminal, then a resumed turn |
| `cancellation-mid-stream` | text delta, task cancellation, cancellation-safe flush + marker |
| `retry-502` | first stream attempt fails 502, the real `LLMCaller` retries once |
| `auth-401` | non-transient failure, actionable failed terminal |
| `empty-response-recovery` | empty post-tool response, empty tool-free continuation, recovered summary |
| `repair-edit-verification` | write, verify, finalize nudge, forced tool-free answer |
| `auto-parallel` | harness-owned synthesis, no provider call |
| `delegation` | delegated child result contributes `delegated_tokens` |
| `budget-exceeded` | repair token budget stop before the next model call |

## Stubs and deviations

The loop is the unit under test; only infrastructure outside it is replaced:

- **Context preparation** returns a fixed system+user message list and queues
  one user chunk, so the loop's first persistence flush is exercised.
- **Workspace snapshots** record `begin`/`end` only.
- **Request gate** returns a fixed budget with no refusal and records its
  inputs.
- **Tool batch** is a scripted stand-in. It reproduces start order,
  as-ready completion events, provider-order records, approval events, and
  delegated tokens, but it does not execute registry tools and does not add
  tool chunks to the pending queue. Tool-result persistence is therefore the
  one loop-adjacent behavior this trace does not cover.
- **Usage ledger / session activity / accepted attribution** are no-ops;
  `LLMCaller` retry and `TurnPhases` answer logic still run for real.
- **Approval resume** is modeled as a second turn: approval rehydration lives
  in the gateway, not in `react_loop`.
- **Auto-parallel** is replaced by a scripted synthesis because planning and
  child agents are outside the loop.

## Updating

```bash
./.venv/bin/python -m pytest tests/test_golden_turns.py --update-golden
```

Only regenerate after a reviewed behavior change: the diff is the evidence.
Without the flag a mismatch fails with a unified diff. The suite includes a
stability check (every trace recorded twice must be byte-identical) and a
reorder check (swapping two events must fail).
