# Glossary

Project vocabulary. If a term here disagrees with the code, the code wins and
this file should be corrected.

| Term | Meaning |
|---|---|
| **Harness** | The runtime around the model: turns, tools, context, memory, permissions, routing, and durable state. |
| **Surface** | A transport that presents the same core: terminal UI, CLI, HTTP+SSE, MCP server, ACP bridge, external-harness interop. |
| **Gateway** | The JSON-RPC 2.0 edge the TUI speaks to over stdio (`machina/gateway`). |
| **Turn** | One owned request→tools→answer sequence for a session. |
| **Claim / lease** | The durable, fenced ownership row (`claim_owner`, `claim_kind`, `claim_expires_at`) that lets exactly one worker write a session. |
| **Fencing token** | The owner string checked on release/renew so a stale worker cannot unlock a newer owner. |
| **Session** | The durable conversation identity; owns context chunks, state, claims, and authority. |
| **Context chunk** | One typed, ordered row in the transcript event log (`context_chunks`), stored as MessagePack with a per-session `event_seq`. |
| **Working set** | The bounded prompt actually sent for one request; a projection of the transcript plus retrieval evidence. |
| **Projection** | Selection/fitting of evidence to a token budget with explicit omission disclosure. |
| **Compaction** | Replacing old chunks with a summary/checkpoint and archiving the originals byte-exact. |
| **Continuity anchor** | The bounded deterministic summary of evicted native turns, persisted in session state. |
| **Artifact** | A verbatim tool result stored durably before any lossy view is published. |
| **Reducer** | A deterministic, format-aware smaller view of an oversized tool result (tests, logs, JSON, diff, …). |
| **Memory** | A durable claim extracted from experience, with category, importance, provenance, and use history. |
| **Provenance** | The HMAC-signed record of which agent/source produced a memory. |
| **Quarantine** | A memory stored but excluded from reads because the identity gate rejected it. |
| **Digest (v2)** | SHA-256 over every material action field; the approval identity. |
| **Approval** | A durable human decision bound to session+agent+digest, claimed once before execution. |
| **Grant** | Reusable authority for a reviewed capability/scope; distinct from a one-time approval. |
| **Mode** | `ask`, `workspace`, `sandbox`, `full`; a policy preset, not a security claim. |
| **Execution context** | The immutable broker-stamped mode/backend/workspace/digest snapshot used by tools. |
| **Tool** | A registered callable with a JSON schema and broker effects. |
| **Skill** | A `SKILL.md` package with routing metadata; catalog-first, body on demand. |
| **MCP** | Model Context Protocol: external tools/servers, catalog-first, digest-bound calls. |
| **Route** | A provider/model pair plus role fit, cost tier, transport, and fallbacks. |
| **Inventory** | The connected-model rows built from credentials/auth state, not configuration alone. |
| **Job** | A durable scheduled unit (heartbeat, interval, cron, or script) with a leased claim token. |
| **Repair turn** | A bounded task-run policy for fix/regression work: lean tools, caps, verification gate. |
| **RSI** | The bounded reinforcement/self-improvement loop over retrieval policy and persona notes. |
