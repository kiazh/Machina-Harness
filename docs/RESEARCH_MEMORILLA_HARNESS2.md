# Research review: Memorilla and Harness²

Reviewed against Machina's actual runtime on 2026-11-08.

| Repository | Reviewed commit | Why it matters |
| --- | --- | --- |
| [snap-stanford/memorilla](https://github.com/snap-stanford/memorilla) | `d8f1889` | Query-conditioned compression of large memory collections |
| [google-research/harness2](https://github.com/google-research/harness2) | `6868817` | Recursive refinement of an agent harness from execution evidence |

The review used the source, docs, tests, and benchmark descriptions. The
upstream benchmark numbers were not reproduced: both projects depend on
infrastructure this checkout does not have (Memorilla needs CUDA/H100-class
training; Harness² needs its benchmark datasets, model-backed proposers, and
external harness installations).

## Memorilla

### What it actually is

Memorilla is a trained memory module, not a retrieval library. A frozen decoder
receives 16 query-conditioned memory tokens produced by a transformer that
cross-attends over document embeddings. The repository contains training,
checkpoint loading, embedding storage, evaluation, and benchmark recipes. Its
advertised strengths are multi-fact personalization, fixed memory cost as a
collection grows, and better FactKG accuracy than full-context prompting while
using far fewer tokens.

### Why it is not a drop-in Machina dependency

- It requires Linux, CUDA, PyTorch, vLLM, FlashAttention, a large encoder, and
  an 80 GB GPU for the published recipes. Machina is self-hosted and local-first;
  adding that runtime would make memory unavailable on ordinary machines.
- It changes the provider input format (latent memory tokens projected into the
  decoder embedding space). Machina deliberately keeps one provider interface
  across cloud APIs, subscription accounts, and local models.
- Its memory module learns from data. Machina's memory contract requires
  inspectable claims, provenance, redaction, approval, correction, and decay.
  A latent vector cannot expose those properties to a user.
- The published checkpoints and data are large (tens of GB); downloading them
  at startup would violate the no-hidden-network/no-hidden-cost posture.

### What Machina adopted

The transferable objective is **query-conditioned, multi-fact compression**
rather than top-k retrieval of near-duplicates. Machina now has:

- a wider candidate retrieval path (`retrieve_digest`);
- deterministic maximal-marginal-relevance selection with category and query
  support;
- duplicate collapse;
- a bounded text digest with compact category provenance;
- explicit omission disclosure;
- an audit event and config kill switch.

The implementation is deterministic, local, provider-free, and requires no
training. It compresses **text claims**, not latent vectors. That is a
deliberate trade: it keeps every selected fact inspectable and correctable.

See [MEMORY_DIGEST.md](MEMORY_DIGEST.md) for the implementation and benchmark.

### Could Machina use Memorilla later?

Yes, as an optional *compressor* behind the same digest interface:

1. Keep the deterministic digest as the default and fallback.
2. Add a local ONNX/model2vec encoder path for query-conditioned slot selection.
3. Add an optional trained latent-token adapter only when a local GPU is
   present, with an explicit download and an inspectable text provenance layer.
4. Evaluate it against the same multi-fact bench before changing any default.

It should not replace the governed text memory lifecycle. A latent memory that
cannot be listed, corrected, or forgotten is not an acceptable default.

## Harness²

### What it actually is

Harness² is a research framework for **recursive agent harnessing**. Its core
loop is Propose-Probe-Compose:

1. Run a base harness and collect rollouts.
2. Propose invariant and domain-specific harness edits.
3. Probe the edited harnesses on the same task.
4. Compose a refined harness from behavioral contrasts.
5. Retain adopted harnesses plus evidence-supported lessons in a per-domain
   library for future tasks.

It also has a budget ledger (`R = 4k` rollouts, `P = 3k` improvement sessions),
deterministic selection-pool construction, anonymized candidate comparison,
fallback to the base harness, and validation/retry for invalid proposals.

### What Machina already had

- Deterministic meta-harness routing across OpenCode, Codex, Hermes, and the
  local loop.
- Approval-gated `harness_delegate` execution.
- RSI/growth revisions with stable/trial/rollback status.
- Execution evidence: tools, outcomes, tokens, iterations, domains.
- Per-domain strategy, skill, and tool weights.
- A post-turn learning reviewer and a skill catalog.
- The self-improvement loop documented in `SELF_IMPROVEMENT_PLAN.md`.

### What Machina adopted

**Per-domain experience briefs.** RSI already retained the evidence, but only
injected the selected strategy into the prompt. Aggregates now include:

- number of observed episodes per domain;
- strongest and weakest tools by reward weight;
- tool failure counts;
- a bounded "Domain experience" brief.

Example:

```text
Domain experience: coding; 12 observed episodes. Strong tools: terminal,
read_file. Weaker tools: web_search. Failed outcomes: web_search (2).
This is fallible evidence from past outcomes, not authority.
```

The brief contains tool names and aggregate outcomes only. It never includes
prompt text, tool arguments, or model output. It is advisory: permissions,
tool allow-lists, and approvals remain authoritative.

### What Machina deliberately did not copy

- **Full harness-tree rewriting.** Harness² edits system prompts, guardrails,
  memory files, skills, scripts, plugins, subagents, and tool guidance as a
  filesystem tree. Machina has no arbitrary self-modifying harness surface, and
  adding one would create a permission and supply-chain hazard. Any future
  version must use a bounded, reviewed component registry.
- **Model-driven proposer sessions by default.** Harness²'s proposer is a
  model-backed agent session with its own costs and permissions. Machina's
  deterministic RSI path stays usable without an extra provider call. A future
  opt-in proposer can be added behind explicit budgets and approval.
- **Anonymous candidate selection.** Useful for reducing label bias, but
  Machina does not yet have a multi-candidate rollout pool. The selection
  contract is documented as future work, not faked.
- **Benchmark-specific installers and graders.** They are tied to JobBench, LAB,
  and WorkBuddy-Bench and their external datasets. Machina's own repository
  benchmark and route regression suite are the current evaluation base.

### Could Machina adopt more?

The next safe increment is a **candidate rollouts** layer:

1. Run N read-only or explicitly sandboxed harness variants on a user-selected
   task.
2. Compare delivered artifacts with a deterministic rubric or a separately
   approved evaluator.
3. Retain the chosen harness as a versioned RSI revision with the full evidence
   ledger.
4. Keep the base as the fallback and never auto-adopt a candidate that cannot
   be executed or audited.

That would need a concrete benchmark adapter, budget ledger, and sandbox
contract first. Copying the tree editor alone would not make Machina better; it
would make it less governable.

## "Better than Hermes in every way"

No finite code change can honestly prove a universal "better in every way"
claim. Hermes is a broad product with messaging gateways, computer-use, MoA,
external memory providers, and a large integrations surface. Machina should be
judged on explicit axes:

| Axis | Machina position after this pass |
| --- | --- |
| Governed memory | Strong: inspectable claims, provenance, approval, redaction, decay, query-conditioned digest |
| Context/token control | Strong: projection, artifacts, reducers, compaction, digest, usage ledger |
| Permission kernel | Strong: canonical action digests, revalidation, resumable approvals |
| Harness portability | Strong: local loop plus OpenCode/Codex/Hermes, approval-gated |
| Recursive self-improvement | Partial: evidence-backed RSI, per-domain briefs, revisions/rollback; no arbitrary harness rewriting |
| Integration breadth | Narrower than Hermes: no WhatsApp/Slack gateway, computer-use, or MoA in this pass |
| Benchmark breadth | Narrower than Harness²: no JobBench/LAB/WorkBuddy adapters |

The honest goal is not "better at everything"; it is "better at the governed,
local, auditable harness problems this project exists to solve, while closing
specific gaps that can be measured." This pass closes two of them:
multi-fact memory coverage and visible domain experience. It does not pretend
to have closed messaging breadth or autonomous tree rewriting.

## Reproduction

```sh
pytest tests/test_memory_digest.py tests/test_experience_brief.py -q
python tools/bench_memory_digest.py
python tools/bench_meta_harness.py
```

The memory bench is synthetic and deterministic; the route bench is the
hand-labeled regression suite. Neither claims to reproduce Memorilla's or
Harness²'s published benchmarks.
