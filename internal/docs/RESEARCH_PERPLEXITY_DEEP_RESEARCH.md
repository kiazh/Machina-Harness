# Perplexity Deep Research — a source-grounded product and capability review

**Prepared:** 2026-11-24  
**Scope:** Perplexity's consumer Deep Research mode, its 2026 “Computer” incarnation and Search-as-Code architecture, plan/quota limits, API access and cost, benchmarks, and known risks.  
**Method:** Primary sources first (Perplexity blog, Help Center, product pages, API docs, court records) with secondary reporting used only where Perplexity does not publish numbers; every claim is cited and dated. Numbers that Perplexity does not disclose are marked as secondary or unknown rather than estimated.

---

## 1. Executive summary

- **What it is.** Deep Research is Perplexity's agentic, multi-step research mode. Instead of one search and one answer, it plans, runs many searches, reads sources, reasons about what to look for next, and writes a cited report. At launch Perplexity described “dozens of searches” and “hundreds of sources” in 2–4 minutes [1].
- **How it has changed.** The 2025 product was a standalone mode in the Perplexity search box. By February 2026 it ran on frontier reasoning models (Anthropic Opus 4.5 at launch of the upgrade, with Opus 4.6 appearing in later benchmark runs) and was measured on new benchmarks [5][13]. By June 2026 it became a skill inside **Perplexity Computer**, built on a **Search as Code** architecture that writes and runs retrieval programs with thousands of parallel steps and routes subtasks across 20+ models [3][4].
- **Access and limits.** Deep Research is included with Pro, Max, Enterprise Pro and Enterprise Max. Consumer Pro/Max limits are monthly, unpublished, and recover gradually; Enterprise publishes 50 (Pro) and 500 (Max) research queries per month [2][14]. At launch it was also available free with a small daily allowance [1]; by late 2026 the Help Center lists only paid plans, and third-party trackers report Free at one research query per month [2][14].
- **API.** The original `sonar-deep-research` model remains documented as legacy: 128K context, reasoning-effort control, and metered pricing (tokens + citations + reasoning + searches) [7][9]. Asynchronous Sonar requests are deprecated; long runs now use **background mode** on the Agent API (`POST /v1/agent`) with polling, streaming, reconnect and cancel [8].
- **Evidence.** Perplexity's own launch numbers were 21.1% on Humanity's Last Exam and 93.9% on SimpleQA [1]. In February 2026 it reported state-of-the-art results on DRACO (67.15% at Opus 4.5, 70.5% at Opus 4.6), DeepSearchQA (79.5%) and Scale ResearchRubrics, ahead of Gemini Deep Research and OpenAI Deep Research in those runs [5][6][13]. These are largely vendor-run and the benchmark is Perplexity-authored, so treat the ranking as directional, not independent [13].
- **Main risks.** Opaque and reduced consumer quotas; citation and paywall disputes that are now in active litigation; a benchmark suite whose absolute scores move 10–25 points with the judge model; and API churn as Sonar gives way to the Agent API [8][11][13][14][15].

---

## 2. What Deep Research actually does

Perplexity's own definition is consistent across the launch post and the current Help Center:

1. **Plan and search iteratively.** The agent searches, reads documents, and decides what to search next, refining its plan as it learns. It is not a single retrieval pass [1][2].
2. **Reason over the collected material.** It evaluates whether the evidence is sufficient, notes conflicts, and then synthesizes [1][3].
3. **Write a report.** The output is a structured report with inline numbered citations, not a short answer with links [3][4].
4. **Hand off the artifact.** Reports can be exported to PDF/DOCX (and, when generated as Markdown, downloaded as Markdown), or shared as a Perplexity Page. In Computer the result can be turned into a deck, dashboard, spreadsheet or site in the same session [1][2][3].

At launch Perplexity positioned it for expert-level work products — finance, marketing, product research, health, travel planning — and claimed a typical run of 2–4 minutes [1]. The current product page describes a run as breaking a complex question into subtasks and executing “thousands of retrieval steps across hundreds of sources” [4].

### What distinguishes it from normal Search

| Normal Search | Deep Research |
| --- | --- |
| One query, ranked sources, short synthesized answer | Multi-step plan, many queries, source reading, conflict handling, long cited report |
| Seconds | Minutes (launch: 2–4 minutes) [1] |
| Optimized for a fact or a quick overview | Optimized for a work artifact: memo, comparison, due-diligence brief, literature review [2][4] |
| Citations support a paragraph | Every factual claim carries an inline citation in Computer [3] |

---

## 3. Timeline: from a search mode to a Computer skill

| Date | Change | Why it matters |
| --- | --- | --- |
| **2025-02-14** | Deep Research launches on web (mobile/desktop “soon”), free with daily limits, Pro with high volume. Claims 21.1% HLE, 93.9% SimpleQA, 2–4 minute runs, PDF/DOCX/Page export [1]. | Establishes the product category and the “dozens of searches, hundreds of sources” framing. |
| **2025-05/06** | `sonar-deep-research` API gets asynchronous mode and `reasoning_effort` (`low`/`medium`/`high`); academic search mode and richer citations added [7][10]. | Moves Deep Research from UI-only to a programmable, metered API. |
| **2026-02-05** | “Upgraded Deep Research”: SOTA claims on Google DeepMind DeepSearchQA and Scale ResearchRubrics; runs on Opus 4.5 for Max and Pro; DRACO benchmark open-sourced [5][6]. | Frontier-model reasoning replaces the earlier stack; Perplexity starts publishing its own evaluation suite. |
| **2026-02-12 (paper run)** | DRACO paper evaluates shipped products; Perplexity Deep Research scores 67.2 (Opus 4.5) and 70.5 (Opus 4.6) vs Gemini Deep Research 59.0 and OpenAI o3 Deep Research 52.1 under the Gemini-3-Pro judge [13]. | First detailed, rubric-based public comparison — with important caveats (Section 7). |
| **2026-06-11** | Deep Research becomes a skill in **Computer**, built on **Search as Code**: the model writes retrieval programs, runs thousands of steps in parallel in a sandbox, pulls internal files/connectors and premium sources (Statista, PitchBook, CB Insights), and routes subtasks across 20+ frontier models [3][4]. | The product shifts from “a report” to “research plus deliverables” inside one agent session. |
| **2026 (current)** | Help Center describes Deep Research and Model Council as Computer skills for Pro/Max on web; included usage first, credits after; model/effort choices consume credits [10]. | Confirms the Computer-first direction and a credits-based overage model. |

---

## 4. Architecture and internals (as publicly described)

### 4.1 The 2025–early-2026 loop

Perplexity describes a reasoning loop equipped with search and coding capabilities. It iteratively searches, reads documents, reasons about the next action, and refines its research plan; once the material is evaluated, it synthesizes the report [1]. The `sonar-deep-research` API exposes this as a single request with a 128K context window, optional `reasoning_effort`, and usage metadata that reports `num_search_queries`, `reasoning_tokens`, `citation_tokens` and a cost breakdown [7][9].

A sample response in Perplexity's docs shows 21 search queries, ~194k reasoning tokens, ~19k citation tokens and a total cost of about **$0.816** for one request [7]. That is a useful order-of-magnitude anchor: a single deep research run is not a cheap search call.

### 4.2 Search as Code (2026)

The June 2026 announcement changes the mechanism rather than just the model:

- The model **writes code that designs and runs retrieval**, instead of issuing one query at a time.
- The code runs in a sandbox next to the model, which can observe results as they arrive, track which sources scored well, and change course mid-search [3].
- A run can issue hundreds to thousands of targeted retrievals in parallel, dedupe/join/filter results in code before the model reads them, and combine live web with authorized Connectors to internal files and apps [3].
- Subtasks are routed across 20+ frontier models, and premium data sources are pulled when warranted [3][4].

This is the most important architectural claim to evaluate: it is a shift from “agent with a search tool” to “agent that programs its own retrieval pipeline.” Perplexity says the move improved factual accuracy, depth and citation quality on Humanity's Last Exam, BrowseComp and DeepSearchQA, but the blog presents the gains as charts without tabulated numbers [3].

### 4.3 Models

Perplexity does not expose a fixed model for Deep Research in the consumer product; the system selects models for the user [2]. In February 2026 the upgrade ran on **Anthropic Opus 4.5** for Max and Pro, with a stated intent to adopt newer top reasoning models [5]. The DRACO paper's February 12 run also includes **Opus 4.6**, indicating the harness moved quickly to a newer model [13]. In Computer, choosing a model yourself or changing effort in a follow-up consumes credits instead of included usage [10].

---

## 5. Plans, quotas and practical access

### 5.1 Plan inclusion

Deep Research is included on **Pro, Max, Enterprise Pro and Enterprise Max** [2]. At launch Perplexity also made it available to non-subscribers with a limited number of answers per day [1]. The current Help Center article no longer lists Free; third-party trackers report Free at **1 research query per month** [14]. Treat the Free entitlement as changed/uncertain and verify in-app.

### 5.2 Prices (US, web)

| Plan | Monthly (annual billing) | Monthly (month-to-month) | Deep Research entitlement |
| --- | --- | --- | --- |
| Free | $0 | $0 | Not listed by Help Center; third-party trackers: 1 research query/month [2][14] |
| Pro | $17/mo billed annually | $20/mo | Included; monthly limit described only as “average use” [2][11][14] |
| Max | $167/mo billed annually | $200/mo | Included; higher “advanced use” monthly limit; frontier models; 10,000 monthly Computer credits [2][11][14] |
| Enterprise Pro | — | ~$40/seat/mo (secondary) | 50 research queries/month/seat (secondary) [14] |
| Enterprise Max | — | ~$325/seat/mo (secondary) | 500 research queries/month/seat (secondary) [14] |

Perplexity's pricing page lists the annual-billing figures and the Max credit allowances; the month-to-month and enterprise numbers come from secondary trackers because Perplexity does not print them on that page [11][14].

### 5.3 How limits behave

- Consumer limits are **monthly** and **not numerically published**; Perplexity describes Pro as sized for “average use” and Max for “advanced use” [2][14].
- The limit **does not reset on a single date**. It recovers gradually, and renewing the subscription does not reset it [2].
- Hitting the limit does not block other search modes; Pro users are told to upgrade to Max for more, and Computer users may continue with credits once included usage runs out [2][10].
- In Computer, selecting a model manually or changing effort in a follow-up uses **credits**, not included usage [10].
- **Quota controversy:** After the February 2026 upgrade, reporting documented Pro users dropping from hundreds of Deep Research queries per day to roughly 20 per month (some accounts reportedly 4), with file uploads and Labs quotas also cut. Perplexity framed the change as allocating more compute per session; it does not publish exact consumer caps [5][15]. This is a secondary report, but it is consistent with the Help Center's refusal to state numbers and with the shift to credits.

**Practical implication:** budget Deep Research as a scarce, high-value mode, not a default search. For repeated or programmatic use, the API's metered pricing is more predictable than consumer quotas (Section 6).

---

## 6. API access, pricing and the Sonar → Agent migration

### 6.1 Legacy `sonar-deep-research`

The model is still documented but explicitly marked **legacy, superseded by the Agent API** [7].

| Component | Price |
| --- | --- |
| Input tokens | $2 / 1M |
| Output tokens | $8 / 1M |
| Citation tokens | $2 / 1M |
| Reasoning tokens | $3 / 1M |
| Search queries | $5 / 1K requests |

- 128K context; `reasoning_effort` = `minimal`/`low`/`medium`/`high` (docs disagree slightly on whether `minimal` is accepted; the model page lists low/medium/high, the API schema includes minimal) [7][9].
- The sample cost breakdown totals **$0.816** for 21 queries and ~194k reasoning tokens [7].
- **Asynchronous Sonar requests are no longer supported.** Perplexity directs long runs to the Agent API's background mode [7][8].

### 6.2 Agent API background mode (current path)

For deep-research-style work the Agent API provides the async mechanics the old async Sonar endpoint used to:

- Submit `POST /v1/agent` with `background: true` (and optionally `stream: true`).
- Poll `GET /v1/agent/{id}` until `completed`, `failed`, `cancelled` or `incomplete`.
- Reconnect a dropped stream with `starting_after=<sequence_number>` within the reconnect window, or fall back to a final snapshot.
- Cancel with `POST /v1/agent/{id}/cancel` [8].

Pricing is per-token at the chosen model's rates **plus tool fees**: `web_search` at $0.0025/call ($0.001 fast), `fetch_url` $0.0005, `image_search` $0.0025, `people_search`/`finance_search` $0.005, and `sandbox` at $0.03 per started 20-minute block plus $0.001 per sandbox SDK web search [9]. A deep run therefore has three cost drivers: model tokens (including reasoning), tool calls, and sandbox time. Perplexity warns that outcomes vary with effort and tool usage and that `usage.cost.total_cost` is an estimate [9].

**Compatibility note:** Anyone who built on `sonar-deep-research` async endpoints must migrate; the docs describe this explicitly [7]. This is a real integration risk, not a hypothetical.

---

## 7. Benchmarks: what the numbers do and do not prove

### 7.1 Launch numbers (February 2025)

- **Humanity's Last Exam: 21.1%** — Perplexity compared this favorably to Gemini Thinking, o3-mini, o1, DeepSeek-R1 and others [1].
- **SimpleQA: 93.9%** — short-form factuality [1].
- **Latency:** most research tasks under 3 minutes [1].

**Reading these correctly:** 21.1% on HLE is state-of-the-art-ish for its time but low in absolute terms — HLE is designed to be extremely hard. 93.9% on SimpleQA says the system is good at isolated fact questions; it says little about synthesis quality, citation faithfulness or whether a report is decision-useful. Neither benchmark measures the thing buyers care about most: whether the report's conclusions follow from its sources.

### 7.2 DRACO (February 2026)

Perplexity open-sourced **DRACO** (Deep Research Accuracy, Completeness, and Objectivity): 100 tasks across 10 domains, expert rubrics averaging ~40 criteria, ~half of criteria about factual accuracy; negative criteria penalize hallucinations and unsupported claims; graded by LLM judges [6].

Reported results (vendor-run):

| System | DRACO score |
| --- | --- |
| Perplexity Deep Research (Opus 4.6) | 70.5 |
| Perplexity Deep Research (Opus 4.5) | 67.2 |
| Gemini Deep Research | 59.0 |
| OpenAI Deep Research (o3) | 52.1 |
| Claude Opus 4.6 + web search + code exec | 59.8 |
| OpenAI Deep Research (o4-mini) | 41.9 |

Per-domain strengths reported: Law 89.4% pass rate, Academic 82.4%; largest gaps in Personalized Assistant and Needle-in-a-Haystack [6]. Perplexity also reports citation quality 76% vs 60.4% for o3 and a 79.5% DeepSearchQA score [13].

**Caveats that matter:**

- Perplexity **authored the benchmark and ran the comparison**, and the analysis notes task difficulty was tuned against its own product [13].
- Judge choice moves absolute scores by **10–25 points**, even though the ranking held across Gemini-3-Pro, GPT-5.2 and Sonnet-4.5 judges [13].
- The paper compares **products as shipped**, not models under controlled settings; OpenAI/Google tool settings and reasoning effort are undisclosed [13].
- DRACO is English-only and single-turn in its current version [6].

So: the direction (Perplexity ahead of Gemini and OpenAI deep research in these runs) is plausible and consistent across judges, but the exact numbers should not be treated as neutral third-party measurements.

### 7.3 Other independent signal

Google DeepMind's DeepSearchQA paper reports Gemini Deep Research Agent at 81.9 F1 and o3 Deep Research at 66.45 F1 on its 900-prompt benchmark, with the benchmark explicitly measuring completeness and precision of exhaustive answer sets [16]. Perplexity's claimed 79.5% on DeepSearchQA is in the same range as Gemini's independently reported F1, which makes the “SOTA” claim credible but not obviously dominant. Again, harness and judge differences prevent an apples-to-apples read.

---

## 8. Strengths

1. **Depth that is real, not cosmetic.** Multi-step planning, source reading and conflict handling are described consistently by Perplexity and visible in the API's query/reasoning usage [1][2][7].
2. **Citations are first-class.** Every claim in the Computer version carries an inline numbered citation to a live URL [3]. Citation quality is also where Perplexity claims the largest margin over o3 in DRACO (76% vs 60.4%) [13].
3. **Work-product output.** PDF/DOCX/Markdown export, Pages, and (in Computer) decks/dashboards/sites in the same session remove the “research then copy into another tool” step [1][2][3].
4. **Model-agnostic harness.** Perplexity has moved from one stack to Opus 4.5/4.6 and routes across 20+ models in Computer; the harness, not a single model, is the product [3][5][13].
5. **Programmable via API.** `reasoning_effort`, background mode, streaming/reconnect and explicit usage/cost data make it operable in production — with the caveat that the API surface is changing [7][8][9].
6. **Competitive pricing for the consumer tier.** At $20/mo (or $17 annual), Pro includes Deep Research plus frontier model access; the marginal cost of a run is effectively zero within quota [11].

## 9. Limitations and risks

1. **Quota opacity and reduction.** Consumer Pro/Max limits are unpublished and recover gradually; the February 2026 upgrade coincided with reports of a >99% cut in Pro allowances [2][15]. You cannot reliably plan capacity around “Deep Research per month” on Pro.
2. **Paywall and copyright exposure.** Dow Jones/NYP Holdings sued Perplexity in October 2024 over retrieval-index copying, output reproduction and trademark misuse; the motion to dismiss/transfer was denied in August 2025, and the case is in discovery with a motion to compel granted in October 2026. No merits ruling yet [12]. Deep Research's heavy source reading makes this more than a Search-only issue, even though the complaint targets the answer engine broadly.
3. **Benchmark self-reporting.** DRACO is Perplexity's own; judge variance is large; shipped-product comparisons hide tool/effort settings [13].
4. **Citation faithfulness is not guaranteed by presence of citations.** DRACO's negative criteria explicitly penalize unsupported claims, which implies the failure mode exists. Independent audits of AI search citation accuracy have repeatedly found mismatches; users should spot-check claims that carry decisions, not just count footnotes.
5. **Product fragmentation.** The “new experience” is Pro/Max **on web**, not yet in desktop/mobile; behavior differs by surface [10]. Naming also drifts (“Research” vs “Deep Research”; Help Center slug is `what-is-research-mode`).
6. **API churn.** Async Sonar is gone; Sonar Deep Research is legacy; the Agent API is the path forward. Migration is mandatory for long-running integrations [7][8].
7. **Cost at scale.** A single legacy-model run can cost ~$0.82; Agent API runs add tool and sandbox fees. Heavy use is materially more expensive than search [7][9].
8. **English/single-turn benchmark scope.** DRACO currently evaluates English, single-turn tasks; multi-turn and multilingual quality are unmeasured [6].

---

## 10. How it compares

| Dimension | Perplexity Deep Research | OpenAI Deep Research | Gemini Deep Research |
| --- | --- | --- | --- |
| Positioning | Agentic research inside a search/Computer product; report + deliverable | Agentic research inside ChatGPT; long-running report | Agentic research in Gemini; multi-step browsing |
| Consumer access | Pro/Max (Free uncertain/limited) [2][14] | Paid ChatGPT tiers (not assessed here) | Google AI plans (not assessed here) |
| Published head-to-head | DRACO 67.2–70.5, DeepSearchQA 79.5 (vendor-run) [6][13] | DRACO 52.1 (o3) in Perplexity's run [13]; DeepSearchQA 66.45 F1 (independent paper) [16] | DRACO 59.0 in Perplexity's run [13]; DeepSearchQA 81.9 F1 (independent paper) [16] |
| Differentiator | Citations + search index + Computer deliverables + Search-as-Code parallel retrieval [3][4] | Deep OpenAI integration and reasoning models | Google search/index and Gemini ecosystem |
| Main weakness | Quota opacity, litigation, self-reported benchmarks [12][13][15] | Less transparent tool settings in third-party comparisons [13] | Less transparent tool settings in third-party comparisons [13] |

**Verdict on comparisons:** On Perplexity's own DRACO, Perplexity leads. On Google's independently published DeepSearchQA F1, Gemini's agent is at least competitive. The honest summary is that the top three are close enough that task type, source access, latency and price should decide — not a single leaderboard.

---

## 11. Practical guidance

### When Deep Research is the right tool

- The answer requires **many sources and synthesis** (competitive landscape, literature review, due diligence, policy comparison) [2].
- You need a **cited artifact** you can hand to someone else, not a chat answer [3].
- The question is **decomposable** into sub-questions and benefits from parallel retrieval [3].
- You can afford minutes, not seconds [1].

### When it is the wrong tool

- A single fact, definition, or current price → normal Search is faster and cheaper [2].
- A decision that hinges on one proprietary document → upload it and ask directly, or use a connector; broad web research adds noise.
- You need guaranteed throughput → use the API with explicit budget, not consumer quotas [8][9].

### Prompt patterns that work with this architecture

1. **State the deliverable and the decision it supports.** “A 2-page risk memo for an investment committee” beats “research X.”
2. **Define scope and exclusions.** Geography, time window, source types, and what to ignore reduce wandering.
3. **Ask for a comparison table plus a short narrative.** Tables force entity resolution; narratives force synthesis.
4. **Require primary sources and dates.** “Prefer filings, official statistics and peer-reviewed papers; flag undated claims.”
5. **Ask it to surface conflicts and unknowns.** A good report says where sources disagree and what it could not verify.
6. **Request a verification pass.** “List the five claims most likely to be wrong and the source that would settle each.”

### Verification workflow

1. Open every citation that supports a decision-relevant number.
2. Check that the cited page actually contains the claim (citation presence ≠ citation faithfulness).
3. Prefer primary data for financial, legal and medical claims.
4. Re-run a narrow sub-question in normal Search to see whether the conclusion survives a different retrieval path.
5. Date-stamp the report; Deep Research is only as current as its retrieval.

---

## 12. Bottom line

Perplexity Deep Research is a genuinely agentic research system, not a marketing label on search. Its 2026 form — frontier reasoning models plus Search-as-Code parallel retrieval inside Computer — is the most ambitious consumer research architecture of the three major products, and its citation-first, deliverable-oriented workflow is a real differentiator. The evidence for quality is favorable but not neutral: Perplexity's own DRACO and DeepSearchQA numbers put it ahead, while independent DeepSearchQA results show Gemini at least competitive and judge variance is large. The practical constraints are equally real: unpublished and recently reduced consumer quotas, active copyright litigation over the retrieval/output pipeline, a web-only Computer experience, and an API in migration. Use it for high-value synthesis with a verification pass; do not treat its citations as proof or its quotas as capacity.

---

## Sources

Primary sources are marked **[P]**; secondary reporting or analysis **[S]**.

1. **[P]** Perplexity, “Introducing Perplexity Deep Research,” 2025-02-14. https://www.perplexity.ai/hub/blog/introducing-perplexity-deep-research
2. **[P]** Perplexity Help Center, “What is Deep Research?” (accessed 2026-11-24). https://www.perplexity.ai/help-center/en/articles/10738684-what-is-research-mode
3. **[P]** Perplexity, “Deep Research, now in Computer,” 2026-06-11. https://www.perplexity.ai/hub/blog/deep-research-now-in-computer
4. **[P]** Perplexity, “Deep Research built for the hardest questions” product page (accessed 2026-11-24). https://www.perplexity.ai/hub/products/deep-research
5. **[P]** Perplexity Changelog, “Upgraded Deep Research, Model Council, and a simplified input bar,” 2026-02-05. https://www.perplexity.ai/changelog/what-we-shipped---february-6th-2026
6. **[P]** Perplexity, “Evaluating Deep Research performance in the wild with the DRACO benchmark,” 2026-02-04. https://www.perplexity.ai/hub/blog/evaluating-deep-research-performance-in-the-wild-with-the-draco-benchmark
7. **[P]** Perplexity API docs, “Sonar Deep Research (Legacy — Superseded by Agent API).” https://docs.perplexity.ai/docs/legacy/sonar/models/sonar-deep-research
8. **[P]** Perplexity API docs, “Background Mode.” https://docs.perplexity.ai/docs/agent-api/background-mode
9. **[P]** Perplexity API docs, “Pricing.” https://docs.perplexity.ai/docs/getting-started/pricing
10. **[P]** Perplexity Help Center, “Deep Research and Model Council in Computer.” https://www.perplexity.ai/help-center/en/articles/2026102603-deep-research-and-model-council-in-computer
11. **[P]** Perplexity, “Pricing” page (accessed 2026-11-24). https://www.perplexity.ai/hub/pricing
12. **[P]** Court record and reporting on *Dow Jones & Company, Inc. et al v. Perplexity AI, Inc.*, S.D.N.Y. 1:24-cv-07984: complaint 2024-10-21; motion to dismiss/transfer denied 2025-08-21 (Reuters); discovery order 2026-10-29 (Justia docket 227); case summary (DataPacts, checked 2026-11-10). https://docs.justia.com/cases/federal/district-courts/new-york/nysdce/1:2024cv07984/630270/227 · https://www.reuters.com/legal/litigation/perplexity-ai-loses-bid-dismiss-or-transfer-news-corp-copyright-case-2025-08-21/ · https://datapacts.com/cases/dow-jones-perplexity/
13. **[S]** Analysis of the DRACO paper and leaderboard, including judge-variance and self-authorship caveats (Klu, 2026-11-11). https://klu.ai/benchmarks/draco-research · secondary coverage of the 67.15/79.5 numbers: https://aihola.com/article/perplexity-deep-research-draco-benchmark
14. **[S]** Plan-limit trackers summarizing Perplexity's Help Center and enterprise allowances (ToolColumn, updated 2026-11-21; DataStudios, 2026-11-13). https://www.toolcolumn.com/learn/perplexity-pro-search-limits-explained · https://www.datastudios.org/post/perplexity-pro-vs-perplexity-max-comparison
15. **[S]** Reporting on the February 2026 Pro quota reduction (MakeUseOf, 2026-02-21). https://www.makeuseof.com/bought-annual-perplexity-subscription-lied/
16. **[P]** Google DeepMind, “DeepSearchQA: Bridging the Comprehensiveness Gap for Deep Research Agents,” arXiv:2601.20975, 2026-01-28. https://arxiv.org/html/2601.20975

---

## Open questions to verify in-product

1. Does Free still include any Deep Research queries in your region/account, and what is the current allowance? (Help Center no longer lists Free; trackers say 1/month.)
2. What is the actual Pro/Max monthly cap today? (Unpublished; in-app usage meter is the only reliable source.)
3. Which model served a given Computer run, and how many credits did it consume? (Model/effort choices are credit-metered; the UI exposes usage but the mapping is not documented in detail.)
4. Is the “Deep Research” skill available on desktop/mobile in your plan yet? (Help Center says web-only for the new experience.)
5. For API work: are you on the Agent API yet, and have you budgeted tool + sandbox fees rather than tokens alone?
