# Optimization and caching

Machina has four independently gated optimizations. None of them selects a
model or changes execution authority. A turn always uses the provider/model,
tools, sandbox, and approval path already resolved by the normal route.

| Subsystem | Config key | Default | What it does |
|---|---|---|---|
| Semantic response cache | `optimization_semantic_cache_mode` | `off` | Reuses a validated response for an equivalent read-only request within an exact scope |
| Prompt-cache breakpoints | `optimization_warmers_prompt_cache_mode` | `off` | Adds provider-supported cache markers to a stable prefix |
| Learned compression | `optimization_compression_mode` | `off` | Compresses eligible older assistant/tool text on complete and streaming provider calls |
| Startup warmers | `optimization_warmers_startup_enabled` | `true` | Loads local tokenizer/embedding/capability state off the request path |

Modes are `off`, `shadow`, and `wired`:

- **off** — no computation and no behavior change.
- **shadow** — compute the decision and record counts/reason codes, but send
  the original request and serve the live response.
- **wired** — the subsystem may act, but only after its promotion gate passes.

`optimization_emergency_disabled = true` forces every subsystem to `off`
without changing the selected model or authority.

## Semantic cache safety contract

A semantic lookup is allowed only for the read-only auxiliary allowlist
(`compression_summary`, `memory_rerank`, `skill_metadata`), with no tools, a
deterministic temperature (at most 0.3), no attachments/personalized memory,
and no detected secrets or PII. `memory_rerank` is invoked at temperature 0 so
it is eligible; `skill_metadata` requires the explicit cross-session
auxiliary setting because it has no session.

Further guards:

- the canonical request is at most 2,000 characters (the embedding adapter
  width) and contains no freshness words or timestamps;
- fenced-code/diff requests are not cached;
- the exact scope fingerprint matches: session, agent, workspace, provider,
  model, purpose/task, tool schema, and compressor revision;
- the exact embedding space matches.

The vector search runs only after those exact guards. Cache hits return a
response with zero usage and `cache_source="semantic"`. Tool calls, truncated
responses, empty responses, and responses containing detected secrets or PII
are never stored.

Session deletion invalidates that session's semantic entries. Expired rows are
vacuumed by the scheduler while the semantic cache is enabled.

The semantic cache is auxiliary-only. The interactive turn path keeps its
tool schemas and therefore does not qualify for the no-tools cache contract.

## Compression safety contract

Compression runs after deterministic projection/reducers on both provider
paths: `UsageStore.complete_call` for non-stream/auxiliary calls and
`agent._stream_llm_with_retry` for streaming turns. On complete calls it runs
after the semantic-cache miss. System messages, the current/recent turns, code
fences, diffs, URLs, paths, line references, structured tool output, and error
text are never candidates. Every compressed span must retain all extracted
anchors and, when wired, clear the embedding-similarity floor. Any timeout,
import failure, anchor loss, or similarity drift reverts that span to its
original text. The canonical history and artifact store are never mutated.

The optional `llmlingua2` backend is loaded lazily. If the package or model is
unavailable, compression reports `no_compressor` and the request is sent
unchanged.

## Prompt-cache warmers and economics

The provider prompt-cache profiles in `machina/core/provider/prompt_cache.py`
remain the source of truth. Unknown endpoints are treated as uncached. In
wired mode, `optimization_warmers_min_prefix_tokens` is enforced before
Anthropic/OpenRouter breakpoints are attached; Anthropic direct requests also
mark the last stable history turn so the next user request can reuse the
append-only prefix within the four-breakpoint budget. The legacy
`prompt_cache_enabled` pilot keeps its historical behavior. `prefix-plan`
estimates paid warming economics but does not send a synthetic request.
Startup warmers never make a provider call and are skipped for one-shot/test
processes.

## Observability

Shadow decisions write to `optimization_shadow_events`: subsystem, mode,
decision, reason codes, session/agent, provider/model, request hash, token
counts, similarity, and latency. Prompt and response bodies are never written
to that table.

## Provider support

| Provider | Prompt cache | Semantic cache | Local compression | KV pruning |
|---|---|---|---|---|
| Anthropic direct | explicit, 4 breakpoints | yes | yes | no |
| OpenRouter → Anthropic | explicit passthrough | yes | yes | no |
| OpenAI / ChatGPT / Copilot | implicit | yes | yes | no |
| DeepSeek | implicit | yes | yes | no |
| Google Gemini | implicit | yes | yes | no |
| Others | none verified | yes | yes | no |

Semantic cache and compression are model-agnostic; they depend on the local
embedding/compressor backend. With `embedding_backend=off`, semantic lookup
is a no-op (no vectors to compare). Prompt-cache TTL metadata is admitted only
when verified.

Semantic hits bypass the usage ledger by design: they create no reservation
and no billed row. Hit/miss counts live in `optimization_shadow_events`.
Exact provider-cache hits retain the existing zero-usage ledger row and are
labeled `cache_source="exact"`.

Inspect configuration and shadow counts:

```bash
machina optimization status
machina optimization status --report --days 7
machina optimization prefix-plan anthropic --model claude-sonnet-4 \
  --prefix-tokens 12000 --history-turns 3,4,5
```

## Rollout gates

1. Semantic cache in `shadow` for auxiliary/read-only calls.
2. Promote to `wired` only when shadow equivalence and scope-isolation checks
   pass.
3. Prompt-cache breakpoints only after measured positive net savings and the
   configured minimum prefix is met.
4. Compression only after anchor retention, semantic similarity, latency, and
   retry-rate gates pass in shadow.

Semantic-cache and compression failures open their own bounded circuit
breakers; startup warmer failures are isolated per warmer and never block the
gateway.

## Rollback

Set `optimization_emergency_disabled = true` (or return every mode to `off`).
This is a configuration-only rollback: it does not change the selected model,
provider, tools, or authority. Expired cache/telemetry rows can be removed by
the scheduler's maintenance pass or the documented SQL tables; prompt text and
full transcripts are not stored in either table (the semantic cache stores the
validated response text only).

## Out of scope

- Automatic task-based model switching/routing.
- KV-cache or attention pruning for API providers.
- Cross-tenant cache sharing.
- Caching mutating actions, approvals, or permission decisions.
- Synthetic provider calls/keepalive.
