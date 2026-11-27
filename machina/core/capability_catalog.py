"""Generated, read-only inventory of everything the harness can do.

The always-on prompt gets a compact index built from the constants below; the
``harness_catalog`` tool returns the detailed sections. Names are pinned to the
live registries by ``tests/test_capability_catalog.py`` so the model's
self-knowledge cannot silently drift from the implementation.

No database, filesystem, provider request, credential, or secret is read here.
"""

from __future__ import annotations

from typing import Any

from machina.core.provider import PROVIDERS
from machina.core.routing.types import (
    COST_TIERS,
    ORCHESTRATION_MODES,
    ROLES,
    TRANSPORTS,
)
from machina.core.session_mode import MODES

SECTIONS: tuple[str, ...] = ("summary", "tools", "providers", "vocabularies", "surfaces", "all")

SURFACES: tuple[str, ...] = (
    "terminal UI (chat, editor, explorer, approval cards)",
    "CLI (headless chat, sessions, memory, jobs, routing)",
    "HTTP API (REST + server-sent events)",
    "MCP server (inbound tools for other assistants)",
    "MCP client (outbound stdio and HTTP servers)",
    "external harness interop (OpenCode, Codex, Hermes)",
)

MECHANISMS: dict[str, str] = {
    "sessions": "durable conversations with pins, goals, fork, export, search, and resume",
    "steering": "queued mid-turn user guidance applied at safe tool boundaries",
    "questions": "interactive multiple-choice questions that pause and resume the current turn",
    "terminal": "foreground commands and background shell jobs with output, wait, and stop",
    "context": "typed chunks, token budget, projection, compaction/checkpoints, continuity, artifacts, reducers",
    "memory": "consolidation, scoring, dedupe, redaction/provenance, optional approval, hybrid retrieval, digest, growth/RSI",
    "permissions": "canonical digest, allow/deny/review, durable approvals and grants, modes, execution revalidation",
    "routing": "connected-model inventory; planner/supervisor/worker/utility fit; cost tiers; pins and fallbacks; native/proxy/MCP transports",
    "orchestration": "task classification, delegation, dependency-aware parallel plans, supervisor synthesis",
    "repair": "TaskRun phases, evidence tracking, turn/token caps, repeated-loop stops, verification gate",
    "jobs": "interval, cron, heartbeat, and script jobs; durable approval pause and explicit resume",
    "skills": "catalog-first, stage-aware loading, revisions, learning review, marketplace",
    "rag": "document ingestion, embeddings, hybrid search, reranking",
    "workspace": "editor, explorer, language servers, quick open, diagnostics, git review",
    "web": "search, extract, crawl, research, loopback browser verification",
    "artifacts": "store-before-publish, character paging, literal search, metadata inspection",
    "mcp": "catalog-first remote tools, namespaced calls, optional OAuth",
    "interop": "register Machina's MCP server with external harnesses, or delegate bounded work to them",
    "media": "inline images from the UI, pinned now-playing control",
    "observability": "audit events, traces, metrics, diagnostics, safe logging",
    "accounting": "normalized usage, cache categories, pricing provenance, budgets",
}

#: Every built-in tool, grouped for the prompt index. ``catalog`` also unions
#: the live registry, so a newly registered tool cannot disappear from the
#: detailed catalog; the test pins this map to the registry.
TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "files and repo": (
        "read_file",
        "write_file",
        "edit_file",
        "list_files",
        "search_files",
        "repo_stage",
        "repo_activate",
        "learn_directory",
    ),
    "terminal": ("terminal", "terminal_job"),
    "web and browser": (
        "web_search",
        "web_extract",
        "web_research",
        "web_crawl",
        "browser_verify",
        "web_verify",
    ),
    "documents and RAG": ("index_document", "search_documents"),
    "memory and recall": (
        "remember",
        "recall",
        "session_recall",
        "session_recall_window",
        "share_memory",
        "learn_personality",
    ),
    "skills": ("skill_list", "skill_read", "skill_resource"),
    "agents and harnesses": (
        "delegate",
        "delegate_status",
        "list_agents",
        "harness_delegate",
        "harness_job",
        "harness_status",
        "harness_features",
        "harness_catalog",
    ),
    "resident": (
        "resident_remember",
        "resident_pending_approvals",
        "resident_proxy_resolve",
    ),
    "routing": ("route_explain", "model_inventory"),
    "artifacts": ("artifact_describe", "artifact_read", "artifact_search"),
    "MCP": ("mcp_call", "mcp_connect", "mcp_describe", "mcp_disconnect", "mcp_list"),
    "workspace": ("code_intelligence",),
    "interaction": ("ask_user", "tool_load"),
}

VOCABULARIES: dict[str, tuple[str, ...]] = {
    "modes": MODES,
    "terminal_backends": ("disabled", "local", "docker"),
    "orchestration_modes": ORCHESTRATION_MODES,
    "route_roles": ROLES,
    "transports": TRANSPORTS,
    "cost_tiers": COST_TIERS,
    "job_schedules": ("interval", "cron", "heartbeat"),
    "job_content": ("prompt", "script"),
    "job_states": ("idle", "running", "error", "paused_approval"),
    "approval_states": (
        "pending",
        "approved",
        "denied",
        "expired",
        "cancelled",
        "claimed",
        "completed",
        "failed",
    ),
    "session_states": ("active", "idle", "archived"),
    "delegation_states": (
        "queued",
        "running",
        "pending",
        "complete",
        "error",
        "cancelled",
        "waiting_approval",
        "interrupted",
    ),
    "memory_categories": ("preference", "decision", "fact", "event", "transient"),
    "usage_bases": ("provider", "provider_partial", "local_estimate", "missing", "failed"),
    "reducer_formats": ("tests", "diff", "json", "csv", "logs", "code", "head_tail"),
}

HUMAN_SURFACES: dict[str, str] = {
    "slash commands": "/help lists the terminal UI command registry; typing / filters it",
    "CLI": "`machina --help` and `<command> --help` list the command surface",
}


def _registry_tools() -> dict[str, Any]:
    """Lazy registry read: avoids an import cycle with ``tools.harness``."""
    try:
        import machina.tools  # noqa: F401 - registers every built-in tool
        from machina.tools.base import registry

        return dict(registry._tools)
    except Exception:
        return {}


def _tool_entries() -> list[dict[str, Any]]:
    tools = _registry_tools()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group, names in TOOL_GROUPS.items():
        for name in names:
            tool = tools.get(name)
            seen.add(name)
            rows.append(
                {
                    "name": name,
                    "group": group,
                    "effects": list(getattr(tool, "effects", ())),
                    "description": _one_line(getattr(tool, "description", "")),
                }
            )
    for name in sorted(set(tools) - seen):
        rows.append(
            {
                "name": name,
                "group": "other",
                "effects": list(getattr(tools[name], "effects", ())),
                "description": _one_line(getattr(tools[name], "description", "")),
            }
        )
    return rows


def _one_line(text: Any, limit: int = 160) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def capability_index(*, offered: set[str] | None = None) -> str:
    """Compact always-on index; ``offered`` is the exact callable set."""
    offered = set(offered or ())
    tool_groups = "; ".join(f"{group}: {', '.join(names)}" for group, names in TOOL_GROUPS.items())
    vocab = "; ".join(f"{name}: {'/'.join(values)}" for name, values in VOCABULARIES.items())
    surfaces = "; ".join(SURFACES)
    mechanisms = ", ".join(MECHANISMS)
    offered_text = ", ".join(sorted(offered)) if offered else "none"
    return (
        "Capability index (generated from live registries):\n"
        f"Surfaces: {surfaces}.\n"
        f"Mechanisms: {mechanisms}.\n"
        f"Built-in tools: {tool_groups}.\n"
        f"Providers: {', '.join(PROVIDERS)}.\n"
        f"Vocabularies: {vocab}.\n"
        f"Offered to this agent now: {offered_text}.\n"
        "Only offered tools are callable now; never claim or attempt an unoffered one. "
        "Every built-in tool exists in the harness; harness_catalog returns effects and "
        "descriptions. Human surfaces: /help and `machina --help`."
    )


def catalog(section: str = "summary") -> dict[str, Any]:
    """Return one catalog section, or ``all`` for the complete inventory."""
    if section not in SECTIONS:
        raise ValueError(f"section must be one of: {', '.join(SECTIONS)}")
    tools = _tool_entries()
    payload: dict[str, Any] = {"tool_effect": "read-only inspection", "section": section}
    if section in {"summary", "all"}:
        payload["tool_groups"] = {group: list(names) for group, names in TOOL_GROUPS.items()}
    if section in {"tools", "all"}:
        payload["tools"] = tools
    if section in {"summary", "providers", "all"}:
        payload["providers"] = list(PROVIDERS)
    if section in {"summary", "vocabularies", "all"}:
        payload["vocabularies"] = {name: list(values) for name, values in VOCABULARIES.items()}
    if section in {"summary", "surfaces", "all"}:
        payload["surfaces"] = list(SURFACES)
        payload["mechanisms"] = dict(MECHANISMS)
        payload["human_surfaces"] = dict(HUMAN_SURFACES)
    payload["counts"] = {
        "tools": len(tools),
        "providers": len(PROVIDERS),
        "surfaces": len(SURFACES),
        "mechanisms": len(MECHANISMS),
    }
    return payload
