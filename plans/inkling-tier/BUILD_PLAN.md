# Build plan: 1M-context Inkling 2 Mini decode on 10 GB VRAM / 16 GB RAM

Audience: the coding model (DeepSeek) that executes tasks, and the human reviewer.
Status: plan only. No implementation code. Interface sketches are illustrative.

Tags used throughout:
- **[V]** verified from a primary source (linked).
- **[C]** community claim (Hugging Face model cards of third-party quants), not verified.
- **[D]** derived arithmetic from [V] values.
- **[E]** estimate; must be measured before relying on it.

Units: VRAM/RAM budgets in GiB/MiB. Link speeds in GB/s (1e9) as vendors quote them. 22 GB/s = 20.5 GiB/s.

---

## 0. Reality check: corrected numbers

The model is real and its config is public. Several numbers in the brief are off, one of them by ~6x, and it changes the architecture.

Sources: [HF config.json](https://huggingface.co/thinkingmachines/Inkling-2-Mini/raw/main/config.json), [model card](https://thinkingmachines.ai/model-card/inkling-2-mini/), [vLLM recipe](https://recipes.vllm.ai/thinkingmachines/Inkling-2-Mini), [community GGUF README](https://huggingface.co/Abiray/Inkling-2-Mini-Uncensored-GGUF/blob/main/README.md).

### 0.1 Model facts

| Item | Brief said | Actual | Tag |
|---|---|---|---|
| Layers | 24 physical, 48 passes | 24 physical; layers 6–17 run 3 passes → 48 visits | [V] |
| Layers 0–5 / 18–23 | "dense" | **MoE too.** Every layer has 128 routed experts, top-8, 0 shared experts. They are non-looped, not dense. | [V] |
| Hidden size | – | 2304 | [V] |
| Attention heads | – | 24 Q heads, 8 KV heads, head_dim 128 (same for local and global) | [V] |
| Global layers | 8 of 48 passes | Physical globals are layers 5, 11, 17, 23. Visits {5, 11, 17, 23, 29, 35, 41, 47} are global (8), 40 are local. | [V]/[D] |
| SWA window | 1024 | 1024 | [V] |
| Positional scheme | (RoPE assumed by Quest) | **Not RoPE.** Relative-bias attention (`d_rel=16`, `rel_extent=1024`), log-length query scaling (`n_floor=8192`, `alpha=0.1`), q/k L2 clip at 22.63, learned sink, output gate, value norm. | [V] |
| Other per-pass state | – | 4 short convolutions per layer (kernel 4) with per-pass conv state; rank-64 gated cross-pass controller; block attention residuals every 6 layers | [V] |
| KV per pass | – | Each of the 48 visits keeps its own KV cache and conv state | [V] (vLLM recipe) |
| Expert FFN | – | intermediate 1152 → 3 × 2304 × 1152 = **7.96M params/expert** | [V]/[D] |
| Active params | 3.4B | 3.4B official; **5.4B effective** per token because of loops | [V] |
| Checkpoint format | 4-bit | ~20 GB, experts NVFP4 (layers 0–19) and MXFP8 (layers 20–23), attention/embeddings BF16. No official BF16 checkpoint. | [V] format, [C] per-layer split |
| Bundled drafter | – | DSpark speculator (3-layer MTP, block 8) in the checkpoint | [V] |
| Context | 1M | `model_max_length` 1,048,576 | [V] |
| llama.cpp support | – | Mainline since build b12600 (PR #32694), GGUF Q4_K_S = 14.97 GiB | [C] |

### 0.2 Memory arithmetic

| Quantity | Brief | Corrected | Tag |
|---|---|---|---|
| Expert size at 4.5 bpw (NVFP4: E2M1 + E4M3 scale per 16) | – | 4,478,976 B ≈ **4.27 MiB** (4 KiB-aligned: 4,481,024 B) | [D] |
| Expert uses per token | – | 48 visits × 8 = **384** | [D] |
| Expert bytes/token, no reuse | ~1.8 GB | 384 × 4.27 MiB = **1.60 GiB** (1.72 GB). Close to your number, but for a different reason (3.06B routed params touched per token). | [D] |
| All experts | – | 3072 experts. All NVFP4: **12.8 GiB**. Native (layers 20–23 MXFP8 at 8.25 bpw): **14.6 GiB** | [D]/[C] |
| Non-expert params | – | ~1.9B: embedding 0.46B, LM head 0.46B (tying unknown), attention 0.45B, MTP ~0.25B, vision ~0.3B | [D]/[E] |
| Text-only pack size | ~15.7 GB | experts 12.8 GiB + attention Q8_0 0.45 GiB + LM head Q8_0 0.45 GiB + embedding Q8_0 0.45 GiB ≈ **14.2 GiB** | [E] |
| KV per token per visit | – | 2 × 8 × 128 × 2 B = **4 KiB** FP16 | [D] |
| SWA KV (40 visits × 1024 tokens) | – | **160 MiB** FP16 | [D] |
| Global KV at 1M, FP16 | 5.3 GB | 8 × 1,048,576 × 4 KiB = **32 GiB** | [D] |
| Global KV at 1M, Q4_0 K+V | 1.65 GB | **9.0 GiB** (1152 B/token/visit) | [D] |
| Global KV at 1M, Q8_0 K + Q4_0 V | – | **13.0 GiB** | [D] |
| Global KV, Q4_0, at 128K / 256K / 512K | – | 1.1 / 2.25 / 4.5 GiB | [D] |

Your global-KV figures look like they assumed ~1 KV head of ~160 dims. With 8 KV heads × 128 the global KV is ~6x larger.

**Consequence:** at 1M, Q4 global KV (9 GiB) plus the RAM share of experts (~5.9 GiB) does not fit in 12.5 GiB RAM. Global KV in pinned RAM only works up to ~512K. Beyond that, global KV needs NVMe as its home tier, and RAM becomes a page cache. Experts should stay entirely in VRAM + RAM (§0.5 explains why that is the right way round).

### 0.3 Hardware numbers

| Item | Brief | Comment | Tag |
|---|---|---|---|
| GPU | 10 GB | Almost certainly RTX 3080 10GB (GA102, sm_86, ~760 GB/s, no FP8/FP4 tensor cores). NVFP4 must be dequantized in software (LUT). Cheap for batch-1 decode, costly for prefill. An AMD RX 6700 also has 10 GB; that would invalidate the CUDA plan. | [E] |
| PCIe 4.0 x16 | ~24 GB/s | Plausible for pinned H2D on x16. Pageable is far lower. x8 slot halves it. Measure in T04. | [E] |
| NVMe Gen4 | 7–14 GB/s | A single Gen4 x4 drive tops out at ~7.4 GB/s sequential. 14 GB/s needs Gen5 or RAID0. Random 64–128 KiB reads at QD32: expect 4–6.5 GB/s. | [E] |
| GPU FP16 tensor | – | ~59.5 TFLOPS with FP32 accumulate on GeForce Ampere (half rate), ~119 with FP16 accumulate | [E] |
| 4-bit lossless compression gain 3–10% | – | Agree. Drop it. | – |

### 0.4 Prefill cost (not in your targets, but it dominates experiment cost)

- Global attention: 6144 × L² FLOPs per global visit × 8 visits ≈ **5.4e16 FLOPs** at L = 1M. [D]
- MoE + projections: ~7.9 GFLOP/token × 1M ≈ **8.3e15 FLOPs**. [D]
- On a 3080 at realistic 30–38 TFLOPS: **~30–40 minutes per 1M prefill**. At 128K: **~1 minute**. [E]

Implications: most recall evaluation must happen at 32K–128K and offline from dumps. 1M evals must be few samples, ideally many queries against one prefilled haystack (KV snapshot, T46).

### 0.5 Back-of-envelope decode budget at 1M context

Assumptions: all experts NVFP4; VRAM expert cache 6.9 GiB (54% of experts; see §3.7); RAM holds the rest pinned; global KV Q4_0 in 72 KiB pages on NVMe with a 4.6 GiB RAM page cache; Quest budget as listed; serial sum (no overlap credit).

| Term | Pessimistic | Base | Optimistic |
|---|---|---|---|
| VRAM byte hit rate h (static + dynamic + loop reuse) | 0.45 | 0.55 | 0.70 |
| Expert miss bytes/token (1−h) × 1.60 GiB | 0.88 GiB | 0.72 GiB | 0.48 GiB |
| Effective miss bandwidth | 16 GiB/s | 19 GiB/s | 28 GiB/s (PCIe + CPU path) |
| Expert miss time | 55 ms | 38 ms | 17 ms |
| Quest budget (of 1M) | 3% | 1.6% | 1.6% |
| KV pages over PCIe (2096 pages × 72 KiB at 1.6%) | 277 MiB, 14 ms | 147 MiB, 7 ms | 3.5 ms (50% VRAM page-cache hit) |
| NVMe KV stalls (RAM page-cache misses) | 6 ms | 3 ms | 1 ms |
| GPU compute + launches (resident reads ~1.5 GiB + hits) | 10 ms | 8 ms | 6 ms |
| Sync and pipeline bubbles | 6 ms | 3 ms | 1 ms |
| **Total** | **91 ms (11 tok/s)** | **59 ms (17 tok/s)** | **28 ms (35 tok/s)** |

All [E]. Real systems usually land at 60–80% of such a model. So: 8–15 tok/s at 1M is plausible; 20+ needs the optimistic levers (loop reuse, CPU path). The two numbers that decide the project are **h** (measured in Phase 1 from routing traces) and the **Quest budget needed for recall** (measured in Phase 1 from attention dumps).

Where your risk list is right and where it isn't:
- Expert traffic does dominate per-token bytes (0.5–0.9 GiB vs ~0.15 GiB KV). Correct.
- KV *capacity* is the bigger structural problem: it forces NVMe for KV at >512K.
- Speculative decoding is probably **negative** here. Verifying n tokens loads the union of their experts. With independent routing over 128 experts top-8, the union for n=8 is ~51.6 experts (6.4x bytes) for maybe 3–4 accepted tokens. Measure the real union in Phase 1 (cheap, from traces); expect to drop it.
- Prefetch only fills idle link time (attention/router phases, ~5–7 ms/token). Ceiling ≈ 10–15% gain. Loop-reuse caching and the CPU path are bigger levers.

### 0.6 Verdicts on the five draft ideas

1. **Asymmetric KV tiering.** Keep SWA KV in VRAM (160 MiB). Global KV: pinned RAM up to ~512K; at 1M, NVMe is the home tier, RAM is an LRU page cache, VRAM holds a small hot cache, the sink page, and an exact FP16 near-window ring.
2. **Quest retrieval.** Keep, with changes: INT8 min/max boxes (~260 MiB at 1M); page selection shared across KV heads so each page is one 72 KiB I/O; near window (last 1024 tokens) always exact because the relative bias lives there [assumption A-REL, verify in T09]; treat the budget as a measured variable, affordable traffic-wise up to ~5%.
3. **Loop-aware prefetch.** Split it. (a) *Loop-reuse caching*: keep experts loaded at pass p for passes p+1, p+2 of the same physical layer. Potentially the largest cheap win; measure overlap first. (b) *Lookahead prefetch*: ≤10–15% ceiling; build only if probe precision is good.
4. **NVMe expert repacking.** Demote. Experts should not be read from NVMe during decode at all (12.8 GiB fits VRAM 6.9 + RAM 5.9). Still use a 4 KiB-aligned pack file for fast loading and prefill streaming.
5. **Tiered expert cache.** Two tiers (VRAM, RAM) are enough. Swap roles with KV: **NVMe holds KV, RAM holds experts.** Putting 33% of experts on NVMe instead would cost ~0.3–0.4 GiB/token of NVMe reads (60–80 ms), ~10x worse than NVMe KV misses.

Additions worth testing: zero-copy "read-through" GEMV for expert misses (GPU reads weights straight from pinned RAM over PCIe, optionally filling a VRAM slot in the same pass); a CPU expert path for cold experts (uses DRAM bandwidth in parallel with PCIe); keeping NVFP4 natively (lossless vs the release) instead of requantizing to Q4_K.


---

## 1. Repo and architecture

### 1.1 Base choice

| Option | Pros | Cons |
|---|---|---|
| Fork llama.cpp | Inkling numerics already implemented [C]; GGUF kernels; CPU MoE kernels; tokenizer | ggml-sched places tensors statically; no expert paging; KV cache class would need a rewrite for paged/Quest/NVMe; mmap-centric loading fights the RSS cap; fast upstream churn |
| Minimal custom C++/CUDA runtime | Owns every byte, stream, and transfer, which is the whole project; can keep NVFP4 native | Must re-implement ~15 model ops exactly; must write kernels |
| PyTorch harness only | Fastest iteration; good for reference and analysis | Python launch overhead at batch-1 over 48 visits; torch + CUDA context costs ~1–2 GiB RSS; dynamic pointers break CUDA graphs |
| vLLM | Official support | Targets Hopper/Blackwell; won't run 20 GB on a 10 GB card with this memory hierarchy |

**Decision:** three parts.
1. **`engine/`**: custom C++17/CUDA runtime for decode, prefill, and the memory hierarchy. Kernels may be vendored from llama.cpp (MIT) with notices.
2. **`lab/`**: Python. PyTorch layer-streamed reference (golden numerics), offline analysis, simulators, eval runners. Torch never runs inside the engine process.
3. **llama.cpp** (pinned submodule): baseline numbers, fast routing-trace and Q/K dump source via `cb_eval`, secondary oracle.

Fallback trigger: if the engine cannot reach numerical equivalence (Phase 3a gate) within 3 weeks, pivot to a llama.cpp fork limited to a custom `mul_mat_id` expert cache and a custom KV cache.

### 1.2 Directory tree

```
inkling-tier/
├── setup.sh                     # one command: deps, build, downloads, health check
├── CMakeLists.txt
├── pyproject.toml               # lab + tools (uv-managed venv)
├── configs/
│   ├── model/inkling2_mini.json # HF config.json verbatim
│   ├── hw/default.yaml          # budgets + measured link params
│   └── run/*.yaml               # policy configs per experiment
├── docs/
│   ├── model-spec.md            # exact math per op (T09)
│   ├── formats.md               # pack, trace, dump, page formats
│   ├── budgets.md               # VRAM/RAM per component
│   ├── measurements.md          # microbench + baseline results
│   └── adr/                     # decisions D1..D8
├── engine/
│   ├── include/it/              # public headers = contracts in §3
│   ├── src/core/                # config, visit schedule, arenas, budget, counters, logging
│   ├── src/io/                  # pack reader, io_uring NVMe, pinned pools
│   ├── src/model/               # forward driver, per-visit state, resident weights
│   ├── src/experts/             # ExpertStore, placement, dynamic rings, miss paths
│   ├── src/kv/                  # SwaRing, GlobalKvStore, page caches, quantizers
│   ├── src/retrieval/           # QuestIndex
│   ├── src/sched/               # StreamManager, TransferQueue, Decode/PrefillScheduler
│   ├── src/cpu/                 # NVFP4 AVX2 GEMV, worker pool
│   ├── kernels/                 # *.cu
│   ├── capi/                    # C ABI for Python ctypes clients
│   ├── tests/                   # googletest
│   └── bench/                   # microbenchmarks
├── tools/
│   ├── llama_trace/             # libllama trace/dump tool (C++)
│   └── pack/                    # safetensors -> pack converter (Python)
├── lab/
│   ├── reference/               # weights.py, model.py, ops.py, hooks.py
│   ├── analysis/                # routing, cache_sim, probe, quest_recall
│   ├── sim/                     # analytic.py, des/
│   ├── evals/                   # datasets, ruler, mrcr, ppl, engine client
│   └── tests/
├── scripts/                     # bench_*.sh, run_capped.sh, baseline_llamacpp.sh
├── third_party/                 # llama.cpp (submodule), googletest, nlohmann_json
└── results/                     # gitignored: CSVs, traces, dumps, reports
```

### 1.3 Runtime data placement (target design, subject to Phase 2 ADRs)

```
VRAM (9.5 GiB)                RAM (12.5 GiB)                 NVMe
- resident weights (attn,     - experts not in VRAM          - pack files (all weights)
  routers, norms, gates) Q8   - embedding Q8_0               - global KV pages (home),
- LM head Q8_0                - global KV page cache (LRU)     one file per global visit
- expert static hot set       - NVMe staging buffers         - KV/state snapshots
- expert dynamic rings          (all pinned, mapped)
- SWA rings FP16 (40 visits)
- global near-window rings FP16 (8)
- Quest boxes INT8
- KV VRAM page cache
- scratch
```

Decode visit pipeline (one visit; `v` = 0..47):
1. Pre-attention ops (norm, sconv, loop controller) on the compute stream.
2. Attention. Local: SWA ring. Global: Quest select → resolve pages (VRAM cache, pinned RAM via zero-copy, or NVMe miss → host stall) → sparse far attention + exact near-window + sink → LSE merge.
3. Router → top-8 ids written to device memory (and a mapped host mirror if the host path is used).
4. MoE: for each selected expert, look up the device slot table: VRAM hit → GEMV from VRAM; miss → zero-copy GEMV from pinned RAM with optional read-through fill into the layer's dynamic ring (D1), or CPU path (D3).
5. Weighted sum, residual, post-ops. Append KV to ring/tail page; seal page every 64 tokens (quantize, build box, write-behind to RAM/NVMe).


---

## 2. Phased milestones

Effort assumes one human reviewing and DeepSeek coding; a "session" is one task card (§4). Calendar estimates are [E].

### Phase 0: Environment, baselines, microbenchmarks, analytic model (~1 week)

- **Goal:** replace every [E] hardware number with a measurement; get an off-the-shelf baseline; build a closed-form performance model.
- **Deliverables:** `setup.sh` with health JSON; config + visit schedule; memory budget/arenas; PCIe, NVMe, host microbench CSVs; llama.cpp baseline CSV; `lab/sim/analytic.py`; `docs/measurements.md`.
- **Tasks:** T01–T08. **Depends:** none.
- **Acceptance (measured on target box):**
  - Pinned H2D ≥ 20 GB/s for transfers ≥ 1 MiB.
  - Zero-copy kernel read ≥ 17 GB/s with ≤ 34 SMs busy.
  - 2048 × 72 KiB scattered pinned→VRAM gather (any method) ≤ 9 ms.
  - NVMe 72 KiB random read QD32 ≥ 3.5 GB/s; 4.27 MiB reads ≥ 5.5 GB/s.
  - DRAM read ≥ 30 GB/s multi-threaded.
  - Mapped-flag GPU→host signal ≤ 15 µs.
  - llama.cpp baseline tok/s recorded at 4K and 32K under the 12.5 GiB cgroup.
  - Analytic model reproduces §0.5 within 5% using the plan's inputs, then is re-run with measured inputs.
- **Go/no-go:** If pinned H2D < 15 GB/s (likely x8 slot or chipset lanes) or the re-run analytic base case < 8 tok/s at 1M → stop and re-scope (256K–512K target, or lower-bit experts) before writing engine code.

### Phase 1: Instrumentation and offline analysis (~1.5–2 weeks)

- **Goal:** measure the two deciding numbers, expert hit rate (with loop reuse) and Quest recall vs budget, without building the engine.
- **Key trick:** teacher-forced prefill produces the same routing and the same queries as decode would on that text. Traces from prefill are decode traces.
- **Deliverables:** `docs/model-spec.md`; libllama trace/dump tool; PyTorch layer-streamed reference (≤ 8K tokens); datasets; routing report; cache simulator; probe report; Quest recall report.
- **Tasks:** T09–T16. **Depends:** T01–T03, T07 (llama.cpp built).
- **Acceptance:**
  - ≥ 200K tokens of routing traces across ≥ 4 domains (code, prose, chat/reasoning, long-doc QA).
  - ≥ 20K tokens of router-input dumps.
  - Attention dumps at 32K and 128K for ≥ 50 RULER/MRCR samples (K for all tokens, Q at answer positions).
  - Reports: per-layer popularity CDF; loop overlap o = E|S(ℓ,p) ∩ S(ℓ,p+1)|/8; temporal overlap t→t+1; union size for n = 2, 4, 8; cache curves at VRAM budgets 3–8 GiB; probe precision/recall; Quest far-mass recall curves.
- **Go/no-go gates:**
  - **G1 experts:** best policy at 6.9 GiB VRAM gives simulated miss bytes ≤ 0.9 GiB/token. If > 1.2 GiB → the base case drops below ~12 tok/s; continue only if the CPU path is in scope. If > 1.6 GiB → kill the 1M@8 tok/s target.
  - **G2 retrieval:** some config (page 32/64/128, shared vs per-head selection, KV format) captures ≥ 90% of far attention mass at the p10 query, with budget ≤ 3% at 128K, and the 32K→128K trend extrapolates to ≤ 5% at 1M. If the needed budget exceeds 8% at 1M → KV traffic rivals experts; re-scope or change index (per-head selection, smaller pages, CPU ANN index).
  - **G3 trace fidelity:** llama.cpp Q4_K_S top-8 sets vs the reference: mean Jaccard ≥ 0.8. If lower, generate traces with the reference only.
  - **G4 spec decode:** keep only if measured union(n=4)/(4×8) ≤ 0.5. Expected: drop.

### Phase 2: Trace-driven simulator decides what to build (~1 week)

- **Goal:** pick policies with numbers before writing the expensive parts.
- **Deliverables:** discrete-event simulator (DES) in `lab/sim/des/`; sweep report; ADRs:
  - D1 expert miss path (zero-copy read-through vs copy-engine into slots).
  - D2 placement (static popularity set + per-layer dynamic rings; ring size).
  - D3 CPU expert path in/out.
  - D4 lookahead prefetch in/out.
  - D5 KV format (Q4/Q4, K8/V4, KIVI-style per-channel-in-page K) and page size.
  - D6 selection mode (shared vs per-head) and budget.
  - D7 tier thresholds (RAM-only KV up to which length; VRAM page-cache size vs expert cache size).
  - D8 spec decode out (or in, if G4 passed).
- **Tasks:** T17–T18. **Depends:** T08, T13–T16.
- **Acceptance:** DES matches the analytic model within 5% on synthetic uniform traces; every ADR cites a sweep result.
- **Go/no-go:** predicted tok/s at 1M with p10 parameters ≥ 8 → proceed. 5–8 → proceed only to 512K target. < 5 → stop.

### Phase 3a: Engine forward pass, correct first (~2–3 weeks)

- **Goal:** the engine produces the same logits as the reference at short context, with naive expert placement.
- **Deliverables:** pack converter and reader; decode kernels (functional); forward driver with 48-visit schedule and all per-pass state; C API; equivalence harness.
- **Tasks:** T19–T26. **Depends:** T02, T03, T09, T11.
- **Acceptance (4K teacher-forced, same weights as reference, FP16 KV, dense attention):**
  - Per-visit hidden-state cosine ≥ 0.9995; relative L2 ≤ 1e-2.
  - Final logits: mean KL(ref‖engine) ≤ 0.005 nats; top-1 agreement ≥ 99.5%.
  - Peak VRAM ≤ 9.5 GiB, RSS ≤ 12.5 GiB under cgroup.
  - (Thresholds are starting values; tighten or loosen with evidence.)
- **Go/no-go:** not met after 3 weeks → fallback to llama.cpp fork (§1.1).

### Phase 3b: KV tiering and page retrieval at 128K (~2–3 weeks)

- **Goal:** long-context correctness with RAM-resident paged global KV and Quest selection at 128K.
- **Deliverables:** chunked prefill v1; prefill attention (far/near split, LSE merge); page quantizers; GlobalKvStore (RAM tier); QuestIndex; sparse decode attention; long-context eval runner.
- **Tasks:** T27–T33. **Depends:** Phase 3a; D5, D6.
- **Acceptance:**
  - 128K prefill completes under caps (time recorded; expect ~1–2 min).
  - KV quant vs FP16 KV at 32K: RULER average drop ≤ 1 point.
  - Sparse vs dense-paged (all pages, same quantized KV) at 128K: RULER average drop ≤ 2 points; MRCR 8-needle drop ≤ 3 points.
  - Quest select ≤ 0.1 ms/visit at 128K; sparse attention ≤ 0.2 ms/visit when pages are VRAM-cached.
- **Go/no-go:** drop > 3 points at budget ≤ 5% → revisit D5/D6 (per-head selection, smaller pages, K8). If still failing → retrieval is the blocker; stop the 1M track.

### Phase 4: Expert store, cache, scheduler (~2–3 weeks)

- **Goal:** hit the decode tok/s target at 128K with the real memory hierarchy.
- **Deliverables:** placement planner; ExpertStore with static set + dynamic rings; miss path per D1; StreamManager/TransferQueue; CPU path (if D3); prefetcher (if D4); decode scheduler with CUDA graphs and counters.
- **Tasks:** T34–T40. **Depends:** Phase 3a, D1–D4.
- **Acceptance:**
  - Measured expert bytes/token within 10% of the DES on the same text.
  - Decode at 128K: ≥ 12 tok/s target, ≥ 8 must.
  - Link utilization during miss phases ≥ 70% of measured PCIe peak (Nsight Systems PCIe metrics).
  - Prefetch kept only if it adds ≥ 5% tok/s; CPU path kept only if it adds ≥ 15%.
- **Go/no-go:** measured < 70% of DES prediction → profile before adding features; if the gap is structural (sync latency, launch overhead), fix that first.

### Phase 5: Kernel optimization (~2–3 weeks, can overlap Phase 4)

- **Goal:** move kernels from functional to bandwidth-bound.
- **Tasks:** T41–T44.
- **Acceptance (Nsight Compute):**
  - NVFP4 MoE decode from VRAM ≥ 75% of peak DRAM bandwidth (~570 GB/s).
  - Zero-copy GEMV ≥ 85% of measured PCIe bandwidth.
  - Sparse attention and Quest scoring ≥ 60% of the relevant bandwidth.
  - Prefill MoE ≥ 25 TFLOPS, prefill attention ≥ 35 TFLOPS (FP32 accumulate).
  - CPU NVFP4 GEMV ≥ 70% of measured DRAM bandwidth.

### Phase 6: Scale to 1M, end-to-end (~2 weeks)

- **Goal:** 1M context decode under the hard memory caps.
- **Tasks:** T45–T48. **Depends:** Phases 3b, 4, 5.
- **Acceptance:**
  - 1M prefill completes under caps (expect 30–45 min).
  - Decode median ≥ 8 tok/s over 256 tokens at 1M (target 8–15).
  - Peak VRAM ≤ 9.5 GiB; RSS ≤ 12.5 GiB; swap use 0; < 100 major faults/min during decode.
  - 1M eval subset (fixed-haystack multi-query, RULER NIAH subset, MRCR 8-needle subset): sparse within 5 points of the dense-paged reference at 1M (dense-paged decode reads all pages, ~2 s/token, feasible for short answers).
- **Go/no-go:** < 6 tok/s after Phase 5 → publish results, re-scope to 512K (KV fits RAM there).


---

## 3. Interface contracts

All sketches are C++17 headers under `engine/include/it/`. Signatures are illustrative; names and semantics are binding.

### 3.1 Common types

```cpp
struct Visit { uint16_t idx, layer; uint8_t pass; bool global; int8_t gidx; };  // gidx 0..7 or -1
struct ModelConfig { /* every HF text_config field */ std::vector<Visit> visits; };
enum class Status : int32_t { Ok, OverBudget, IoError, CudaError, InvalidArg, NotReady };
enum class Prio : uint8_t { Demand = 0, KvDemand = 1, Prefetch = 2, Background = 3 };
struct Deadline { uint32_t token; uint16_t visit; };   // work must finish before this visit starts
```

- Visit order is pass-major: visits 0–5 = layers 0–5; 6–41 = layers 6–17 × passes 0..2; 42–47 = layers 18–23.
- No component hard-codes a model dimension; all come from `ModelConfig`.

### 3.2 MemoryBudget and arenas

```cpp
enum class Tier : uint8_t { Vram, Pinned, Host };
class MemoryBudget { Status reserve(Tier, Component, uint64_t bytes); void release(Tier, Component, uint64_t);
                     uint64_t used(Tier) const; std::string report_json() const; };
class DeviceArena { void* alloc(uint64_t bytes, uint32_t align); void free(void*); };     // slab + bump
class PinnedArena { void* alloc(uint64_t, uint32_t); void* device_ptr(void* host) const; }; // mapped
```

- Every buffer > 1 MiB goes through an arena. Raw `cudaMalloc`/`cudaHostAlloc` elsewhere is a review failure.
- Caps come from `configs/hw/*.yaml`. Exceeding a cap returns `OverBudget`; debug builds abort.
- `report_json()` includes NVML process VRAM and `/proc/self/status` VmRSS/VmLck for cross-checking.

### 3.3 ExpertStore

```cpp
struct ExpertId { uint16_t layer; uint8_t expert; };            // physical layer 0..23
enum class Home : uint8_t { VramStatic, Ram };
struct DevExpertEntry { const uint8_t* vram; const uint8_t* host_mapped; uint32_t ring_slot; uint32_t flags; };
class ExpertStore {
  Status init(const PackManifest&, const PlacementPlan&, Arenas&);
  const DevExpertEntry* device_table() const;   // [n_layers * n_experts], lives in VRAM for engine lifetime
  DynamicRing* ring(uint16_t layer);            // per-layer FIFO of R VRAM slots, read-through filled
  Status replan(const PlacementPlan&);          // only between requests; blocking
};
```

- **Ownership:** ExpertStore owns all expert memory in every tier. Each expert has exactly one home (VramStatic or Ram). Dynamic ring slots are copies of Ram-homed experts.
- **Lifetime:** a `vram` pointer from a ring slot is valid until that slot is overwritten. Ring overwrite happens only on the compute stream, so stream order guarantees no kernel reads a slot being refilled. Ring size R ≥ 2 × top_k per core layer so pass-p fills survive until pass p+2 (≥ 16; tune in D2).
- **Device table updates** happen only in-stream (a fill kernel writes the entry after writing the slot).

### 3.4 KV stores

```cpp
class SwaRing { half* k(int visit), *v(int visit); uint32_t head(int visit) const; };  // FP16, 1024 slots
struct PageKey { uint8_t gidx; uint32_t page; };
enum class PageLoc : uint8_t { Tail, VramCache, RamCache, Nvme };
struct DevPageEntry { const uint8_t* vram; const uint8_t* host_mapped; uint32_t flags; };
class GlobalKvStore {
  Status append(int gidx, const half* k, const half* v, cudaStream_t);   // into tail + near ring; seals at 64
  Lease  acquire(int gidx, const uint32_t* pages, int n, Deadline);      // ensures VRAM or RAM residency
  const DevPageEntry* device_page_table(int gidx) const;
  void   release(Lease);                                                 // after attention completion event
};
```

- Page = 64 tokens × 8 KV heads × 128 dims, K then V, quantized per D5. Q4_0 K+V page = 73,728 B = 18 × 4 KiB; K8/V4 = 106,496 B = 26 × 4 KiB.
- **Lifecycle:** Tail (FP16, VRAM) → sealed (quantized; immutable) → written to RAM cache and, beyond the RAM-only threshold, to NVMe (write-behind). Sealed pages never change.
- **Leases:** pages in a Lease are not evicted until `release`. Eviction (LRU) runs only on unleased pages.
- **Near window:** each global visit also keeps the last 1024 tokens FP16 in a VRAM ring for exact attention with relative bias. The sink page (page 0) is pinned in the VRAM cache.

### 3.5 QuestIndex

```cpp
enum class SelectMode : uint8_t { SharedMaxNorm, SharedSum, PerHead };
struct SelectParams { int budget_pages; SelectMode mode; float q_scale; };
class QuestIndex {
  void on_seal(int gidx, uint32_t page, const half* k_page, cudaStream_t);   // builds INT8 min/max box
  void select(int gidx, const half* q, const SelectParams&, uint32_t* d_pages, int* d_n, cudaStream_t);
};
```

- Boxes: per page per KV head, min and max over 64 tokens per dim, INT8 with one FP16 scale each.
- Score for query head h: Σ_d max(q_d·min_d, q_d·max_d). `SharedMaxNorm` = max over heads of (score − per-head max). The score covers far pages only (> 1024 tokens back); near window and sink are always attended exactly.
- `select` runs on the compute stream; outputs stay on device.

### 3.6 Scheduling, streams, I/O, CPU workers

```cpp
struct Xfer { const void* src; void* dst; uint64_t bytes; Prio prio; Deadline dl; cudaEvent_t done; };
class StreamManager { cudaStream_t compute, h2d, d2h; cudaEvent_t visit_done[48]; };
class TransferQueue { void submit(const Xfer&); void pump(); };   // splits into 1 MiB chunks, highest prio first
class NvmeIo { Status submit_read(int fd, uint64_t off, void* pinned, uint32_t len, uint64_t tag);
               int poll(Completion* out, int max); };            // io_uring, O_DIRECT, registered buffers
class CpuExpertPool { void submit(const CpuJob&); };              // results to pinned buffer + mapped flag
```

**Threading model**
- **Driver thread:** owns all mutable engine state; issues all CUDA calls. No locks on the hot path.
- **IO thread (1):** io_uring submit/complete. Talks to the driver via SPSC rings.
- **CPU expert workers (N = physical cores − 2):** pinned to cores; make no CUDA calls; write outputs to pinned buffers and set a mapped flag. The GPU waits via `cuStreamWaitValue32` if supported (T06 checks), else a spin kernel.
- **GPU streams:** `compute` (high priority), `h2d` (copy engine, chunked so demand traffic can jump prefetch), `d2h`.
- Copy-engine traffic cannot be prioritized against zero-copy SM reads. The scheduler sequences them in time instead: KV gather during the attention phase, expert misses after the router, prefetch only in idle windows.

### 3.7 Memory budget per component (1M context, all-NVFP4 experts)

VRAM (cap 9.5 GiB):

| Component | Size | Tag |
|---|---|---|
| CUDA context, modules, cuBLAS (lazy), allocator slack | 0.50 GiB | [E] measure |
| Resident weights: attention Q8_0 (453M params), routers, norms, sconv, loop controllers, attn gates | 0.60 GiB | [D]+[E] gate size unknown |
| LM head Q8_0 (200,058 × 2304) | 0.46 GiB | [D] |
| SWA rings FP16 | 0.16 GiB | [D] |
| Global near-window rings FP16 | 0.03 GiB | [D] |
| Quest boxes INT8 (16,384 pages × 8 visits × 8 heads × ~260 B) | 0.25 GiB | [D] |
| KV VRAM page cache | 0.25 GiB | policy |
| Decode scratch | 0.06 GiB | [E] |
| Safety margin | 0.25 GiB | policy |
| **Expert static set + dynamic rings** | **6.94 GiB** (≈1,660 experts, 54%) | remainder |

During prefill, ~0.8–1.0 GiB of prefill scratch is carved out of the expert budget and returned at decode start.

RAM (cap 12.5 GiB):

| Component | Size | Tag |
|---|---|---|
| Process baseline (engine, CUDA host side) | 0.8 GiB | [E] measure |
| Eval client process (no torch) | 0.3 GiB | [E] |
| Experts homed in RAM, pinned | 5.88 GiB | [D] (7.66 if layers 20–23 stay MXFP8) |
| Embedding Q8_0 | 0.46 GiB | [D] |
| NVMe staging | 0.25 GiB | policy |
| Safety margin | 0.5 GiB | policy |
| **Global KV RAM page cache** | **4.3 GiB** (~62K pages, ~47% of 1M KV) | remainder |

Pinned total ≈ 10.4 GiB of 16 GiB physical. This is aggressive; T03/T06 must confirm the OS tolerates it. With RAM page cache ≥ KV size (≤ ~480K tokens), the NVMe KV tier is idle.


---

## 4. Task breakdown for DeepSeek

### 4.0 Conventions (prepend to every task prompt)

- C++17, CUDA ≥ 12.8, `sm_86`, CMake ≥ 3.27, Ninja. Kernels built with `-lineinfo`.
- Python ≥ 3.11 via `uv`. Lab deps: numpy, safetensors, pyyaml, matplotlib, pytest, tokenizers, torch (lab only). Engine deps: googletest, nlohmann_json, liburing. Ask before adding anything else.
- Never hard-code model dims; read `ModelConfig`. Never allocate > 1 MiB outside `MemoryBudget` arenas.
- Public C++ functions return `Status`; no exceptions across the C ABI.
- Every kernel has a CPU reference implementation in its test, with stated tolerances.
- Touching a file format means updating `docs/formats.md` in the same task.
- Only modify the files listed in the task. If another file must change, say so in the report.
- Report at the end: files changed, tests run with results, measured numbers, open issues.

Card fields: **Files**, **In → Out**, **Spec**, **Tests**, **Done when**. Sessions are [E].

### Phase 0

**T01. Repo skeleton and one-command setup** (1 session; deps: none)
- Files: `CMakeLists.txt`, `engine/CMakeLists.txt`, `engine/src/core/health.cpp`, `engine/tests/test_smoke.cpp`, `pyproject.toml`, `setup.sh`, `.gitignore`, `.gitmodules` (llama.cpp pinned to a commit ≥ b12600).
- In → Out: empty repo → buildable repo; `it_health` binary.
- Spec: `setup.sh` checks OS (Ubuntu ≥ 22.04), NVIDIA driver, `nvcc ≥ 12.8`; installs apt packages (build-essential, cmake, ninja-build, liburing-dev, fio); installs `uv` and the lab venv; builds engine and llama.cpp with CUDA; runs `ctest`; runs `it_health`. `it_health` prints JSON: GPU name, VRAM total/free, driver, CUDA runtime, PCIe gen/width (NVML), RAM total/available, CPU flags (avx2, avx512f, avx_vnni), swap state, free disk at `results/`. On any failed requirement, print one actionable message and exit non-zero. Idempotent.
- Tests: smoke ctest; run `setup.sh` twice.
- Done when: fresh clone → `./setup.sh` prints health JSON; second run finishes in < 60 s without rebuilding.

**T02. ModelConfig and visit schedule** (1 session; deps: T01)
- Files: `engine/include/it/config.h`, `engine/src/core/config.cpp`, `engine/tests/test_config.cpp`, `configs/model/inkling2_mini.json`, `configs/hw/default.yaml`.
- In → Out: HF `config.json` + hw yaml → `ModelConfig`, `HwConfig`.
- Spec: parse every field of `text_config` including `loop_config` and `local_layer_ids`. Build `visits` in pass-major order (§3.1). `global = layer ∉ local_layer_ids`; `gidx` numbers global visits 0..7 in order. Helpers: `kv_bytes_per_token_visit(fp16) = 2·n_kv·head_dim·2`; `expert_params = 3·hidden·intermediate`. Reject unknown `model_type`. HwConfig holds tier caps and per-component caps.
- Tests: 48 visits; global idx set {5,11,17,23,29,35,41,47}; visit 6→(6,0), 18→(6,1), 41→(17,2), 42→(18,0); 40 locals; kv bytes 4096; expert params 7,962,624. Synthetic config with `num_passes=1` → 24 visits.
- Done when: tests pass.

**T03. MemoryBudget and arenas** (1 session; deps: T02)
- Files: `engine/include/it/memory.h`, `engine/src/core/memory.cpp`, `engine/tests/test_memory.cpp`.
- Spec: per §3.2. Components enum covers every row of §3.7. DeviceArena: one `cudaMalloc` per component region at init, slab allocator for fixed-size objects (experts, pages), bump allocator for the rest. PinnedArena: `cudaHostAlloc(Mapped|Portable)`, exposes device pointer. `report_json()` with used/cap per component and NVML/VmRSS/VmLck cross-check. Add a test binary that pins N GiB in 1 GiB steps and reports when allocation or system responsiveness fails (for the 10 GiB pinned question).
- Tests: cap enforcement; slab reuse; mapped pointer valid in a kernel; JSON schema.
- Done when: tests pass; pinned-limit result recorded.

**T04. PCIe microbenchmark** (1 session; deps: T03)
- Files: `engine/bench/bench_pcie.cu`, `scripts/bench_pcie.sh`.
- Spec: measure (a) `cudaMemcpyAsync` H2D/D2H, pinned and pageable, 4 KiB–64 MiB in ×2 steps, 50 reps, median and p10 GB/s plus per-call latency; (b) 256 and 2048 scattered copies of 9 KiB and 72 KiB from random pinned offsets via individual `cudaMemcpyAsync`, `cudaMemcpyBatchAsync` (if available), and a gather kernel reading mapped pinned memory; (c) zero-copy streaming read of 4.27 MiB blocks with {8,16,34,68} SMs; (d) H2D memcpy concurrent with a zero-copy kernel (aggregate); (e) H2D concurrent with a VRAM-bound kernel (interference). Verify data after each copy. CSV to `results/bench/pcie.csv`.
- Done when: CSV + summary table in `docs/measurements.md`.

**T05. NVMe microbenchmark and io_uring wrapper** (1 session; deps: T03)
- Files: `engine/include/it/nvme_io.h`, `engine/src/io/nvme_io.cpp`, `engine/bench/bench_nvme.cpp`, `scripts/bench_nvme.sh`.
- Spec: `NvmeIo` per §3.6 (O_DIRECT, registered pinned buffers, 4096 alignment checks). Bench: create a 20 GiB file if absent; random reads of 4, 72, 104 KiB, 1 MiB, 4.27 MiB at QD {1,4,16,32,64}; sequential 8 MiB. Report GB/s, IOPS, p50/p99 latency. Confirm page cache is unaffected (`/proc/meminfo` Cached before/after). Run equivalent fio jobs in the script.
- Done when: results within 15% of fio; CSV + summary.

**T06. Host, CPU, and sync microbenchmark** (1 session; deps: T03)
- Files: `engine/bench/bench_host.cu`.
- Spec: (a) DRAM read bandwidth, 1..N threads; (b) DRAM read while H2D runs; (c) GPU→host latency (kernel writes mapped flag, host spins); host→GPU via `cuStreamWaitValue32` (report support) and via spin kernel; (d) empty-kernel launch cost and a CUDA graph of 600 small kernels; (e) `cudaEventSynchronize` vs spin-wait.
- Done when: CSV + summary.

**T07. llama.cpp baseline** (1 session; deps: T01, T03 for cgroup script)
- Files: `scripts/baseline_llamacpp.sh`, `scripts/run_capped.sh`, `lab/evals/baseline_parse.py`.
- Spec: `run_capped.sh` wraps any command in `systemd-run --user --scope -p MemoryMax=12.5G -p MemorySwapMax=0`. Baseline: base-model GGUF Q4_K_S (not abliterated variants). Sweep `--n-cpu-moe` to the largest GPU share fitting 9.5 GiB, `-ngl 99 -fa on -ctk q8_0 -ctv q8_0`, contexts 4K and 32K (64K if it fits), `--no-mmap` and mmap. Record decode tok/s, prefill tok/s, peak VRAM (nvidia-smi polling at 100 ms), peak RSS and major faults (`/usr/bin/time -v`), swap.
- Done when: `results/baseline.csv` + table in `docs/measurements.md`.

**T08. Analytic performance model** (1 session; deps: T02)
- Files: `lab/sim/analytic.py`, `lab/sim/params/default.yaml`, `lab/tests/test_analytic.py`.
- Spec: closed-form per-token decode time = sum of the §0.5 terms. Inputs: model config, measured hw CSVs (optional, else yaml defaults), policy params (h, budget %, page size, KV format, link efficiency, CPU-path bandwidth, RAM page-cache hit). Outputs: tok/s, ms per term, bytes per tier per token, VRAM/RAM budget check (fail if over cap), prefill time estimate (§0.4 formulas). CLI table; `--sweep` over h ∈ [0.3, 0.8] and budget ∈ {1.6, 3, 5}%.
- Tests: §0.5 base column within 5%; §3.7 tables reproduced.
- Done when: tests pass; measured-input rerun appended to `docs/measurements.md`.

### Phase 1

**T09. Model numerics spec** (1–2 sessions; deps: none)
- Files: `docs/model-spec.md`.
- In: official HF repo modeling code (if present), llama.cpp PR #32694 sources, vLLM model file.
- Spec: for every op in a visit, write the exact math with shapes, dtypes, constants, and order: embedding (any scaling), prelude, loop core (rank-64 gated controller, which tensors carry messages between passes), block attention residuals (`n_layers_per_attn_res_block=6`, `attn_res_normalize_values`), the 4 short convolutions (where, causal state), attention (projections, value norm without gamma, q/k L2 clip, relative bias with `d_rel=16`, `rel_extent=1024`, log scaling, sink, gate, window boundary inclusive/exclusive), router (`use_gate_bias`, `route_scale=8`, top-k, weight normalization), expert MLP (gate/up softcaps 4 and 25, activation), final norm, logit softcap 30, unpadded vocab. List per-pass decode state with bytes per token. Document NVFP4/MXFP8 tensor names and dequant formulas in the checkpoint. Answer explicitly: is anything position-related cached per token beyond K/V? Is the relative bias zero (or constant) beyond 1024? Are embeddings tied? Flag every discrepancy between implementations.
- Done when: every config field maps to a code location; reviewer signs off.

**T10. libllama trace and dump tool** (1–2 sessions; deps: T01, T07, T09)
- Files: `tools/llama_trace/main.cpp`, `tools/llama_trace/CMakeLists.txt`, `docs/formats.md`.
- Spec: link libllama; register `cb_eval`. Modes: `--list-tensors` (names/shapes of all graph nodes for one token, to find router outputs, router inputs, Q/K per visit); `--trace` (teacher-forced token files, ubatch 512; write `ids [T,48,8] uint8`, `w [T,48,8] fp16`, `doc_offsets int64`); `--dump-router-input` (`fp16 [T,48,2304]` for a token range); `--dump-attn` for global visits (`K [T,8,128] fp16` for all tokens, `Q [S,24,128] fp16` at given positions, tensors exactly as named in `model-spec.md`). Document the llama.cpp layer/pass → visit mapping.
- Tests: T×48 records; ids < 128; deterministic across runs; on 64 tokens, dumped K at a global visit matches the KV cache content within quantization tolerance.
- Done when: trace of 10K tokens produced and validated.

**T11a. NVFP4/MXFP8 weight loader (Python)** (1 session; deps: T09)
- Files: `lab/reference/weights.py`, `lab/tests/test_weights.py`.
- Spec: lazy safetensors iteration. NVFP4: packed E2M1 nibbles, E4M3 scale per 16 along the input dim, FP32 per-tensor scale → BF16 on GPU. MXFP8: E4M3 values, E8M0 scale per 32 → BF16. BF16 passthrough. API `load_layer(layer, device) -> dict[str, Tensor]`, `load_globals()`.
- Tests: E2M1 LUT, nibble order, scale application; bit-exact vs vLLM's or modelopt's dequant on 3 tensors.
- Done when: tests pass.

**T11b. Layer-streamed reference forward** (2 sessions; deps: T11a)
- Files: `lab/reference/model.py`, `lab/reference/ops.py`, `lab/reference/hooks.py`, `lab/reference/run.py`.
- Spec: reuse official modeling classes if available, else implement from `model-spec.md` in `ops.py`. Run ≤ 8K tokens: all tokens through visit v before v+1, loading each physical layer once per visit. Hooks dump router inputs, top-k, per-visit hidden output, Q/K/V at chosen visits, final logits. VRAM ≤ 9.5 GiB.
- Tests: per-op unit tests vs official module on random inputs (if official exists); sane perplexity on a PG-19 chunk; coherent greedy continuation; top-8 Jaccard vs T10 traces reported.
- Done when: golden dumps for 3 sequences (512, 2K, 8K tokens) saved.

**T12. Dataset builders** (1 session; deps: T01)
- Files: `lab/evals/datasets.py`, `scripts/fetch_datasets.sh`.
- Spec: (a) trace corpora ≥ 50K tokens each: code, prose (PG-19), chat/reasoning, long-doc QA, tokenized with the model tokenizer and chat template; (b) RULER (NVIDIA/RULER) tasks niah_single_1/2/3, niah_multikey_1/2/3, niah_multivalue, niah_multiquery, vt, cwe, fwe, qa_1/2 at 32K and 128K, n=50 each; (c) MRCR v2 8-needle (`openai/mrcr`) bucketed ≤128K, 128–256K, ~1M; (d) one 1M-token fixed haystack with 20 needles and 20 queries. Each item stores token ids, answer, and answer-token positions for teacher forcing.
- Done when: files in `results/data/` with a manifest.

**T13. Routing statistics report** (1 session; deps: T10, T12)
- Files: `lab/analysis/routing_stats.py`, `lab/tests/test_routing_stats.py`.
- Spec: from traces compute per-layer popularity CDF, entropy, loop overlap o per core layer, temporal overlap t→t+1 per visit, union size for n ∈ {2,4,8} consecutive tokens per visit, and (if T11b dumps exist) top-8 Jaccard llama.cpp vs reference. Markdown report with plots.
- Tests: synthetic traces with known overlap and union values.
- Done when: report generated for all domains.

**T14. Trace-driven expert cache simulator** (1–2 sessions; deps: T13)
- Files: `lab/analysis/cache_sim.py`, `lab/tests/test_cache_sim.py`.
- Spec: replay traces in visit order. Policies: LRU, LFU, static-popularity (fit on a disjoint training split), Belady (upper bound), hybrid static + per-layer FIFO ring of R slots with read-through fill. Inputs: VRAM expert budget (GiB), expert size, R. Outputs: byte hit rate, miss bytes/token, split into cross-token hits and intra-token loop-reuse hits; per domain and cross-domain (fit on code, test on prose).
- Tests: Belady ≥ every other policy; hand-checked tiny traces.
- Done when: curves for budgets 3–8 GiB; G1 evaluated.

**T15. Lookahead probe evaluation** (1 session; deps: T10 router-input dumps, T11a)
- Files: `lab/analysis/probe_eval.py`.
- Spec: predictors for visit v+d (d = 1..3): router of v+d applied to the router input at v; loop prior (pass p's set for the same layer); temporal prior (token t−1, same visit); union of the above ranked by score. Metrics: recall and precision at prefetch budgets B ∈ {8,12,16}; wasted bytes/token.
- Done when: report; D4 input ready.

**T16. Quest recall analysis** (1–2 sessions; deps: T09, T10 attention dumps)
- Files: `lab/analysis/quest_recall.py`, `lab/tests/test_quest_recall.py`.
- Spec: for each sampled query, compute exact attention per `model-spec.md` (log scaling, sink, near-window bias), split into far (> 1024 back) and near. Oracle top-K pages by true far mass vs Quest upper-bound selection. Sweep: page size {32,64,128}; budget {0.5,1,1.6,3,5,8}%; modes SharedMaxNorm, SharedSum, PerHead; K stored FP16, Q4_0, Q8_0, KIVI per-channel-in-page (boxes built from quantized K). Also report selection overlap between consecutive answer queries (for page-cache hit estimates). Metrics: far-mass recall (mean, p10), output error ‖o_sparse − o_dense‖/‖o_dense‖.
- Tests: synthetic Q/K with planted needles.
- Done when: report at 32K and 128K; G2 evaluated.


### Phase 2

**T17. Discrete-event performance simulator** (2 sessions; deps: T04–T06, T08)
- Files: `lab/sim/des/{core.py,resources.py,policies.py,run.py}`, `lab/tests/test_des.py`.
- Spec: resources = GPU compute stream, copy engine (FIFO, chunked), zero-copy reads sharing the PCIe link, NVMe queue (latency/bandwidth curves from T05), CPU worker pool (bandwidth from T06), DRAM bandwidth shared by PCIe DMA, NVMe DMA, and CPU workers. Per token, build the 48-visit task graph with real dependencies (router → miss fetch → MoE; Quest select → page fetch → attention). Kernel times from a roofline using T04/T06 numbers. Inputs: routing traces, Quest selection traces (from T16), policy plugin. Outputs: tok/s distribution, per-resource utilization, idle windows, bytes per tier.
- Tests: synthetic uniform traces match T08 within 5%; zero-bandwidth resource produces infinite time (sanity).
- Done when: validated.

**T18. Policy sweep and ADRs** (1 session; deps: T14–T17)
- Files: `lab/sim/des/sweep.py`, `docs/adr/D1..D8.md`.
- Spec: sweep D1–D8 options (§2 Phase 2) at 128K, 512K, 1M with p10/p50/p90 parameter sets. Each ADR states options, numbers, decision, and the measurement that would reverse it.
- Done when: ADRs reviewed; Phase 2 gate evaluated.

### Phase 3a

**T19. Pack converter** (1–2 sessions; deps: T09, T11a)
- Files: `tools/pack/convert.py`, `tools/pack/tests/test_convert.py`, `docs/formats.md`.
- Spec: safetensors → `experts.bin`, `resident.bin`, `embed.bin`, `manifest.json`. Expert blob for (layer, expert) at offset `(layer·n_experts + expert)·STRIDE`, STRIDE = blob size rounded up to 4096. Blob layout v1: gate|up packed nibbles (row-major, low nibble = even column), down nibbles, E4M3 scales, FP32 tensor scales, padding; `layout_version` field reserved for kernel swizzles. Layers 20–23: option `--mxfp8-to-nvfp4` (default on) or keep MXFP8 with per-layer `format` in manifest. Resident weights Q8_0 (option BF16). Embedding Q8_0 rows. Text-only: skip vision and MTP unless `--with-mtp`.
- Tests: dequant(pack) vs T11a dequant(source): exact for NVFP4 passthrough; report error for MXFP8→NVFP4 and Q8_0; alignment of every blob.
- Done when: pack built; sizes match §0.2 within 2%.

**T20. Pack reader and resident weights** (1 session; deps: T03, T05, T19)
- Files: `engine/include/it/pack.h`, `engine/src/io/pack_reader.cpp`, `engine/src/model/resident_weights.cpp`, tests.
- Spec: parse manifest; O_DIRECT reads via NvmeIo into arenas; resident weights to VRAM; RAM-homed experts to PinnedArena; embedding rows to RAM. Load time and throughput logged.
- Tests: checksums of loaded blobs vs manifest; arena accounting matches manifest sizes.
- Done when: full load under caps; load time recorded.

**T21. Decode GEMV kernels (functional)** (1–2 sessions; deps: T19)
- Files: `engine/kernels/gemv_nvfp4.cu`, `gemv_mxfp8.cu`, `gemv_q8_0.cu`, `gemv_bf16.cu`, `engine/tests/test_gemv.cpp`.
- Spec: y = W·x, batch 1, x BF16, FP32 accumulate, y FP32 or BF16. NVFP4 dequant via 16-entry LUT and E4M3→FP32 scale LUT. Weight pointer may be VRAM or mapped host (zero-copy). Fused variant for gate+up into one launch.
- Tests: vs CPU FP64 reference on random and real blobs; rel error ≤ 1e-3.
- Done when: tests pass; bandwidth printed (no target yet).

**T22a. Small ops: norms, clips, softcaps, router** (1 session; deps: T09)
- Files: `engine/kernels/ops_norm.cu`, `ops_router.cu`, tests.
- Spec: RMSNorm, value norm without gamma, q/k L2 clip, softcaps, router logits + gate bias + route scale + top-k + weight normalization, exactly per `model-spec.md`.
- Tests: vs `lab/reference/ops.py` outputs on dumped inputs (golden files), tolerance ≤ 1e-3 rel; top-k ids identical.

**T22b. Small ops: short conv, loop controller, attention residual blocks** (1 session; deps: T09)
- Files: `engine/kernels/ops_sconv.cu`, `ops_loop.cu`, `ops_attnres.cu`, tests.
- Spec: per `model-spec.md`, decode step versions with explicit state buffers (conv state per pass, loop messages, block outputs).
- Tests: as T22a; multi-step state test over 16 tokens.

**T23. Decode attention (functional)** (1–2 sessions; deps: T09, T22a)
- Files: `engine/kernels/attn_local_decode.cu`, `attn_dense_global_decode.cu`, tests.
- Spec: local: window ring FP16 with relative bias, sink, gate, log scaling, GQA 24/8. Dense global (for ≤ 32K and as reference mode): reads FP16 KV or dequantized pages; split-KV with LSE merge. Output also returns LSE for later merging.
- Tests: vs reference ops on dumped Q/K/V, including window-boundary positions and ring wrap.

**T24. MoE decode op (functional)** (1 session; deps: T21, T22a)
- Files: `engine/kernels/moe_decode.cu`, tests.
- Spec: input top-8 ids and weights, device expert table (§3.3); per expert: gate/up GEMV → softcaps → activation → down GEMV → weighted sum. Pointer may be VRAM or mapped host.
- Tests: vs reference expert MLP on dumped inputs.

**T25. Forward driver, naive placement, C API** (2 sessions; deps: T20–T24)
- Files: `engine/src/model/forward.cpp`, `engine/src/model/state.cpp`, `engine/capi/engine_c_api.cpp`, `lab/evals/engine_client.py`.
- Spec: 48-visit schedule; per-pass state; naive placement: static VRAM set from a placement file (uniform fallback), misses served by zero-copy GEMV (no fill). Prefill for short contexts = token loop. C API: `it_create(cfg)`, `it_prefill(tokens)`, `it_decode(n, sampler)`, `it_logits()`, `it_stats_json()`, `it_destroy()`. Python client via ctypes, no torch.
- Tests: runs 4K tokens under caps; stats JSON includes arena report.

**T26. Equivalence harness** (1 session; deps: T11b, T25)
- Files: `lab/evals/equivalence.py`.
- Spec: run engine and reference on the same token files with hooks; per-visit cosine and rel L2; final KL and top-1 agreement; first-divergence localization (which visit/op).
- Done when: Phase 3a acceptance numbers reported.

### Phase 3b

**T27. Chunked prefill v1** (2 sessions; deps: T25)
- Files: `engine/src/sched/prefill.cpp`, `engine/kernels/dequant_scratch.cu`.
- Spec: chunk size C (default 8192). Per visit: projections via cuBLAS on dequantized BF16; MoE: group tokens by expert, dequantize experts to scratch in groups of 16, cuBLAS GEMM, scatter-add. Experts streamed from RAM per visit (VRAM-resident ones used in place). Writes SWA rings, global tail/near rings, sealed pages.
- Tests: chunked prefill of 4K vs token-loop prefill: logits KL ≤ 0.005.

**T28. Prefill attention kernel** (2 sessions; deps: T23, T29)
- Files: `engine/kernels/attn_prefill.cu`.
- Spec: query tile × KV tile flash-style kernel with FP32 accumulate. Near part (≤ 1024 back) with relative bias in FP16; far part over quantized pages (dequant in shared memory) streamed from RAM via zero-copy or staged copies; LSE merge; sink; log scaling.
- Tests: vs reference at 8K; far/near split vs monolithic reference.

**T29. Page quantizers** (1 session; deps: T09)
- Files: `engine/kernels/page_quant.cu`, tests.
- Spec: formats Q4_0, Q8_0, KIVI-style INT4 K (per-channel scale/zero across 64 tokens per head), Q4_0 V. Byte layouts per §3.4, 4 KiB multiples. Dequant device functions shared with attention kernels.
- Tests: round-trip error bounds; layout sizes 73,728 / 106,496 B.

**T30. GlobalKvStore, RAM tier** (2 sessions; deps: T03, T29)
- Files: `engine/include/it/kv.h`, `engine/src/kv/global_kv.cpp`, `engine/src/kv/swa_ring.cpp`, tests.
- Spec: per §3.4: tail page, near ring, seal (quantize + `QuestIndex::on_seal`), RAM page pool (slab), VRAM page cache (LRU), device page tables, Leases. NVMe path stubbed (Phase 6).
- Tests: append 10K tokens; page contents vs direct quantization; lease prevents eviction; eviction order.

**T31. QuestIndex** (1 session; deps: T29, T30)
- Files: `engine/src/retrieval/quest.cpp`, `engine/kernels/quest_score.cu`, `engine/kernels/topk.cu`, tests.
- Spec: per §3.5; INT8 boxes; scoring kernel; radix-select top-K; all three SelectModes.
- Tests: vs `lab/analysis/quest_recall.py` selections on the same dumps: identical page sets (ties aside).

**T32. Sparse global decode attention** (1–2 sessions; deps: T30, T31, T23)
- Files: `engine/kernels/attn_sparse_decode.cu`, tests.
- Spec: inputs: selected page list, device page table (VRAM or mapped host pointers). Split over pages, dequant in registers/shared memory, partial softmax, merge with near-window (T23) and sink via LSE. Budget = all pages must equal dense-paged output.
- Tests: budget=all vs dense reference ≤ 1e-3 rel; random budgets vs CPU sparse reference.

**T33. Long-context eval runner** (1 session; deps: T12, T27, T32)
- Files: `lab/evals/ruler_runner.py`, `lab/evals/mrcr_runner.py`.
- Spec: run RULER/MRCR via the C API; modes dense-paged and sparse with budget list; greedy decoding, max new tokens per task; scoring per the benchmark's official scorer; results CSV with per-task accuracy, prefill time, decode tok/s, peak VRAM/RSS.
- Done when: Phase 3b acceptance table produced.


### Phase 4 (re-issue these cards after the Phase 2 ADRs; gated tasks marked)

**T34. Placement planner** (1 session; deps: T14, D2)
- Files: `tools/pack/placement.py`, `docs/formats.md`.
- Spec: from training-split traces, choose the static VRAM set maximizing expected byte hits under the expert budget (popularity per physical layer, counting all passes). Emit `placement.json`: home per expert, ring size R per layer, budget used.
- Tests: on held-out traces, planner's predicted hit rate equals T14 static-policy result.

**T35. ExpertStore with dynamic rings** (2 sessions; deps: T20, T34)
- Files: `engine/include/it/experts.h`, `engine/src/experts/expert_store.cpp`, `engine/kernels/expert_fill.cu`, tests.
- Spec: per §3.3. Read-through fill: when a miss is computed via zero-copy, the kernel also writes the bytes into the layer ring slot `head % R` and updates the device table entry in-stream, invalidating the slot's previous owner.
- Tests: ring invariants under a replayed trace; hit counts equal T14 hybrid policy on the same trace (exact).

**T36. Miss path per D1** (1–2 sessions; deps: T35, T37 if copy-engine path)
- Files: `engine/kernels/moe_decode_tiered.cu` (zero-copy read-through) or `engine/src/experts/miss_copy.cpp` (copy-engine into slots + per-expert events).
- Spec: implement only the ADR-selected path; keep the other behind a compile flag only if the ADR says so.
- Tests: numerics identical to T24; measured bytes over PCIe equals miss bytes.

**T37. StreamManager and TransferQueue** (1 session; deps: T03)
- Files: `engine/include/it/sched.h`, `engine/src/sched/streams.cpp`, `engine/src/sched/transfer_queue.cpp`, tests.
- Spec: per §3.6; 1 MiB chunking; priority order Demand > KvDemand > Prefetch > Background; deadline accounting (late-arrival counter).
- Tests: a Demand submitted behind 64 MiB of Prefetch completes after ≤ 1 chunk of delay.

**T38. CPU expert path** (gated by D3; 2 sessions; deps: T06, T19)
- Files: `engine/src/cpu/nvfp4_avx2.cpp`, `engine/src/cpu/worker_pool.cpp`, tests.
- Spec: E2M1 nibbles → int8 (values ×2: {0,1,2,3,4,6,8,12} with sign) via `pshufb`; activations quantized to int8 per 16-block; `maddubs`/VNNI dot; E4M3 scale LUT; FP32 output. Workers pinned to cores. Scheduler splits a visit's misses between GPU zero-copy and CPU to balance finish times (DES-derived ratio, then online adjustment). GPU waits via `cuStreamWaitValue32` or spin kernel.
- Tests: vs FP64 reference; bandwidth vs T06 DRAM numbers.

**T39. Lookahead prefetcher** (gated by D4; 1 session; deps: T15, T35, T37)
- Files: `engine/src/experts/prefetch.cpp`, `engine/kernels/probe.cu`.
- Spec: ADR-selected predictor; issue Prefetch-priority fills into ring slots only during idle windows; count useful vs wasted bytes.
- Tests: replay mode reproduces T15 precision on the same trace.

**T40. Decode scheduler, CUDA graphs, counters** (2 sessions; deps: T35–T37)
- Files: `engine/src/sched/decode.cpp`, `engine/src/core/counters.cpp`.
- Spec: capture each token as CUDA graph segments split only where host action is needed (NVMe KV misses, CPU path handoff if no stream-wait support). NVTX ranges per visit and phase. Counters per token: bytes per tier, hit rates, wait times, late arrivals.
- Done when: Phase 4 acceptance table at 128K.

### Phase 5

**T41. Fused MoE decode kernel** (1–2 sessions): 8 experts in one launch, gate+up fused, down + weighted sum fused, split-K across SMs; may introduce `layout_version 2` in the pack. Target ≥ 75% DRAM peak for VRAM experts, ≥ 85% PCIe for zero-copy. Tests: numerics vs T24.

**T42. Sparse attention and Quest optimization** (1–2 sessions): vectorized dequant, page-parallel split with tree LSE merge, INT8 box scoring with dp4a. Targets per Phase 5. Tests vs T31/T32.

**T43. Prefill NVFP4 grouped GEMM** (2 sessions, optional if prefill time is acceptable): dequant tiles into shared memory, `mma.sync` FP16, grouped by expert. Target ≥ 25 TFLOPS. Tests vs T27 output.

**T44. Prefill attention optimization** (1–2 sessions): tune T28 tiles, async copies, FP32 accumulate. Target ≥ 35 TFLOPS. Tests vs T28.

### Phase 6

**T45. NVMe KV tier** (2 sessions; deps: T05, T30): one file per global visit, page p at offset `p·page_bytes`; write-behind during prefill; demand reads through NvmeIo into the RAM page pool; prefetch of the previous step's selection; RAM LRU with leases. Tests: kill-and-reload integrity (checksums); stall time per global visit logged.

**T46. KV and state snapshot/restore** (1 session; deps: T45): save NVMe KV files + SWA rings + conv states + loop/attn-res state + token position; restore and continue decoding. Test: logits after restore equal logits without snapshot (bit-exact).

**T47. 1M benchmark and memory-limit harness** (1 session): runs prefill + 256-token decode at 128K/256K/512K/1M under `run_capped.sh`; collects tok/s (median, p10, p90 per-token latency), peak VRAM (NVML), peak RSS, swap, major faults, PCIe and NVMe throughput. Includes a "VRAM balloon" run (second process holding 0.5 GiB) to confirm the 9.5 GiB cap holds.

**T48. 1M eval subset** (1 session): fixed-haystack multi-query set (one prefill, 20 queries via snapshot), RULER NIAH subset, MRCR 8-needle subset; sparse vs dense-paged.


---

## 5. Test and benchmark plan

### 5.1 Unit tests
- C++: googletest via `ctest`. Python: pytest. Run on every task.
- Kernels: CPU FP64 reference; random inputs plus real weight blobs; edge cases (ring wrap, window boundary, page boundary, budget = 0 and = all).

### 5.2 Numerical equivalence ladder
1. Kernel vs CPU reference (T21–T24, T29, T31, T32): rel error ≤ 1e-3.
2. Op vs `lab/reference/ops.py` on dumped real inputs: rel error ≤ 1e-3, top-k ids identical.
3. Per-visit hidden state vs reference at 512/2K/8K tokens: cosine ≥ 0.9995.
4. End-to-end logits (teacher-forced): mean KL ≤ 0.005 nats, top-1 ≥ 99.5%.
5. Quantization choices (MXFP8→NVFP4 for layers 20–23, Q8_0 attention, KV formats): perplexity delta on PG-19 and code ≤ 1%, plus RULER at 32K ≤ 1 point.
6. Sparse vs dense-paged at the same KV format: thresholds in Phases 3b and 6.

### 5.3 Memory-limit tests
- Every benchmark runs under `scripts/run_capped.sh` (cgroup v2 `MemoryMax=12.5G`, `MemorySwapMax=0`).
- RSS/VmLck from `/proc/self/status`; major faults via `/usr/bin/time -v` and `perf stat -e major-faults`; page cache via `/proc/meminfo`.
- VRAM: engine arena report plus NVML per-process polling at 10 ms; balloon test (T47).
- Fail conditions: any OverBudget at runtime, RSS > 12.5 GiB, swap > 0, > 100 major faults/min during decode.

### 5.4 Recall sweeps
- Offline (cheap, many budgets from one dump): T16 far-mass recall and output error at 32K and 128K.
- Online: RULER (13 tasks) at 32K and 128K, n=50; MRCR v2 8-needle at 128K and 256K. The model card reports [46.4% on MRCR v2 8-needle 256K–512K](https://thinkingmachines.ai/model-card/inkling-2-mini/); our dense-paged result should land near that before sparse comparisons mean anything.
- 1M: small subsets only (§0.4); prefer the fixed-haystack multi-query set with snapshots.

### 5.5 Performance profiling
- Engine counters (T40) per token: bytes per tier, hit rates, waits, late arrivals.
- Nsight Systems with `--gpu-metrics-device=all` for PCIe read/write throughput and timeline; NVTX ranges per visit/phase.
- Nsight Compute for kernel DRAM throughput and occupancy.
- `nvidia-smi dmon -s pucvmet` for coarse PCIe and clocks; check for thermal throttling during long runs.
- `perf stat` for CPU path (cycles, LLC misses; uncore IMC counters for DRAM bandwidth on Intel, `amd_df` on AMD).
- `iostat -x 1` for NVMe; io_uring latency histograms from NvmeIo.
- Protocol: 32-token warmup, 256 measured tokens, report median and p10/p90 per-token latency; 3 runs; record GPU clocks.

---

## 6. Risk register and kill criteria

| # | Risk | Likelihood | Early signal (phase) | Mitigation | Kill / pivot trigger |
|---|---|---|---|---|---|
| R1 | PCIe bandwidth lower than assumed (x8 slot, chipset lanes) | Med | T04 (P0) | Fix slot; lower-bit cold experts | H2D < 15 GB/s → re-scope target |
| R2 | VRAM hit rate too low (balanced routing, low loop overlap) | Med-High | T13/T14 (P1) | Loop-reuse rings; CPU path; 3-bit cold experts | Miss bytes > 1.6 GiB/token at best policy → kill 1M@8 tok/s |
| R3 | Quest recall insufficient at small budgets | Med | T16 (P1) | Per-head selection, smaller pages, K8, larger budget | Needed budget > 8% at 1M, or RULER drop > 3 pts at ≤ 5% (P3b) → stop 1M track |
| R4 | Relative-bias attention breaks the far/near split assumption (A-REL) | Med | T09 (P1) | Bound the bias per page in the Quest score; widen near window | Bias unbounded beyond 1024 and no tight per-page bound → redesign retrieval before P3b |
| R5 | Global KV capacity at 1M (9–13 GiB) | Certain | §0.2 | NVMe home tier + RAM cache | NVMe stalls > 15 ms/token after prefetch → cap target at 512K |
| R6 | Pinned memory ~10.4 GiB destabilizes the OS | Med | T03 (P0) | Smaller pinned regions; register on demand; pageable for cold KV cache | System thrash at < 8 GiB pinned → re-budget (lower-bit experts) |
| R7 | Engine numerics don't match reference | Med | T26 (P3a) | Per-op golden tests; first-divergence localization | Not met in 3 weeks → llama.cpp fork fallback |
| R8 | Host sync / launch overhead eats the budget | Med | T06 (P0), T40 (P4) | Zero-copy read-through (no host per visit); CUDA graphs; stream-wait-value | Bubbles > 15 ms/token after graphs → GPU-autonomous redesign |
| R9 | Prefill too slow to iterate (30–40 min at 1M) | Certain | §0.4 | Offline dump analysis, 128K for most evals, snapshots | n/a (scheduling risk); budget evaluation time accordingly |
| R10 | Speculative decoding hurts (expert union growth) | High | T13 (P1) | Keep out by default | G4 fails → drop permanently |
| R11 | NVFP4 W4A16 emulation deviates from trained numerics (if trained W4A4) | Low-Med | T26, 5.2 step 5 | Compare against vLLM output on short prompts if any access to a Hopper/Blackwell endpoint | PPL delta > 2% vs provider API logprobs → investigate activation quant |
| R12 | DRAM bandwidth contention (PCIe DMA + NVMe + CPU path) | Med | T06 (P0), T38 | DES models it; throttle CPU path | CPU path gain < 15% → drop it |
| R13 | Model updates or llama.cpp churn | Med | ongoing | Pin model revision and llama.cpp commit | n/a |

Global kill rule: if Phase 2 predicts < 5 tok/s at 1M with p10 parameters, stop building and publish the analysis. That outcome is still a useful result.

---

## 7. Open questions that block the plan

1. **GPU:** exact model (RTX 3080 10GB?), driver version, PCIe slot width (x16 or x8, CPU or chipset lanes), Resizable BAR on/off, and whether a display is attached (is 9.5 GiB "usable" with the desktop running?).
2. **OS:** Linux distro and kernel version (io_uring needs ≥ 5.10, prefer 6.x). Windows or WSL2 would change the I/O and pinned-memory design; WSL2 is not recommended.
3. **CPU:** model, physical cores, AVX2 / AVX-512 / AVX-VNNI.
4. **RAM:** DDR4 or DDR5, speed, and channel config (2×8 GB vs 1×16 GB; single channel would roughly halve DRAM bandwidth and likely kill the CPU path).
5. **NVMe:** model and free space. Need ~100 GB: checkpoint 20 GB, GGUF 16 GB, pack 15 GB, 1M KV 10–14 GB, dumps/traces ~30 GB.
6. **Memory caps:** RSS ≤ 13 GB or 12.5 GiB? I used 12.5 GiB as the hard cap.
7. **Scope:** text-only acceptable (drop vision tower and DSpark drafter)? Is 1M reached by one long prefill or by accumulating context over a session? Any prefill-time target?
8. **Quality bars:** acceptable to requantize layers 20–23 from MXFP8 to NVFP4 (saves 1.8 GiB)? Maximum acceptable RULER/MRCR drop for KV quantization and sparsity?
9. **Reference code:** is official HF modeling code available (trust_remote_code), or only vLLM and llama.cpp implementations? I have not read any of them; T09 depends on this.
10. **Evaluation:** are RULER, MRCR v2, and LongBench v2 sufficient, or do you have a private long-context set? Is a small 1M eval (~20–40 queries) acceptable given ~30–40 min per prefill?
11. **Sampling:** greedy for evals, or the model's default temperature 1.0?

---

## 8. Start here: week 1 checklist

**Before day 1**
- [ ] Answer §7 questions 1–6 (hardware and OS). If not on Linux, install Ubuntu 24.04 on bare metal.
- [ ] Confirm ~100 GB free NVMe space.

**Day 1**
- [ ] T01: repo skeleton and `setup.sh`; record health JSON in `docs/measurements.md`.
- [ ] Start downloads in the background: official checkpoint (`thinkingmachines/Inkling-2-Mini`, ~20 GB, pin the revision) and a base-model Q4_K_S GGUF (not an abliterated variant).
- [ ] T02: config + visit schedule (tests encode the global visit set {5,11,17,23,29,35,41,47}).

**Day 2**
- [ ] T03: arenas + pinned-limit probe (answers R6 early).
- [ ] T04, T05, T06: microbenchmarks. Compare against the Phase 0 thresholds.

**Day 3**
- [ ] T07: llama.cpp baseline at 4K and 32K under the 12.5 GiB cgroup.
- [ ] T08: analytic model; re-run §0.5 with measured link numbers.
- [ ] Start T09 (model-spec) in parallel; it is on the critical path for everything numeric.

**Day 4**
- [ ] T10: libllama trace tool, `--list-tensors` first to map llama.cpp names to visits.
- [ ] T12 part (a): tokenize ≥ 50K tokens per domain for traces.

**Day 5**
- [ ] Collect first routing traces (≥ 50K tokens).
- [ ] T13 quick pass: loop overlap o, popularity skew, temporal overlap, union(n=4).
- [ ] Write a one-page decision memo: measured hardware vs §0.3, Phase 0 gate result, first read on G1 (hit rate) and G4 (spec decode), and any change to §3.7 budgets.

**End-of-week decision:** proceed to the rest of Phase 1 only if the Phase 0 gate passes and the re-run analytic base case is ≥ 8 tok/s at 1M.
