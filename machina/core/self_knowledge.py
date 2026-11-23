"""Small implementation brief and safe current flags, shared by prompts and inspection.

No filesystem, database, provider requests or secret configuration enumeration.
Feature facts are distinct from configuration and observed learning evidence.

Style: 1-3 terse sentences per entry; the brief is bounded (prompt token pin and
``harness_status`` response cap), so trim before adding. snake_case tokens are
configuration keys or registered tool names, pinned against the real
inventories by ``tests/test_capability_catalog.py``; never add an
invented name.
"""

from __future__ import annotations

import json

from machina.core.config import config
from machina.core.models import Session
from machina.core.session_mode import MODES
from machina.memory.persona import EmotionTopology
from machina.workspace.service import (
    CODE_INTELLIGENCE_OPERATIONS,
    active_workspace_service,
)

# Keep this map lean: ``harness_status`` serializes a copy alongside live
# learning state inside its 8,500-byte budget, and the truncation ladder drops
# sections in this order: installed skill names -> this map -> workspace
# profiles -> learning notes -> persona/trial. Learning notes are the least
# recoverable section, so this map is sized to leave headroom for them instead
# of forcing their omission. Add a fact only if it displaces prose here.
FEATURES = {
    "persona": "SoulSpec unifies identity, /persona tone (persona_description), lessons and RSI guidance; agent id is role/tooling scope, not persona. persona_memory_enabled adds fact interpretations and emotion-weighted recall via persona_default_emotion.",
    "growth": "Per-agent preferences, style and verified lessons persist across sessions/models (learn_personality; /growth). empty notes mean none saved, not a missing feature.",
    "rsi": "RSI automatically adapts retrieval, skill ranks, tool order and approach from weak outcomes; persona trials need three human-rated episodes to stabilize or roll back (/growth shows evidence). No model weights, code or permission rewriting; more turns are not proof.",
    "memory": "remember/recall retrieve durable facts; session_recall searches conversations (not docs); search_documents searches indexed RAG documents. memory_approval_enabled routes new facts to pending queues (/memory pending|approve|reject; `machina memory-pending|memory-approve|memory-reject`); /profile and /profiles manage profiles.",
    "sessions": "Sessions persist conversation, model and goal; /sessions and /resume reopen one, /new starts one, /search finds titles, /recall searches past text; /rename, /goal, /fork, /delete, /export manage the rest; CLI: `machina sessions`.",
    "context": "Turns select evidence, not replay; /context shows retained context, /compress and `machina compress` summarize older context, `machina context` lists chunks. Compaction: compression_enabled, auto_compaction_enabled, compression_threshold.",
    "web": "web_search finds links without fetching; web_research saves public pages with source URLs to session RAG; web_crawl crawls sites with bounds. The TUI manages the stack (firecrawl_autostart, firecrawl_stop_on_exit, firecrawl_home).",
    "skills": "First-party and ECC skills activate by request, trigger or task evidence with bounded context; skill tools read more when offered. /skills list|learn|proposals|approve|reject|revert|delete|curator and `machina learn|hub|curator` manage them. Skills grant no permissions.",
    "agents": "Delegated subagents (ECC roles plus built-ins) inherit parent authority and tools; /agents manages definitions, /delegate picks one. max_delegate_concurrency bounds active workers (default 8, max 64); worker_count controls planned workers (0=auto, 20+ supported). orchestration_mode is single-model or multi-model; model_selection may auto-pick or honor pins; worker_transport is native, proxy, or mcp. delegate_inline_wait_seconds returns long workers as trackable work (default 120, 0 waits indefinitely). Use delegate_status with a child session ID to check them; roles grant no new permissions.",
    "jobs": "Jobs run interval prompts, heartbeats, UTC-cron and scripts; /jobs list|add|script|heartbeat|cron|on|off|delete manages them. Consent-needing jobs pause, never self-grant.",
    "usage": "`machina usage` and /usage show recorded tokens/requests; budgets: usage_session_token_limit, usage_session_request_limit, usage_agent_token_limit, usage_agent_request_limit (0 = unlimited).",
    "models": "Models/providers: /models picks and saves a model from catalogs, /provider manages OpenAI-compatible providers, /effort sets reasoning_effort; `machina providers` and /keys manage the inventory (keys in a git-ignored .env, 0600). The connected-model router builds role-specific planner/supervisor/worker rankings from live credential state, model metadata, capability fit, cost and quota; route_explain and model_inventory inspect it without a provider call. Multi Auto is the default and appears as a first-class /models choice; it sets multi-model + auto selection and keeps the current model as the session/planner anchor while delegated and parallel work is auto-routed. single-model copies one route to every role; multi-model chooses per role. /routing only explains a route. Cost tier low|balanced|high and worker_count 0..64 are configurable.",
    "auth": "/auth signs in the ChatGPT account; `machina auth chatgpt|copilot|status|logout` covers subscription sign-in, Copilot device flow and token removal. Never edits harness auth files; Codex imports are one-way.",
    "theme": "/theme previews and saves one of 53 UI skins (theme); Esc restores the previous skin.",
    "modes": "Modes ask|workspace|sandbox|full: /mode and `machina mode` set them; full is a session grant revoked via /approvals. /approvals list|allow|deny|revoke manages pending consent.",
    "controls": "TUI /help lists commands; /config manages non-secret settings (--save persists), /status reports health, /init writes AGENTS.md, /review reviews changes. Slash commands are UI actions, not model tools.",
    "self_change": "When asked, the agent may change this installation within its offered tools and permissions: `machina config-set` (non-secret settings), the slash-command surface (/config, /theme, /models, /mode, /keys, /mcp, /skills, /memory, /harnesses), or permission-gated edits. Autonomous RSI never rewrites weights, code or permissions; user-owned choices are asked, not guessed.",
    "install": "repo_stage builds a digest-bound skills review plan; repo_activate installs reviewed skills. Never runs tests/installers/hooks/package managers. Skill metadata generates once at install. MCP drafts need /mcp review.",
    "execution": "Offered tools define access; approval-gated actions follow /mode and /approvals. Delegation inherits the parent's authority and returns through the parent, without sibling peer chat; share_memory shares signed memories.",
    "mcp": "TUI /mcp is a centered provider menu: configured providers show cached tools under their header, unauthorized providers offer Authorize, and Remove server is the only cleanup action. Pasting an http(s)/mcp link still works, and mcp_connect/mcp_disconnect add/remove servers. Authorized HTTP servers reconnect at startup; @provider mentions route requests to mcp_list/mcp_describe/mcp_call automatically. mcp_call requires exact approval unless trusted-read. HTTP servers support browser OAuth with PKCE; authorization grants no execution permission.",
    "harnesses": 'External agent harnesses (OpenCode, Codex, Hermes) are detected installed/running/registered; `machina harnesses status|enable|disable|setup|run` manages them and registers Machina\'s MCP server (state ~/.machina/interop.json; `interop` and /interop alias it). harness_features probes and caches available models, agents, MCP servers, auth providers and plugins with staleness; harness_status includes the cached compact inventory. "connect to opencode" means `machina harnesses enable opencode`; `machina harnesses run` delegates (TUI /harnesses; chat: approval-gated). Never touches their auth files.',
    "images": "The TUI accepts dropped PNG/JPEG/GIF/WebP or clipboard images (Ctrl+V), sent inline to the provider as [image 1] markers and kept in recent context. Understanding needs a vision-capable model; markers alone do not prove an image was supplied.",
    "music": "A pinned bar shows what is playing (/music: play/pause, next/previous, on|off; nowplaying_enabled). Detection is local and best-effort (system media APIs, Spotify/Music, a front YouTube tab); permission prompts are reported, never guessed.",
    "workspace": "The terminal has an editor and optional language servers (/workspace, /editor, /explorer, /lsp, /problems, /quickopen). code_intelligence answers diagnostics, definition, references, symbols or hover for one authorized file: source analysis, not runtime or test success; server text is untrusted data. Unsaved content is shared only when attached (/attach); a visible pane grants no edit, command or file permission.",
}


# Compact feature briefs for the always-on prompt. The full ``FEATURES`` map
# stays available to ``harness_status`` and ``capability_context``; normal
# turns need orientation, not the whole manual. Keep these terse and stable so
# the compact section remains useful without becoming a second manual.
_FEATURE_BRIEFS: dict[str, str] = {
    "persona": "SoulSpec identity, /persona tone, growth lessons.",
    "growth": "Per-agent preferences, style and lessons persist; /growth.",
    "rsi": "Adaptive retrieval/skill/tool ranking from outcomes; no code or weights.",
    "memory": "remember/recall/session_recall; /memory and optional approval queue.",
    "sessions": "Persistent sessions; /sessions /resume /new /search /fork.",
    "context": "Budgeted evidence projection; /context /compress.",
    "web": "web_search/research/crawl with source URLs saved to session RAG.",
    "skills": "Catalog-first skills; /skills and skill tools.",
    "agents": "Delegation and specialists; /agents /delegate.",
    "jobs": "Interval, cron, heartbeat and script jobs; /jobs.",
    "usage": "Token/request usage and budgets; /usage.",
    "models": "Provider/model catalog and routing; /models /provider /routing.",
    "auth": "Subscription provider sign-in; /auth.",
    "theme": "UI skins; /theme.",
    "modes": "ask|workspace|sandbox|full; /mode /approvals.",
    "controls": "Help, config, status, init and review commands; /help /config /status.",
    "self_change": "Use offered tools and user-owned settings; no autonomous code/permission rewrite.",
    "install": "repo_stage/repo_activate install reviewed skills; no repo execution.",
    "execution": "Offered tools and approvals define access; delegation inherits.",
    "mcp": "MCP providers and tools; /mcp, browser OAuth, exact approvals.",
    "harnesses": "External OpenCode/Codex/Hermes detection and delegation; /harnesses.",
    "images": "Inline PNG/JPEG/GIF/WebP; understanding needs a vision model.",
    "music": "Now-playing bar and /music controls; local best-effort.",
    "workspace": "Editor, LSP and read-only code_intelligence; /workspace.",
}


def compact_capability_context(session: Session, *, tool_names: set[str], growth_state: str) -> str:
    """Bounded capability orientation for ordinary turns.

    The full feature manual is intentionally not repeated in every request.
    Normal turns get the surfaces, feature names/commands, current flags, and
    a pointer to ``harness_status``/``harness_catalog`` when those tools are
    offered. This keeps the stable prefix small without making feature
    questions unanswerable.
    """
    facts = "\n".join(f"- {name}: {brief}" for name, brief in _FEATURE_BRIEFS.items())
    flags = {**configuration(session), "growth_turn": growth_state}
    inspection = (
        " Call harness_status for live settings or learning state."
        if "harness_status" in tool_names
        else " Live inspection is unavailable to this agent; offer the relevant UI command."
    )
    catalog = (
        " Call harness_catalog for the full tool/provider/vocabulary catalog."
        if "harness_catalog" in tool_names
        else ""
    )
    return (
        "\n\nHarness capabilities (compact; full details on demand):\n"
        "Surfaces: terminal UI, CLI, HTTP/SSE API, MCP server/client, external harnesses.\n"
        "Mechanisms: sessions, steering, questions, terminal, context, memory, permissions, "
        "routing, orchestration, repair, jobs, skills, rag, workspace, web, artifacts, mcp, "
        "interop, media, observability, accounting.\n"
        "Tool categories: files/repo, terminal, web/browser, docs/RAG, memory, skills, "
        "agents/harnesses, routing, MCP, workspace. Use the offered function schemas for "
        "exact names.\n"
        "Feature briefs:\n"
        + facts
        + "\nCurrent configuration (enabled is not proof of successful learning): "
        + json.dumps(flags, separators=(",", ":"))
        + _mcp_connector_summary(tool_names)
        + "\nCall tool_load with names listed in its catalog when a needed tool is not "
        "in the current schemas; loaded schemas appear on the next step."
        + "\nAnswer feature questions from this brief; do not infer features from recalled "
        "chat or empty notes, claim unobserved improvement, or invent unavailable tools."
        + catalog
        + inspection
    )


def configuration(session: Session) -> dict:
    """Allowlisted knobs only; enabled configuration does not prove readiness."""
    memory = bool(config.get("memory_enabled"))
    growth = memory and bool(config.get("personality_growth_enabled"))
    emotion = config.get("persona_default_emotion")
    # This string setting is not enum-validated on input. Never expose arbitrary
    # text in the always-on prompt; unknown values use the topology's neutral profile.
    emotion = emotion.strip().lower() if isinstance(emotion, str) else ""
    if emotion not in EmotionTopology.EMOTION_PROFILES:
        emotion = "unrecognized (neutral fallback)"
    mode = getattr(session, "execution_mode", None)
    mode = mode if mode in MODES else "unknown"
    return {
        "memory": memory,
        "growth": growth,
        "rsi": growth and bool(config.get("rsi_enabled")),
        "persona_lenses": bool(config.get("persona_memory_enabled")),
        "default_emotion": emotion,
        "persona_set": bool(config.get("persona_description")),
        "learning_review": bool(config.get("learning_review_enabled")),
        "rag": bool(config.get("rag_enabled")),
        "compaction": bool(config.get("compression_enabled")),
        "mode": mode,
        "workspace": _workspace_summary(),
    }


def _workspace_summary() -> object:
    """Live workspace facts for this process, never file or buffer text.

    Computed from the active service's truth (``status_facts``): availability,
    root *label* (basename only — full private paths stay out of the prompt),
    per-language server states and the operation names the read-only
    ``code_intelligence`` tool exposes. ``"none"`` when no workspace is open
    in this process; a failure to read live facts is reported as
    ``"unavailable"`` rather than as an absent workspace.
    """
    try:
        service = active_workspace_service()
        facts = service.status_facts() if service is not None else None
    except Exception:
        return "unavailable"
    if facts is None:
        return "none"
    return {
        "available": True,
        "root_label": facts["root_label"],
        "trusted": facts["trusted"],
        "profiles": {language: row["state"] for language, row in facts["profiles"].items()},
        "operations": list(CODE_INTELLIGENCE_OPERATIONS),
    }


def _mcp_connector_summary(tool_names: set[str]) -> str:
    """Bounded connected-connector inventory for automatic MCP routing.

    Reads only the in-process registry and connection state: no refresh,
    network call, connection launch, secret material or tool schema. When the
    runtime has not created its connector service yet, the section is omitted.
    """
    if not {"mcp_list", "mcp_call"} <= tool_names:
        return ""
    try:
        from machina.core.runtime import runtime_services

        service = getattr(runtime_services, "_connector_service", None)
        if service is None:
            return ""
        rows: list[str] = []
        for config in service.registry.list():
            if not getattr(config, "enabled", True):
                continue
            status = service.manager.status(config.id)
            state = str(status.get("state") or "unknown")
            tools = service.registry.tools_for(config.id)
            names = [str(tool.remote_name or tool.name) for tool in tools[:8]]
            suffix = f"; tools: {', '.join(names)}" if names else "; no catalog cached"
            rows.append(f"- {config.id} ({state}{suffix})")
        if not rows:
            return ""
        return (
            "\n\nMCP connectors in this runtime (use them automatically when the "
            "request needs their app or service):\n"
            + "\n".join(rows)
            + "\nCall mcp_list with connector=<id> for the full tool page, "
            "mcp_describe for the exact schema, then mcp_call. A connected "
            "connector must not be reported as unavailable."
        )
    except Exception:
        return ""


def capability_context(session: Session, *, tool_names: set[str], growth_state: str) -> str:
    """Always explain features, even with a custom prompt or failed growth store."""
    from machina.core.capability_catalog import capability_index

    facts = "\n".join(f"{name}: {description}" for name, description in FEATURES.items())
    flags = {**configuration(session), "growth_turn": growth_state}
    inspection = (
        " Use harness_status only for requested live settings or learning state."
        if "harness_status" in tool_names
        else " Live inspection is unavailable to this agent; offer the relevant UI command."
    )
    return (
        "\n\nHarness capabilities (implementation facts):\n"
        + capability_index(offered=tool_names)
        + "\n\nFeature details:\n"
        + facts
        + "\nCurrent configuration (enabled is not proof of successful learning): "
        + json.dumps(flags, separators=(",", ":"))
        + _mcp_connector_summary(tool_names)
        + "\nAnswer feature questions directly from this brief. Do not infer features "
        "from recalled chat or empty notes, claim unobserved improvement, or invent unavailable tools."
        + inspection
    )
