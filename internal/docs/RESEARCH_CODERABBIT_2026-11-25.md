# CodeRabbit — product architecture, hosted vs. prompt layers, and what Machina can reproduce

**Prepared:** 2026-11-25  
**Scope:** CodeRabbit's current (late-2026) product surface, PR review lifecycle, configuration/policy layer, hosted infrastructure, permissions, quotas, CLI/IDE/API/MCP, security and agentic features; what a local harness can reproduce; and a recommended Machina architecture.  
**Method:** Primary sources only: CodeRabbit's own documentation site, its public skills repository, and its API/CLI docs. No hands-on benchmark was run. Claims that CodeRabbit does not document are marked **Unknown** or **Inferred** rather than presented as fact. All URLs were accessed 2026-11-25.  
**Important framing:** CodeRabbit publishes a high-level architecture page and detailed feature/config docs, but not its internal queue schema, prompt text, model routing, indexing pipeline, or state database. Those parts are inferred from observable behavior (status states, rate-limit semantics, webhook/permission docs) and are labelled accordingly.

---

## 1. Executive summary

- **CodeRabbit is a hosted, event-driven review service with a thin local client layer — not a prompt pack.** A Git event enters through a GitHub App / GitLab webhook / Azure DevOps service hook / Bitbucket webhook, is queued, and a sandboxed worker clones the repository, resolves configuration and context, runs 50+ static tools plus multiple model-backed agents, then publishes a PR summary, a walkthrough comment, inline review comments, and a `CodeRabbit` check/status [1][2][8][9][12][13].
- **The product has expanded well beyond "AI PR review."** In 2026 the same platform also markets Triage (cross-repo PR queue), Explain (structured diff workspace), Verify (run the app in a sandbox and test journeys), Secure (PR security review + repository Deep Scan), Coding Agent (cloud implementation tasks), Plan/Issue Planner, Slack/Discord agents, and Automations [1][24][26][27][28][29][30][31][32].
- **The prompt/policy layer is real but is the minority of the system.** `.coderabbit.yaml` V2/V3, path instructions, instruction documents, code guidelines, learnings, AST rules, custom checks, review profiles, tools config, and finishing-touch recipes are all portable, inspectable policy [14][15][16][17]. They steer a review; they do not create the event loop, repo context, state, posting, identity, or billing.
- **Most of CodeRabbit's differentiation is hosted infrastructure**: webhook ingress and signature verification, installation-token minting, queueing and superseding, sandboxed full-repo clones, repository indexing/caching, per-PR review state, finding dedupe, threaded comment posting, check-run publication, multi-platform identity, seat/billing enforcement, and audit/compliance [1][9][11][12][13][18][19][20].
- **CodeRabbit itself ships "skills" — and they are proof that a skill is an interface, not the engine.** Its MIT-licensed `code-review`, `autofix`, and `coderabbit-config` skills check CLI auth, invoke the installed `coderabbit` CLI, parse `--agent` NDJSON, and preserve severity; the actual review is performed by CodeRabbit's API, and the skill explicitly says the CLI sends diffs to that API [22][23].
- **"CodeRabbit can just be a skill" is therefore imprecise.** It can be *triggered* by a skill, and a skill can reproduce the invocation/UX/policy layer. It cannot, by itself, reproduce the event source, repo-wide context, incremental state, dedupe, comment posting, checks, identity, quotas, or compliance surface.
- **Machina can capture most of the practical value for a single repository without rebuilding a SaaS.** The highest-leverage design is: a local-first `pr-review` skill + an isolated read-only reviewer subagent + deterministic evidence tools + a GitHub Action for team enforcement. Add a webhook service only when multi-repo, multi-platform, or central policy/audit requirements justify it.
- **Recommended split:** keep review execution in the developer's harness or an ephemeral CI runner; keep the hosted surface to event ingress, short-lived auth, idempotency/state, and publishing. That preserves Machina's local-first, permission-brokered model and avoids operating a review SaaS.

---

## 2. Current feature matrix

### 2.1 Surfaces and integration

| Surface | What CodeRabbit does | Where it runs | Source |
|---|---|---|---|
| GitHub.com / GitHub Enterprise Server | GitHub App + OAuth login; PR reviews, check runs, merge-queue check, security check, coding-agent commits/PRs | Hosted control plane; self-hosted container for Enterprise 500+ seats | [12][13][20] |
| GitLab.com / self-managed GitLab | Service account + PAT/group token; webhook installed automatically; MR reviews, discussions, commit statuses | Hosted; self-hosted for Enterprise | [19][33] |
| Azure DevOps Services/Server | PAT or Entra service principal; service hooks; PR reviews and Git commit status | Hosted; self-hosted for Enterprise | [19][20] |
| Bitbucket Cloud / Data Center | Service account + scoped Atlassian API token; webhooks; build status | Hosted; self-hosted for Enterprise | [19][20] |
| CLI | Local review of tracked/committed/uncommitted changes, base branch/commit, directory scope, remote review without checkout, `--agent` NDJSON, `--deep`, `--show-prompts`, stored findings | Developer machine / CI runner; inference via CodeRabbit API | [21][25] |
| IDE | VS Code, Cursor, Windsurf, Antigravity, JetBrains; review local changes, findings sidebar, "Fix with AI" handoff to Copilot/Cursor/Claude Code/Codex/opencode/Cline/etc. | Editor extension + CodeRabbit API | [21] |
| Chat | `@coderabbitai` in PR/MR comments; Slack agent; Discord agent; chat about the change in Explain | Hosted bot / app | [1][6][30] |
| API / MCP | REST for reviews, coding tasks, security scans, config, users, usage; hosted MCP server with OAuth for agents; CLI `coderabbit api` | Hosted API/MCP | [24] |
| Skills / plugins | `code-review`, `autofix`, `coderabbit-config`, `coderabbit-secure`, setup skill; plugins for Claude Code, Cursor, Codex, Gemini CLI, Antigravity; 35+ agents via `npx skills` | Local skill files that call the CLI/MCP | [22][23] |

### 2.2 PR review capabilities

| Capability | Behavior | Source |
|---|---|---|
| Automatic review | Reviews a new PR against the default branch; also configurable base branches, drafts, labels, description keywords, ignored titles/authors | [3] |
| Incremental review | Reviews commits pushed since the last review; `auto_incremental_review` default `true`; pauses after 5 reviewed commits by default (`auto_pause_after_reviewed_commits`) | [2][3] |
| Full review | `@coderabbitai full review` re-reviews every file from scratch | [6] |
| Pause/resume/ignore | `@coderabbitai pause`, `resume`, or `ignore` in description/comment | [3][6] |
| Summary | Plain-language summary appended to the PR description; regenerated on every incremental push; placement via `@coderabbitai summary` placeholder | [4] |
| Walkthrough | Structured top-of-thread comment: changed-files table, sequence diagrams, review effort 1–5, merge risk, related issues/PRs, suggested labels/reviewers, review details, optional poem; updated in place while running | [5] |
| Inline comments | Review comments on specific lines, organized by content category and severity; includes a collapsed `🧰 Tools` section for static-tool findings on covered lines; includes "Prompt for AI Agents" fix instructions | [2][7][8] |
| Severity/category | Critical / Major / Minor / Trivial / Info; categories: Security & Privacy, Stability & Availability, Data Integrity & Integration, Functional Correctness, Performance & Scalability, Maintainability & Code Quality | [2] |
| Status check | `CodeRabbit` check run (GitHub) or commit status (other platforms); states: queued/in progress, finished, skipped, rate limited, failed, cancelled; skipped and rate-limited pass a required check by design | [9] |
| Merge queue | Publishes a successful `CodeRabbit` check on the synthetic merge-group commit so the queue is not blocked; does not re-review it | [9][12] |
| Chat commands | `review`, `full review`, `pause`, `resume`, `resolve`, `approve`, `track`, `fixed`, `fix comments`, `fix-ci`, `resolve merge conflict`, `autofix`, `local commit`, `generate unit tests`, `simplify`, `run <recipe>`, `summary`, `generate sequence diagram`, `plan`, `run pre-merge checks`, `ignore pre-merge checks`, `configuration`, `generate configuration`, `emit path instructions`, `rate limit`, `help`, `generate project vocabulary` | [6] |
| Pre-merge checks | Built-in checks plus natural-language custom checks; can gate approval; `run/ignore/evaluate` commands | [6] |
| Automatic approvals | CodeRabbit can resolve its threads and approve within risk limits and policy; author approval optional | [6] |
| Finishing touches | Fix comments (direct commit or stacked PR), fix CI, resolve merge conflicts, generate unit tests, simplify, custom recipes, evaluate a one-off recipe | [6][7] |
| Autofix | Continuous mode that keeps fixing findings, failing required CI checks, and merge conflicts on the PR | [6][7] |
| Triage | Cross-repository queue ranked by value/risk, with review/repair/reassign/merge/close actions and Slack digests/rules | [29] |
| Explain | Structured review workspace: logical cohorts/layers, range summaries, diagrams, findings, pinned snapshots, chat | [30] |
| Verify | Runs the PR or user journeys against the app in an isolated sandbox; returns bugs/blockers with screenshots/recordings | [31] |
| Coding Agent | Cloud task from a request, finding, plan, or failing CI; delivers a commit or stacked PR; steerable live diff; environments and skills | [28] |
| Plan / Issue Planner | Turns issues/PRDs/designs into coding plans; GitHub, GitLab, Azure Boards, Jira, Linear; agent handoff | [1][28] |
| Slack/Discord agent | Investigate code, plan, create issues, open PRs from chat; automations on schedules/webhooks/messages | [1][32] |

### 2.3 Security and static analysis

| Capability | Behavior | Source |
|---|---|---|
| 50+ third-party tools | Linters, SAST, secret scanners, IaC scanners, CI config analyzers; enabled by default when relevant; run in secure sandboxes; findings are passed to the review model as hints, not posted one-per-tool | [8] |
| Tool examples | ESLint, Biome, Oxlint, Ruff, Pylint, Flake8, golangci-lint, Clippy, RuboCop, SwiftLint, PHPStan, PMD, detekt, Clang-Tidy, Cppcheck, ShellCheck, SQLFluff, Checkov, Trivy, TFLint, Hadolint, actionlint, zizmor, Semgrep, OpenGrep, Betterleaks, TruffleHog, OSV-Scanner, Presidio, markdownlint, Vale, LanguageTool | [8] |
| PR security review | Security issues raised during PR review; PR security reviews dashboard; reachability/exploitability badges | [26] |
| Deep Scan | Repository-level scan of committed source and IaC for exploitable vulnerabilities and exposed secrets; map → investigate → verify; CWE/severity/evidence/reachability/exploitability; SARIF/CSV/JSON/PDF export | [27] |
| Security check | Provider check/status for merge enforcement; default blocking threshold Critical; separate from PR review check | [26][27] |
| Attack surface / delta scans | Maps security-relevant code and coverage; delta scans after merges or schedules; recurring weekly full scans on GitHub | [27] |
| Runtime verification | Reproduction guides and optional isolated runtime runs for Deep Scan findings | [26][27] |
| Security Architecture Review / Blast Radius | Architecture-level security implications and dependency/downstream mapping in Explain | [26] |
| Secret scanning | Betterleaks/TruffleHog for changed files; Deep Scan secret findings; credential redaction in learnings | [8][16][27] |

### 2.4 Context, configuration, and knowledge

| Area | What is available | Source |
|---|---|---|
| Configuration | `.coderabbit.yaml` / `.yml` (V2 default, V3 opt-in on GitHub Cloud), `.coderabbit.config.ts`, repo/org/workspace/central config, global overrides, config branch pinning, `@coderabbitai configuration` resolver | [14] |
| Policy | Review profile (`chill`/`assertive`), tone, language, path filters, path instructions (20k chars each), `.coderabbit/instructions/*.md`, code guidelines, AST rules, custom checks, finishing-touch recipes | [14][15][17] |
| Context | Full repository clone, code graph analysis, relevant files outside the diff, callers/dependencies, past PRs, linked issues, code guidelines (AGENTS.md, CLAUDE.md, `.cursorrules`, Copilot instructions), learnings, multi-repo links, knowledge repositories, MCP servers, web search | [1][2][17][19] |
| Indexing/caching | "Code indexing" stores vector representations for codebase context; reusable sandbox cache of repo + dependencies; encrypted (except OSS), expires ≤7 days, not used for training | [18][19] |
| Learnings | Natural-language preferences learned from chat replies; scoped local/global/auto; optional approval delay 0–30 days; vector search; CSV import/export; credential redaction | [16] |
| Multi-repo | Linked repositories for cross-repo breaking changes; plan limits 1/5/10/20 by tier | [10][17] |

### 2.5 Plans, quotas, and billing

| Plan | Price (USD/dev/mo) | PR / IDE / CLI reviews per hour | Files/review | Chat/hour | Notable inclusions |
|---|---|---|---|---|---|
| Free | $0 | 1 (PR summary only) / 3 / 3 | 150 | N/A | Unlimited public/private repos; CLI/IDE reviews; 14-day Advanced trial |
| OSS | $0 | 1–10 (varies by stars) / 1 / 3 | 100–300 | 25 | Team features for public repos |
| Essentials (was Pro) | $24 annual / $30 monthly | 5 / 5 / 5 | 150 | 50 | PR reviews, fixes, Knowledge Base, tools, analytics |
| Team (was Pro+) | $48 / $60 | 8 / 8 / 8 | 300 | 75 | Triage, Plan, issue planning, unit tests, merge conflicts |
| Advanced | $72 / $90 | 10 / 10 / 10 | 300 | 100 | Continuous PR security review |
| Enterprise | Contact sales | 12 / 12 / 12 | 300 | 100 | Self-hosting, multi-org, SSO, API, RBAC, audit logs |

Sources: [10]. Rate limits are **per developer**, rolling, and adapt downward under the Fair Usage Limits Policy (e.g., Essentials 5→4→3→2→1 reviews/hour as 7-day activity rises; Team 8→1; Advanced 10→1; Enterprise 12→1 for enrolled contracts) [11]. Usage-based reviews can continue eligible over-limit PR/CLI reviews with a spending cap; large-PR overage is capped at 300 files [10][11].

**What counts as a review event:** one per PR evaluation/push, not per commit. Three commits pushed together = 1; three separate pushes = 3. Web-UI edits, "Update branch", "Resolve conflicts", force-push, rebase, reopen, and draft→ready each count. A review superseded by a newer push still consumes its event [11]. Bots/shared accounts consume the single identity's allowance, so seat count does not help bot-driven PR volume [11].

---

## 3. Actual architecture and review lifecycle

### 3.1 Event-to-comment path

The following is the observable lifecycle assembled from CodeRabbit's architecture, platform, status-check, and command docs. Items marked **Inferred** are not explicitly documented but are required by the documented behavior.

1. **Install and authorize.** GitHub: OAuth app for login + GitHub App for repository access and webhooks; org owner selects all/selected repos. GitLab/ADO/Bitbucket: service account/token + webhook/service hook [12][13][19][33].
2. **Event ingress.** GitHub App subscribes to `Meta`, `Issue comment`, `Issues`, `Label`, `Public`, `Pull request`, `Pull request review`, `Pull request review comment`, `Pull request review thread`, `Push`, `Release`; webhook URL is `https://app.coderabbit.ai/githubHandler` with a shared secret. GitLab installs `https://coderabbit.ai/gitlabHandler` with merge-request/comment/issue events. ADO/Bitbucket use service hooks/webhooks [13][19][33].
3. **Verify, map, enqueue.** The service verifies the webhook secret/signature, maps installation → org → repo, resolves the PR/head/base, applies auto-review filters, and enqueues a review. The UI exposes `Review queued` / `Review in progress`; a newer push supersedes an in-flight review [3][9][11].
4. **Provision sandbox and clone.** A worker gets an isolated cloud sandbox, clones the full repository at the relevant commit, restores a reusable repo/dependency cache when available, and begins analysis [1][18].
5. **Resolve policy and context.** Layers are merged (workspace → org defaults → central/remote → repo file → global overrides; branch selection matters), then path filters, instructions, code guidelines, learnings, linked repos, MCP/web context, and issue links are assembled [14][15][16][17].
6. **Run static tools.** Relevant linters/SAST/secret/IaC tools execute in the sandbox; findings become model hints rather than one comment per tool [8].
7. **Run model-backed agents.** CodeRabbit describes specialized agents working in parallel: Review, Verification, Chat, Pre-Merge Checks, and Finishing Touches, plus agentic exploration of the repo. It uses multiple AI models; the FAQ says code is shared with OpenAI and/or Anthropic for review [1][2][19].
8. **Publish.** Update the PR description summary, upsert the walkthrough comment, submit a review with inline comments, post/update the `CodeRabbit` check run or commit status, and optionally resolve stale threads or approve [4][5][9].
9. **Persist state.** Per-PR review state records the last reviewed commit, reviewed-commit count, pause state, tracked/resolved findings, and posted comment IDs. The status-check docs explicitly name a "Review state could not be read" failure, and incremental review only works if this state exists [3][6][9].
10. **Command/chat loop.** `@coderabbitai` comments are parsed (one command per comment, must start a line, not in code blocks); chat messages and commands enqueue new work such as full review, fixes, tests, or configuration changes [6].

### 3.2 What runs where

| Layer | CodeRabbit hosted | Local CLI/IDE | GitHub App / Git provider |
|---|---|---|---|
| Event source | Webhook ingress, queue, retries, supersede | Manual command only | Emits PR/push/comment/review events |
| Credentials | GitHub App private key/webhook secret, OAuth, GitLab/ADO/Bitbucket tokens; installation tokens **Inferred** | User browser/API-key login; own credential store | Installation/authorization UI |
| Context | Full clone, code graph/index, cache, learnings, multi-repo, MCP/web, past PRs | Local working tree + repo config; `--remote` uses server-side repo content | Repo contents via API |
| Static tools | Sandboxed 50+ tool execution | Local tools if installed; not the same matrix | None |
| Models | Multi-model orchestration and prompts | Same backend via API | None |
| State | Per-PR review state, findings, learnings, usage counters | Local findings history/checkpoints; resets on base change | Threads/commits/checks as system of record |
| Publishing | Summary, walkthrough, inline comments, checks, approvals, commits/PRs | Terminal/JSON/Markdown; agent handoff | Hosts comments/checks/merge rules |
| Identity/billing | Seats, roles, SSO, quotas, usage-based billing, audit | User seat/API key attribution | Org/team membership |

### 3.3 Incremental state, dedupe, and comment hygiene

CodeRabbit's incremental behavior is not just "send the last diff to a model." The documented behaviors imply a per-PR state machine:

- **Last reviewed head.** `@coderabbitai review` reviews "commits pushed since CodeRabbit's last review"; a plain review of an already-reviewed commit replies instead of re-running; a failed commit may need `full review` because the failed commit was recorded as reviewed [6][9].
- **Reviewed-commit counter.** Automatic reviews pause after 5 reviewed commits since the last pause; `resume` starts an incremental review of commits pushed while paused [3][6].
- **Supersede, not cancel-and-refund.** A newer push during a run supersedes the result, but the event was already counted [11].
- **Carry-forward.** On GitLab/ADO/Bitbucket Data Center, a skipped commit can copy the previous commit's status with "Status carried forward from previous reviewed commit" [9].
- **Thread state.** Resolved threads are not repeated; `track` keeps a reviewer's thread as a finding checked against later commits (up to 20 per review); `fixed` reports whether it is addressed [6].
- **Comment upsert.** The walkthrough is one comment updated in place (fortune placeholder → full walkthrough); the summary is appended to the description and regenerated each push [4][5].
- **Dedupe of findings.** Not publicly specified. **Inferred:** finding fingerprints or thread identity prevent duplicate comments on unchanged lines; re-reviews focus on new commits and new/changed findings.

### 3.4 Permissions and trust boundary

**GitHub App repository permissions** (documented):
- Read-only: Actions, Discussions, Members, Metadata, Merge queues.
- Read/write: Checks, Code, Commit statuses, Issues, Pull requests, Workflows.
- Workflows write is only needed when an agent edits `.github/workflows/`; a review-only installation does not exercise it.
- Checks write publishes the `CodeRabbit` check and Security check; Commit statuses write is the fallback path [12].

**OAuth login** requests read-only organizations/teams and email [12].

**GitLab** uses a dedicated service account with a PAT (`api`, `read_api`, `read_user`) or a group token; the token must have Developer+ on the selected top-level group. Fine-grained tokens map to MR/repo/commit/pipeline permissions [33].

**Webhook trust:** shared secret, signature verification, documented egress IPs for GitHub IP allowlists (`35.222.179.152/32`, `34.170.211.100/32`, `136.113.208.247/32`) [12][13].

**Data handling:** code is shared with OpenAI/Anthropic for review; no training on customer code; encrypted cache (except OSS) up to 7 days; code indexing stores vector representations; `knowledge_base.opt_out` disables retention-dependent features and can delete stored data on next default-branch merge [18][19].

### 3.5 Billing and quota enforcement

Billing is enforced per developer identity, not per org. The entitlement model combines:
- seat assignment and plan,
- rolling hourly PR/IDE/CLI review allowances,
- per-review file caps,
- adaptive fair-use refill rates,
- optional usage-based credits with a spending cap,
- plan-gated features (Triage/Team, Security/Advanced, self-host/Enterprise),
- separate metering for Deep Scan credits and Coding Agent minutes [10][11][28].

This is a control plane concern. A local skill can enforce a token budget, but it cannot enforce seats, identity, plan entitlements, or cross-tenant quota without a service and a database.

---

## 4. Prompt/policy layer vs. hosted infrastructure

### 4.1 Portable policy and instructions

These are the parts Machina can copy or interoperate with directly:

| Layer | Examples | Portability |
|---|---|---|
| Review behavior | `profile: chill/assertive`, tone, language, path filters, auto-review filters, incremental/pause settings | High — plain config semantics |
| Instructions | `reviews.path_instructions`, `.coderabbit/instructions/*.md`, `AGENTS.md`/`CLAUDE.md`/`.cursorrules` | High — Markdown/YAML |
| Checks | Built-in pre-merge checks, custom natural-language checks, AST rules | Medium — check semantics need an executor |
| Tools | Enable/disable linters, config file paths, profiles | Medium — depends on local tool installation |
| Learnings | Natural-language review preferences, scoped local/global, approval delay | Medium — storage/retrieval is hosted; text is portable |
| Finishing touches | Named recipes/instructions for fixes, tests, simplification | Medium — execution needs sandbox + write authority |
| Output policy | Summary instructions, title instructions, labeling/reviewer rules, walkthrough sections | High for text; posting is infra |

Sources: [14][15][16][17].

### 4.2 Hosted infrastructure (not portable as a skill)

| Concern | Why it is infrastructure | Evidence |
|---|---|---|
| Event source | Webhooks/service hooks, signature verification, installation mapping, retries, event filters | [13][19][33] |
| Credentials | GitHub App private key, webhook secret, OAuth, installation tokens, GitLab/ADO/Bitbucket tokens | [12][13][33] |
| Queue/concurrency | Queued/in-progress states, superseding, worker capacity, sandbox provisioning | [1][9][11] |
| Repository context | Full clone, code graph/index, vector representations, dependency cache, multi-repo links | [1][17][18][19] |
| Static analysis | Sandboxed execution of 50+ tools and result normalization | [8] |
| Model orchestration | Multiple models/agents, prompt construction, context budgeting, retries | [1][2][19] |
| Incremental state | Last reviewed SHA, commit counter, pause state, tracked findings, thread resolution | [3][6][9] |
| Dedupe | Commit/head dedupe, supersede, finding/comment identity | [9][11] |
| Publishing | PR review API with line comments, summary in description, walkthrough upsert, check runs, thread resolve/approve | [4][5][9] |
| Identity | OAuth/SSO/SCIM, seats, roles, org/repo access, audit logs | [10][12][20] |
| Billing/quota | Per-developer rolling allowances, file caps, fair use, credits, spend caps | [10][11] |
| Compliance | Self-hosting, data residency, retention controls, audit, RBAC | [19][20] |

### 4.3 The boundary in one sentence

**CodeRabbit's prompts and configuration decide *what a review should care about*; its hosted system decides *whether, when, where, with what context, under whose identity, at what cost, and where the result lands*.** A skill can own the first sentence. Only a runtime + event source + state can own the second.

---

## 5. Reproducing the useful review behavior in Machina

### 5.1 Three levels, and what each actually buys

| Level | What you build | What you retain | What you lose | Effort |
|---|---|---|---|---|
| **A. Skill only** | `skills/pr-review/SKILL.md` that reads repo standards, computes `git diff`, calls the model, emits findings, optionally fixes | Trigger phrases, review checklist, severity taxonomy, local policy, fix loop | Automatic triggers; PR posting; checks; cross-run state; repo-wide index; tool sandbox; multi-user identity; quotas; audit | 1–3 days |
| **B. Skill + reviewer subagent + hooks** | A read-only reviewer subagent in a fresh session; pre-push/post-turn hooks; deterministic tool evidence; bounded fix/re-review loop | A/B isolation of author vs reviewer; context isolation; repeatable evidence; tool-backed findings; harness-enforced permissions; local reports | Everything outside the machine: GitHub events, inline comments, checks, team-wide dedupe/state, org learnings, billing | 1–2 weeks |
| **C. GitHub Action / webhook + queue + App/auth + state** | Event ingress, idempotent queue, installation tokens, PR review/check publishing, persistent finding state, dedupe, budgets | Full PR lifecycle: automatic reviews, inline comments, checks, incremental reviews, team-wide dedupe, multi-repo, audit | CodeRabbit's proprietary model orchestration, 50+ tool sandbox, code graph/index, polished multi-platform UI, vendor SLAs | Action 1–2 weeks; service 1–3 months |

### 5.2 What "useful review behavior" actually requires

The minimum credible reviewer needs five things, in order of value:

1. **A trustworthy diff and scope.** Merge-base diff, changed-file list, renames, generated/lockfile exclusions, and a cap on files/tokens. This alone removes most noise.
2. **Repository context beyond the diff.** At least callers/imports/tests for changed symbols, plus `AGENTS.md`/`GLOSSARY.md`/ADRs. A repomap or symbol index is enough; a vector database is optional.
3. **Deterministic evidence.** Run the repo's own linters/tests/type-checker and a secret scanner. Models are bad at pretending they ran tools; capture real output and cite it.
4. **A reviewer that is not the author.** A fresh subagent with read-only tools and no knowledge of the author's rationalizations produces better findings and is easier to audit.
5. **A publishing/state loop.** Without posting, dedupe, and incremental state, the reviewer is a one-shot report, not a review workflow.

### 5.3 What a local harness cannot honestly claim

- **"Same as CodeRabbit."** The model may be the same family, but context assembly, tool coverage, prompt tuning, multi-agent verification, and repository indexing are not public and are not reproduced.
- **Org-wide learning.** A local `learnings.md` is not a multi-tenant vector store with scoped retrieval, approval, redaction, and usage tracking.
- **Guaranteed coverage.** CodeRabbit itself says its check does not guarantee every PR was fully reviewed (skips, path filters, file caps, incremental scope). A local reviewer has the same limitation and should say so.
- **Compliance equivalence.** SSO, RBAC, audit logs, retention controls, and self-hosting are product/ops capabilities, not prompt features.

---

## 6. Is "CodeRabbit can just be a skill" accurate?

**No, not as a description of the product. Yes, as a description of one integration surface.**

CodeRabbit's own skills repository is the strongest evidence. The `code-review` skill:
- checks CLI installation and authentication,
- runs `coderabbit review --agent`,
- parses NDJSON and preserves `critical`/`major`/`minor`/`trivial`/`info`/`none`,
- sends code diffs to the CodeRabbit API,
- explicitly says installation is user-run and credentials are never read by the skill,
- has no automatic hook or MCP review launcher [22][23].

So the skill owns: **when the user asked, what scope to review, how to present findings, how to loop fixes, and safety rules**. The service owns: **models, prompts, tools, repo clone/index, state, dedupe, billing, and the actual review execution**.

### Retained by a skill
- Natural-language trigger and discoverability.
- Scope selection (committed/uncommitted/base/dir).
- Policy/checklist/severity presentation.
- Auth handoff and failure recovery instructions.
- Bounded fix-and-re-review workflow.
- Safe handling of untrusted output.

### Lost by a skill alone
- Event-driven automatic review on PR open/push.
- Inline line comments and threaded conversation.
- Check runs and merge gating.
- Incremental state and supersede/dedupe.
- Full-repo clone/index, code graph, multi-repo context.
- 50+ sandboxed tools and result normalization.
- Multi-agent verification and proprietary prompt/context assembly.
- Identity, seats, quotas, usage billing, audit, SSO.
- Cross-platform (GitLab/ADO/Bitbucket) installation and webhooks.

**Precise statement:** *CodeRabbit can be exposed as a skill; it cannot be reduced to a skill.* The correct local analogue is "skill + reviewer subagent + deterministic tools + (optionally) CI/event plumbing," not "skill only."

---

## 7. Recommended Machina architecture

### 7.1 Principle

**Local-first review plane; hosted only at the edge.** Run the expensive, context-heavy review where the code and the developer already are (Machina session or ephemeral CI runner). Add a small service only for what a local process genuinely cannot do: receive provider events, mint short-lived credentials, enforce idempotency across users, and publish under a bot identity.

### 7.2 Target architecture

```text
┌──────────────────────────────┐
│ Triggers                     │
│  • pre-push / pre-PR hook    │
│  • GitHub Action (primary)   │
│  • optional webhook service  │
└──────────────┬───────────────┘
               │ review request {repo, base, head, pr, policy}
               ▼
┌──────────────────────────────┐
│ Machina review skill         │
│  • resolve policy/config     │
│  • compute diff + scope      │
│  • build evidence bundle     │
│  • call reviewer subagent    │
│  • dedupe + gate + publish   │
└───────┬───────────────┬──────┘
        │               │
        ▼               ▼
┌───────────────┐  ┌────────────────────┐
│ Reviewer      │  │ Deterministic      │
│ subagent      │  │ evidence           │
│ read-only,    │  │  • git diff/status │
│ fresh context │  │  • repo map/symbols│
│ no write/edit │  │  • linters/tests   │
└───────┬───────┘  │  • secret scan     │
        │          └─────────┬──────────┘
        └────────┬───────────┘
                 ▼
        ┌──────────────────┐
        │ Findings schema  │
        │ + fingerprints   │
        └────────┬─────────┘
                 ▼
        ┌──────────────────┐        ┌──────────────────┐
        │ Publisher        │───────▶│ State store      │
        │ local / PR / CI  │        │ local / cache /  │
        │ summary+comments │        │ optional DB      │
        │ + check          │        └──────────────────┘
        └──────────────────┘
```

### 7.3 Components and concrete choices

#### 1. Policy: one skill, one config file
- `skills/pr-review/SKILL.md`: the workflow contract (scope, evidence, severity, dedupe, publish, safety).
- `.machina/review.yaml`: native config. Read a **subset of `.coderabbit.yaml`** when present for interop (profile, path filters, path instructions, tools enablement, auto-review filters). Do not pretend to support every CodeRabbit key; fail clearly on unsupported keys.
- Reuse Machina's existing `AGENTS.md`, `GLOSSARY.md`, `docs/adr/`, and skill routing. Do not create a second standards system.

#### 2. Reviewer subagent: isolated and read-only
- A `pr-reviewer` agent definition with tools limited to `read_file`, `search_files`, `list_files`, and a **read-only terminal allowlist** (git diff/log/show, test/lint commands that do not write).
- Fresh session per review; only the diff, changed files, relevant callers/tests, standards, prior unresolved findings, and tool outputs go in.
- No `write_file`, no `edit`, no network, no package install, no `gh` write. The parent agent (author) applies fixes; the reviewer only reports.
- Return structured JSON, not free-form prose. Machina's `delegate`/`machina_delegate` already provides parent-authority intersection and approval handling [34].

#### 3. Deterministic evidence layer
- Diff: `git diff --merge-base <base> <head>`, `--name-status`, `--find-renames`, `--stat`.
- Context: changed symbols → callers/imports/tests via ripgrep or the existing repomap; cap at a token budget.
- Tools: detect and run the repo's own commands (`ruff`, `eslint`, `tsc`, `pytest`, `go test`, `cargo clippy`, `semgrep`, `gitleaks`) in a sandbox; capture exit code, stdout/stderr, and file/line.
- Normalize to a `ToolFinding` shape. Never let the model claim a tool ran when it did not.

#### 4. Findings schema
```json
{
  "id": "stable-id",
  "fingerprint": "sha256(category|path|normalized_context|rule_id)",
  "severity": "critical|major|minor|trivial|info",
  "category": "security|stability|data|correctness|performance|maintainability",
  "file": "src/x.py",
  "line": 42,
  "evidence": "quoted code or tool output",
  "explanation": "why it matters",
  "suggestion": "minimal fix",
  "confidence": 0.0,
  "source": "model|tool|both",
  "status": "open|fixed|dismissed|superseded"
}
```
Add `prompt_for_agents` only when a fix is actionable; otherwise keep the comment short.

#### 5. Publisher
- **Local:** Markdown/JSON report; optional `machina review post` command.
- **GitHub Action (recommended first host):** `pull_request` on `opened`, `synchronize`, `reopened`, `ready_for_review`; `contents: read`, `pull-requests: write`, `checks: write`; `concurrency: cancel-in-progress: true` per PR. Post one summary comment, one review with inline comments, and one check run. Use a hidden marker (`<!-- machina-review:... -->`) to upsert.
- **Webhook service (later):** verify HMAC, enqueue, mint installation token, run the same skill in a sandboxed worker or dispatch to CI, publish. Keep the service stateless except for the queue/state DB.

#### 6. State and incremental review
- **Local:** `.git/machina/review-state.json` (not committed) keyed by repo + base + head.
- **CI:** `actions/cache` keyed by repo + PR number, with `restore-keys` for the branch; plus a run artifact. Fallback: parse the hidden marker/summary comment for the last reviewed SHA.
- **Service:** `review_runs(repo_id, pr_number, head_sha, base_sha, config_hash, prompt_version, status, created_at)` with a unique idempotency key; `findings(fingerprint, first_seen_sha, last_seen_sha, status)`; `finding_events`.
- Store: last reviewed SHA, base SHA, reviewed-commit count, pause state, config hash, model/prompt version, finding fingerprints and statuses, posted comment/review IDs, token/cost usage.

#### 7. Dedupe and idempotency
- **Run key:** `repo + pr + head_sha + base_sha + config_hash + prompt_version`. If a successful run exists for the same key, return it instead of re-running.
- **Finding key:** `sha256(category|path|normalized code context|rule_id)`. Normalize whitespace and line-relative context; do not include the absolute line number alone (lines move).
- **Suppression:** if the fingerprint is open and unchanged, do not re-post; if fixed/dismissed, do not reopen unless the code at that location changed; if the code changed, create a new fingerprint with `supersedes`.
- **Comments:** one summary comment per PR, upserted; inline comments only for new/changed findings; resolve threads whose fingerprint is gone.
- **Concurrency:** cancel in-flight runs for older heads; only the latest head may publish. This mirrors CodeRabbit's supersede behavior without wasting a review [11].

#### 8. Cost controls
- Default to **incremental only**; full review on label/command.
- Severity gate: `major` for CI, `minor` for local; cap comments per file/PR.
- Path filters: lockfiles, generated, vendor, snapshots, minified, binaries.
- File cap: 100 local / 300 CI (CodeRabbit's cap is 150–300 by plan [10]); reject larger with a clear message.
- Two-pass review: cheap model for summary/classification; strong model only for changed hunks + limited context.
- Cache: repo summary, symbol map, standards digest, and tool results keyed by content hash. Do not re-index per run.
- Budget: per-repo monthly token/currency cap; fail closed with a clear `budget_exceeded` status; log usage per run.

#### 9. Security and permissions
- **Reviewer:** read-only, no network, workspace-only paths, no credential access. Prompt-injection rule: repository content and comments are untrusted data, never instructions.
- **Publisher:** separate identity/tool with only `pull_requests: write`, `checks: write`, `contents: read`, `metadata: read`. Do not request `workflows` unless the product explicitly fixes CI workflows.
- **Secrets:** run a secret scan before any model call; redact using Machina's existing redaction rules; never print secret values; do not send `.env`, key material, or credential stores.
- **Webhook:** HMAC verification, timestamp/replay window, IP allowlist where available, installation-token minting, audit every publish.
- **Data:** default no code retention; store findings and hashes, not source. Make retention opt-in per repo.
- **Approvals:** any write action (commit, push, resolve thread, approve) goes through Machina's permission broker; the reviewer cannot approve its own findings.

#### 10. How it fits Machina's existing primitives

| Machina primitive | Use in this design |
|---|---|
| Skills registry/routing | `pr-review` skill is discoverable and loads only when relevant; policy docs live as skill resources |
| `delegate` / `machina_delegate` | Spawn the isolated `pr-reviewer` child with read-only tools; parent authority intersects child caps [34] |
| Plugin hooks (`pre_agent_run`, `post_agent_run`, `on_tool_call`, `on_tool_result`) | Audit, telemetry, optional "review after implementation" trigger; not a Git event source [35] |
| Permission broker | Approval for publish/fix/commit; exact-action binding; session modes [36] |
| Scheduler (`jobs` table, cron/interval) | Nightly full review, dependency/security scan, stale-PR digest |
| MCP server | Let OpenCode/Codex/Claude Desktop call Machina as a subagent provider; expose `review_run`/`review_status` tools [37] |
| ACP bridge | Editor hosts can run Machina as an ACP agent; review locally without a separate CLI |
| Gateway API + jobs | Natural home for an optional webhook ingress and review queue |
| Memory/redaction | Local learnings and secret redaction; org-wide shared learnings need a service DB |
| Connectors / GitHub MCP | Read PR metadata and threads; do not grant write scopes to the reviewer |

### 7.4 Phased plan

**Phase 0 — local value (days)**
- Add `skills/pr-review/SKILL.md` + `agents/pr-reviewer.md`.
- Implement diff/scope, standards loading, evidence bundle, JSON findings, Markdown report.
- Add a `pre-push` or `/review` trigger. No posting, no state beyond a local report.
- Success metric: on 20 real local branches, reviewer finds at least one true major issue per 3 branches with <20% false-positive noise.

**Phase 1 — team enforcement (1–2 weeks)**
- GitHub Action on `pull_request`; summary + inline comments + check; `concurrency` cancel; cache state; hidden marker upsert.
- Severity gate and path filters; per-PR token budget.
- Success metric: <5 min p50 on a 300-file cap; duplicate comments <2%; no secret leakage in logs.

**Phase 2 — optional service (4–8 weeks)**
- Only if needed: webhook ingress for GitLab/ADO/Bitbucket or central multi-repo policy; queue + DB + installation tokens + audit.
- Keep workers ephemeral; do not store source.
- Success metric: idempotent replay, zero duplicate reviews per head SHA, per-org budget enforcement.

**Phase 3 — depth (only if justified)**
- Multi-repo context, org learnings, security deep scan, triage/explain UI. These are separate products, not prerequisites for useful PR review.

### 7.5 What not to build

- A general multi-tenant review SaaS before a single team uses the local/Action path daily.
- A custom vector index before measuring whether repomap + grep + tests are insufficient.
- A 50-tool sandbox. Run the repo's existing tools; add Semgrep/secret scanning first.
- Auto-approve or auto-merge. Start with comments and a non-blocking check; add gating after precision is proven.
- A CodeRabbit-compatible clone of every config key. Support a documented subset and fail loudly on the rest.

---

## 8. Architecture comparison at a glance

| Dimension | CodeRabbit | Machina A: skill | Machina B: skill + subagent/hooks | Machina C: Action/webhook + state |
|---|---|---|---|---|
| Trigger | Webhooks, automatic | User ask | User ask + local hooks | Provider events, automatic |
| Context | Full clone, code graph/index, multi-repo, learnings | Working tree | Working tree + evidence bundle | Checkout at head/base + cache |
| Tools | 50+ sandboxed | Whatever is installed | Allowlisted, sandboxed | Runner-installed |
| Reviewer isolation | Hosted agents | None (same session) | Fresh read-only child | Fresh child/worker |
| PR posting | Summary, walkthrough, inline, checks, approvals | None | Local report | Summary, inline, check |
| Incremental state | Per-PR, commit counter, tracked findings | None | Local JSON | Cache/DB + fingerprints |
| Dedupe | Commit/head + thread state | None | Fingerprint in local state | Idempotency key + fingerprints |
| Identity/quota | Seats, SSO, per-dev rolling limits | User's own model budget | Harness budget | Org/repo budget |
| Ops burden | Vendor | None | Low | Medium–high |
| Best fit | Teams wanting a vendor | Individual pre-commit | Small team, single repo | Multi-repo/team enforcement |

---

## 9. Uncertainties and limitations

1. **Internal architecture is not public.** CodeRabbit's architecture page is high-level. Queue implementation, worker isolation, prompt text, model routing/ensemble, index algorithm, state schema, and dedupe logic are **Unknown**; statements about them here are inferred from documented behavior.
2. **Docs are vendor-authored.** Feature descriptions, accuracy implications, and "enterprise-grade" claims are marketing-adjacent. No independent benchmark was run for this report.
3. **Current-ness.** CodeRabbit ships frequently; V3 configuration is mid-migration, several features are beta (Security, Verify), and plans/prices/limits can change. Re-verify before making contractual decisions.
4. **Permissions can drift.** The documented GitHub App permission list may not match the live manifest exactly; always inspect the installation screen. The `workflows` write permission is broader than a review-only product needs.
5. **Self-hosted details are opaque.** Self-hosting is Enterprise-only (500+ seats) and deployment specifics are provided during onboarding; its internal architecture is not documented publicly [20].
6. **"Skill" is overloaded.** CodeRabbit's skills are clients for its CLI/service. A Machina skill is instructions for Machina's harness. Comparing them requires naming the runtime, tools, and event source.
7. **Quality is model- and context-dependent.** Same model family does not imply same results; CodeRabbit's context assembly and tool hints are part of the product.
8. **No guarantee of full coverage.** CodeRabbit's own check docs say skips, path filters, file caps, and incremental scope mean a passing check does not guarantee a full review [9]. Any local reproduction inherits the same limitation.
9. **Billing/fair-use is adaptive.** Per-hour numbers are baseline refill rates; sustained activity can reduce them [11]. Budget planning should assume degradation under load.
10. **Legal/compliance.** The skills repo is MIT, but the CodeRabbit service, models, and data handling are governed by its terms/privacy policy. Do not treat the MIT skill license as a license to the service.

---

## 10. Citations

All accessed 2026-11-25.

1. CodeRabbit — "The System Behind Every Review Comment" (architecture): https://docs.coderabbit.ai/overview/architecture.md
2. CodeRabbit — "Pull request reviews" overview: https://docs.coderabbit.ai/guides/code-review-overview.md
3. CodeRabbit — "Control when reviews run": https://docs.coderabbit.ai/pr-reviews/when-reviews-run.md
4. CodeRabbit — "PR Summaries": https://docs.coderabbit.ai/pr-reviews/summaries.md
5. CodeRabbit — "PR Walkthroughs": https://docs.coderabbit.ai/pr-reviews/walkthroughs.md
6. CodeRabbit — "Review commands": https://docs.coderabbit.ai/reference/review-commands.md
7. CodeRabbit — "Fix comments / Autofix": https://docs.coderabbit.ai/finishing-touches/autofix.md
8. CodeRabbit — "Third-party tools overview": https://docs.coderabbit.ai/tools/index.md
9. CodeRabbit — "CodeRabbit status check": https://docs.coderabbit.ai/pr-reviews/status-check.md
10. CodeRabbit — "Plans and pricing": https://docs.coderabbit.ai/management/plans.md
11. CodeRabbit — "Review rate limits": https://docs.coderabbit.ai/management/rate-limits.md
12. CodeRabbit — "GitHub" integration and permissions: https://docs.coderabbit.ai/platforms/github-com.md
13. CodeRabbit — "GitHub Enterprise Server" (OAuth app, GitHub App, webhook URL/events): https://docs.coderabbit.ai/platforms/github-enterprise-server.md
14. CodeRabbit — "Configuration file": https://docs.coderabbit.ai/configuration/file.md
15. CodeRabbit — "Teach CodeRabbit your review rules": https://docs.coderabbit.ai/pr-reviews/review-instructions.md
16. CodeRabbit — "Learnings": https://docs.coderabbit.ai/knowledge-base/learnings.md
17. CodeRabbit — "Knowledge Base overview": https://docs.coderabbit.ai/knowledge-base/index.md
18. CodeRabbit — "Caching": https://docs.coderabbit.ai/reference/caching.md
19. CodeRabbit — FAQ (diff vs repo context, data security, code indexing, cleanup/webhooks): https://docs.coderabbit.ai/faq.md
20. CodeRabbit — "Self-hosted CodeRabbit": https://docs.coderabbit.ai/self-hosted/overview.md
21. CodeRabbit — CLI overview (local/remote review, agent mode, deep review): https://docs.coderabbit.ai/cli/index.md
22. CodeRabbit — "CodeRabbit Skills" (installable agent skills over the CLI): https://docs.coderabbit.ai/cli/skills.md
23. CodeRabbit skills repository — README, `skills/code-review/SKILL.md`, `agents/code-reviewer.md`: https://github.com/coderabbitai/skills
24. CodeRabbit — API and hosted MCP overview: https://docs.coderabbit.ai/api.md
25. CodeRabbit — "Headless CLI integration": https://docs.coderabbit.ai/cli/headless-cli-integration.md
26. CodeRabbit — "CodeRabbit Secure" overview: https://docs.coderabbit.ai/security/index.md
27. CodeRabbit — "Deep Scan": https://docs.coderabbit.ai/security/deep-scan.md
28. CodeRabbit — "Coding Agent": https://docs.coderabbit.ai/code/index.md
29. CodeRabbit — "Triage": https://docs.coderabbit.ai/triage/index.md
30. CodeRabbit — "CodeRabbit Explain": https://docs.coderabbit.ai/change-stack/index.md
31. CodeRabbit — "CodeRabbit Verify": https://docs.coderabbit.ai/verify/index.md
32. CodeRabbit — "Automations": https://docs.coderabbit.ai/automations/index.md
33. CodeRabbit — "GitLab" integration (tokens, webhook `https://coderabbit.ai/gitlabHandler`): https://docs.coderabbit.ai/platforms/gitlab-com.md
34. Machina — MCP interop and subagent shim (`machina_delegate`, child authority): `docs/MCP.md`; `machina/bundled_agents/ecc/`, `machina/core/agent_def.py`
35. Machina — plugin lifecycle hooks (`pre_agent_run`, `post_agent_run`, `on_tool_call`, `on_tool_result`, `on_memory_extract`): `machina/plugins/base.py`, `machina/plugins/registry.py`
36. Machina — permission modes and broker: `machina/permissions/policy.py`, `machina/permissions/broker.py`
37. Machina — MCP server / ACP bridge: `machina/mcp_server/`, `machina/acp_bridge.py`; `docs/MCP.md`

---

## Appendix A — CodeRabbit feature-to-layer map (compact)

| Feature | Prompt/policy | Hosted infra | Local client |
|---|---|---|---|
| Path instructions | ✅ | config resolution | reads file |
| Code guidelines | ✅ (files) | auto-detection/index | reads repo |
| Learnings | ✅ (text) | storage/retrieval/approval | none |
| Review profile/tone | ✅ | prompt construction | config |
| Tools enable/disable | ✅ | sandbox execution | local tools (CLI) |
| Custom checks | ✅ | check executor | can run locally |
| Summary/walkthrough format | ✅ | posts/upserts | CLI text |
| Incremental review | settings | state machine | checkpoints |
| Inline comments | format | PR review API | none |
| Status check | settings | Checks API | none |
| Auto-fix/autofix | recipe text | sandbox + write creds | CLI/agent loop |
| Security Deep Scan | instructions | repo map/scan/verify | limited |
| Multi-repo context | links | index/access | none |
| Seats/quotas | plan config | billing/identity | usage check |
| SSO/audit | policy | identity/audit | none |

## Appendix B — Minimum viable finding contract

A review is only useful if the author can act on it without re-reading the whole diff. Require every finding to include:

1. **Location** — file and line (or symbol) in the head commit.
2. **Evidence** — a quoted hunk or tool output, not a paraphrase.
3. **Why it matters** — the concrete failure mode, not a style preference.
4. **Minimal fix** — a patch-sized suggestion or a precise instruction.
5. **Confidence/source** — model, tool, or both; low-confidence findings are separated as "questions" rather than blockers.
6. **Fingerprint** — stable across line moves, so dedupe works.
7. **Severity + category** — one of the CodeRabbit-compatible buckets, so downstream gates and dashboards can share semantics.

If a finding cannot supply 1–4, it should not be posted as an inline comment.
