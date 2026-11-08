# Query-conditioned memory digest

Machina's memory path used to retrieve the top five rows and send each row to
the model. That is fast, but it can spend the memory budget on near-duplicates
and miss a second fact needed for a multi-fact question.

The digest path is inspired by Memorilla's query-conditioned compression, with
one deliberate difference: Machina compresses **inspectable text claims**, not
trained latent vectors. That keeps provenance, correction, redaction, and
forgetting intact, and it works without a GPU or an extra provider call.

## Contract

1. Retrieve a wider candidate set than the normal top-k (default 16).
2. Collapse exact duplicate contents.
3. Score relevance from retrieval score + importance + query overlap +
   category support.
4. Select complementary facts with maximal marginal relevance (MMR), so a
   second useful fact can beat a near-duplicate of the first.
5. Render a bounded digest with a compact category marker and explicit
   omission count.
6. Keep the selected memory objects and digest character count internal for
   audit and scoring; the prompt sees only the compact text.
7. Audit `memory_digest` with counts, never content.

The selector is deterministic and has no model, database, network, or
filesystem access.

## Integration

`MemoryRetriever.retrieve_digest()` performs the wider retrieval and returns
`MemoryDigest`. `BaseReActAgent._prepare_context` uses it when
`memory_digest_enabled` is true and the retriever exposes the method; otherwise
it falls back to the original per-memory top-k path. A digest failure also
falls back and is reported through `record_failure`.

The digest is one `memory` context chunk, so existing projection, prompting,
compaction, and redaction paths apply unchanged.

## Configuration

| Key | Default | Meaning |
| --- | ---: | --- |
| `memory_digest_enabled` | `true` | Use the digest path; false restores per-memory top-k |
| `memory_digest_max_chars` | `1200` | Hard text bound for the rendered digest |
| `memory_digest_max_facts` | `6` | Maximum selected memories |
| `memory_digest_candidates` | `16` | Candidate over-fetch before selection |

All values are validated. `MACHINA_MEMORY_DIGEST_ENABLED`,
`MACHINA_MEMORY_DIGEST_MAX_CHARS`, and the other normal config env overrides
work through the standard config layer.

## Benchmark

`python tools/bench_memory_digest.py` runs 9 deterministic scenarios: 6
ordinary retrieval cases and 3 duplicate-heavy multi-fact cases. It compares
the pre-digest top-5 behavior with the digest's wider candidate selection.

Observed on the development machine:

```text
9 scenarios
overall coverage:       0.816 -> 0.915  (+9.9 points)
hard multi-fact:        0.532 -> 0.746  (+21.4 points)
hard multi-fact chars:  ~196 -> ~362 per scenario (bounded by 900)
average digest cost:    ~0.24 ms
redundancy:             0.142 -> 0.126
provider calls/tokens:  0 / 0
```

The digest deliberately spends characters on provenance and can select more
facts than the old top-5 path, so it is not a token-reduction claim. It is a
coverage-under-budget claim: the output is hard-bounded, and the selected fact
count and omissions are always visible. On ordinary cases where top-5 already
covered every required fact, the digest still collapses duplicates and has a
small metadata overhead.

## Limits

- Text digest selection cannot recover a fact that retrieval never returned;
  the wider candidate set only widens the recall envelope.
- Lexical category/query support is a tie-breaker, not a substitute for a
  trained semantic encoder.
- The digest is not a replacement for Memorilla's latent-token compression.
  It is the safe local baseline that a future optional encoder can be compared
  against.
- No raw memory content or source IDs are logged; only counts and the
  prompt-visible digest are retained for the request.

## Verification

```sh
pytest tests/test_memory_digest.py -q
python tools/bench_memory_digest.py
ruff check machina/memory/digest.py machina/memory/retriever.py
```
