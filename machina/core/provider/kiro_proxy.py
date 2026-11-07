"""Kiro local-proxy provider helpers.

Kiro does not expose a public chat API. This provider targets a local
OpenAI-compatible translator (for example ``jwadow/kiro-gateway``) that reads
the user's local Kiro CLI credentials and serves ``/v1/models`` and
``/v1/chat/completions`` on loopback.

Community interoperability only: Kiro's terms prohibit third-party harnesses
that route requests outside its native interfaces, and this path is not
supported by AWS. Prefer the official ``kiro-cli acp`` integration where one
is available.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http import client as http_client
from typing import Any
from urllib.parse import urlsplit

#: Default endpoint exposed by the Python gateway's documented setup.
DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"

#: The model picker calls every provider concurrently, so Kiro discovery must
#: never wait on a dead or wedged loopback listener. A live local gateway
#: answers in milliseconds; a sub-second upper bound keeps the picker snappy.
MODELS_TIMEOUT_SECONDS = 0.75

#: TCP preflight bound. A refused connection is immediate, but Windows can
#: hold a dropped loopback connect for over a second without this.
PROBE_TIMEOUT_SECONDS = 0.05

#: Catalog cache lifetime. `/models catalog` bypasses this to force discovery.
CATALOG_TTL_SECONDS = 60.0

#: Bound on a loopback catalog body before JSON parsing (same intent as the
#: provider-store cap, but the local path avoids constructing an HTTPX client).
MAX_LOCAL_MODELS_BYTES = 1_048_576

#: Kiro CLI's default alias. The live catalog is authoritative; this only
#: seeds first-run configuration.
DEFAULT_MODEL = "auto"

#: Optional local proxy password. This is NOT a Kiro/AWS API key: the
#: gateway authenticates upstream with the Kiro account credentials in the
#: local Kiro CLI database. The documented gateway setup uses this demo
#: value for loopback requests, so Machina supplies it by default.
API_KEY_ENV = "KIRO_API_KEY"
DEFAULT_API_KEY = "dummy-local-only"

#: The full Kiro account catalog, in the order reported by ``kiro-cli``.
#: kiro-gateway's ``/v1/models`` only advertises a subset of these IDs; the
#: remaining aliases work as gateway pass-through models. Descriptions and
#: context windows mirror ``kiro-cli chat --list-models``.
MODEL_METADATA: dict[str, dict[str, Any]] = {
    "auto": {
        "displayName": "Kiro Auto",
        "description": "Models chosen by task for optimal usage and consistent quality",
        "contextWindow": 1_000_000,
    },
    "claude-opus-5.5": {
        "displayName": "Claude Opus 5.5",
        "description": "Claude Opus 5.5 model with 1M context window",
        "contextWindow": 1_000_000,
    },
    "claude-sonnet-5.5": {
        "displayName": "Claude Sonnet 5.5",
        "description": "Claude Sonnet 5.5 model with 1M context window",
        "contextWindow": 1_000_000,
    },
    "claude-haiku-5.5": {
        "displayName": "Claude Haiku 5.5",
        "description": "Claude Haiku 5.5 model with 1M context window",
        "contextWindow": 1_000_000,
    },
    "gpt-5.6-sol": {
        "displayName": "GPT-5.6 Sol",
        "description": "OpenAI GPT 5.6 Sol with 1M context window",
        "contextWindow": 986_000,
    },
    "gpt-5.6-terra": {
        "displayName": "GPT-5.6 Terra",
        "description": "OpenAI GPT 5.6 Terra with 1M context window",
        "contextWindow": 986_000,
    },
    "gpt-5.6-luna": {
        "displayName": "GPT-5.6 Luna",
        "description": "OpenAI GPT 5.6 Luna with 1M context window",
        "contextWindow": 986_000,
    },
    "kimi-k3": {
        "displayName": "Kimi K3",
        "description": "Kimi K3 is a Kiro Excluded Model. For more info, see documentation.",
        "contextWindow": 1_000_000,
    },
    "claude-opus-5": {
        "displayName": "Claude Opus 5",
        "description": "Claude Opus 5 model with 1M context window",
        "contextWindow": 1_000_000,
    },
    "claude-sonnet-5": {
        "displayName": "Claude Sonnet 5",
        "description": "Claude Sonnet 5 model with 1M context window",
        "contextWindow": 1_000_000,
    },
    "claude-opus-4.8": {
        "displayName": "Claude Opus 4.8",
        "description": "Claude Opus 4.8 model with 1M context window",
        "contextWindow": 1_000_000,
    },
    "claude-opus-4.7": {
        "displayName": "Claude Opus 4.7",
        "description": "Claude Opus 4.7 model with 1M context window",
        "contextWindow": 1_000_000,
    },
    "claude-opus-4.6": {
        "displayName": "Claude Opus 4.6",
        "description": "Claude Opus 4.6 model with 1M context window",
        "contextWindow": 1_000_000,
    },
    "claude-sonnet-4.6": {
        "displayName": "Claude Sonnet 4.6",
        "description": "Claude Sonnet 4.6 model with 1M context window",
        "contextWindow": 1_000_000,
    },
    "claude-opus-4.5": {
        "displayName": "Claude Opus 4.5",
        "description": "Claude Opus 4.5 model",
        "contextWindow": 200_000,
    },
    "claude-sonnet-4.5": {
        "displayName": "Claude Sonnet 4.5",
        "description": "Claude Sonnet 4.5 model",
        "contextWindow": 200_000,
    },
    "claude-haiku-4.5": {
        "displayName": "Claude Haiku 4.5",
        "description": "The latest Claude Haiku model",
        "contextWindow": 200_000,
    },
    "deepseek-3.2": {
        "displayName": "DeepSeek V3.2",
        "description": "Experimental preview of DeepSeek V3.2",
        "contextWindow": 164_000,
    },
    "minimax-m2.5": {
        "displayName": "MiniMax M2.5",
        "description": "MiniMax M2.5 model",
        "contextWindow": 196_000,
    },
    "minimax-m2.1": {
        "displayName": "MiniMax M2.1",
        "description": "Experimental preview of MiniMax M2.1",
        "contextWindow": 196_000,
    },
    "glm-5": {
        "displayName": "GLM-5",
        "description": "GLM-5 model",
        "contextWindow": 200_000,
    },
    "qwen3-coder-next": {
        "displayName": "Qwen3 Coder Next",
        "description": "Experimental preview of Qwen3 Coder Next",
        "contextWindow": 256_000,
    },
}

FALLBACK_MODELS: tuple[dict[str, Any], ...] = tuple(
    {
        "id": model_id,
        "displayName": metadata["displayName"],
        "description": metadata["description"],
        "contextWindow": metadata.get("contextWindow"),
        "maxContextWindow": None,
        "source": "fallback",
    }
    for model_id, metadata in MODEL_METADATA.items()
)

_CACHE_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()
# (base URL, monotonic timestamp, rows). Keyed by URL so a runtime config
# change cannot serve a catalog fetched from another endpoint.
_CATALOG_CACHE: tuple[str, float, list[dict[str, Any]]] | None = None


class KiroGatewayUnavailable(RuntimeError):
    """The local proxy is not listening (a fast, user-actionable failure)."""


def base_url() -> str:
    """Resolved proxy base URL (loopback by default): config -> default."""
    from machina.core.config import config

    raw = str(config.get("kiro_base_url") or "").strip().rstrip("/")
    return raw or DEFAULT_BASE_URL


def api_key() -> str:
    """Resolved local proxy password (not a Kiro account/API credential)."""
    from machina.core.config import config

    return str(config.get("kiro_api_key") or "").strip() or DEFAULT_API_KEY


def fallback_models() -> list[dict[str, Any]]:
    """A copy of the offline catalog so callers cannot mutate the constant."""
    return [dict(row) for row in FALLBACK_MODELS]


def canonical_model_id(identifier: str) -> str:
    """Map gateway-only spelling to the ID Kiro's runtime accepts.

    kiro-gateway advertises ``auto-kiro`` to avoid colliding with Cursor's
    own ``auto`` model, but the runtime expects the CLI alias ``auto``.
    """
    return "auto" if identifier == "auto-kiro" else identifier


def _copy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A shallow per-row copy so callers cannot mutate the cache."""
    return [dict(row) for row in rows]


def _cache_get(url: str) -> list[dict[str, Any]] | None:
    with _CACHE_LOCK:
        if _CATALOG_CACHE is None:
            return None
        cached_url, fetched_at, rows = _CATALOG_CACHE
        if cached_url != url or time.monotonic() - fetched_at >= CATALOG_TTL_SECONDS:
            return None
        return _copy_rows(rows)


def _cache_put(url: str, rows: list[dict[str, Any]]) -> None:
    global _CATALOG_CACHE
    with _CACHE_LOCK:
        _CATALOG_CACHE = (url, time.monotonic(), _copy_rows(rows))


def clear_catalog_cache() -> None:
    """Drop the TTL cache after the gateway starts, stops, or reconfigures."""
    global _CATALOG_CACHE
    with _CACHE_LOCK:
        _CATALOG_CACHE = None


def _is_loopback_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").strip().lower()
    return host in {"localhost", "::1"} or host.startswith("127.")


def gateway_reachable(
    url: str | None = None, *, timeout: float = PROBE_TIMEOUT_SECONDS
) -> bool:
    """Bounded TCP preflight for the local OpenAI-compatible gateway.

    This is deliberately a socket connect, not an HTTP request: a dead
    loopback port can hold an HTTP client for seconds, while TCP refusal is
    immediate. It never raises and never carries credentials.
    """
    target = url or base_url()
    try:
        parsed = urlsplit(target)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=max(0.01, timeout)):
            return True
    except (OSError, ValueError):
        return False


def _fetch_loopback_models(
    url: str, *, api_key: str, timeout: float
) -> list[dict[str, Any]]:
    """Fetch ``/models`` from the local proxy without constructing HTTPX.

    ``httpx.Client`` costs ~300 ms per construction on Windows (system
    certificate store load), which is absurd for a cleartext loopback
    request. ``http.client`` keeps the live picker path in the low
    milliseconds while preserving the byte cap and JSON validation.
    """
    parsed = urlsplit(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = f"{parsed.path.rstrip('/')}/models" or "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    connection_cls = (
        http_client.HTTPSConnection if parsed.scheme == "https" else http_client.HTTPConnection
    )
    connection = connection_cls(host, port, timeout=max(0.05, timeout))
    try:
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            status = response.status
            body = response.read(MAX_LOCAL_MODELS_BYTES + 1)
        except (OSError, http_client.HTTPException) as exc:
            raise KiroGatewayUnavailable(
                f"Kiro local gateway stopped answering at {url} "
                f"({type(exc).__name__}). Use /auth kiro start."
            ) from exc
    finally:
        connection.close()

    if status == 401:
        raise KiroGatewayUnavailable(
            "Kiro local gateway rejected the local proxy password (HTTP 401). "
            "Set KIRO_API_KEY to the gateway's PROXY_API_KEY."
        )
    if status != 200:
        raise KiroGatewayUnavailable(
            f"Kiro local gateway returned HTTP {status} from {url}."
        )
    if len(body) > MAX_LOCAL_MODELS_BYTES:
        raise KiroGatewayUnavailable("Kiro model-list response exceeded the byte limit.")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KiroGatewayUnavailable("Kiro model-list response was not JSON.") from exc
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise KiroGatewayUnavailable("Kiro model-list response had no 'data' array.")
    return [item if isinstance(item, dict) else {"id": str(item)} for item in rows]


def _fetch_catalog_rows(
    url: str, *, api_key: str, timeout: float
) -> list[dict[str, Any]]:
    """Route loopback to the lean client; non-loopback stays on the store."""
    if _is_loopback_url(url):
        return _fetch_loopback_models(url, api_key=api_key, timeout=timeout)
    from machina.security.providers import fetch_models as _fetch_models

    return _fetch_models(url, api_key=api_key, timeout=timeout)


def _live_models(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize gateway ``/models`` rows and enrich them from the catalog."""
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identifier = canonical_model_id(str(row.get("id") or "").strip())
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        metadata = MODEL_METADATA.get(identifier, {})
        context_window = row.get("contextWindow")
        if context_window is None:
            context_window = metadata.get("contextWindow")
        models.append(
            {
                "id": identifier,
                "displayName": metadata.get("displayName", identifier),
                "description": metadata.get("description", "Kiro model"),
                "contextWindow": context_window,
                "maxContextWindow": row.get("maxContextWindow"),
                "source": "live",
            }
        )
    return models


def _kiro_cli_path() -> str:
    """Locate the Kiro CLI on PATH or in its Windows install directory."""
    executable = shutil.which("kiro-cli")
    if executable:
        return executable
    local_app_data = os.environ.get("LOCALAPPDATA") if os.name == "nt" else ""
    if local_app_data:
        candidate = os.path.join(local_app_data, "Kiro-Cli", "kiro-cli.exe")
        if os.path.isfile(candidate):
            return candidate
    return ""


def cli_path() -> str:
    """Public, cheap Kiro CLI presence check (never runs ``whoami``)."""
    return _kiro_cli_path()


def _cli_models(*, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Read the authenticated Kiro CLI catalog when the CLI is installed.

    The gateway's ``/v1/models`` list can lag the account's real catalog;
    the CLI knows the full set of pass-through aliases and their context
    windows. Failures are intentionally ignored: a missing or logged-out
    CLI must not break the gateway-only path.
    """
    executable = _kiro_cli_path()
    if not executable:
        return []
    try:
        completed = subprocess.run(
            [executable, "chat", "--list-models", "--format", "json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return []
    rows = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = canonical_model_id(
            str(row.get("model_id") or row.get("model_name") or "").strip()
        )
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        metadata = MODEL_METADATA.get(identifier, {})
        context_window = row.get("context_window_tokens")
        if type(context_window) is not int or context_window <= 0:
            context_window = metadata.get("contextWindow")
        description = str(row.get("description") or "").strip()
        models.append(
            {
                "id": identifier,
                "displayName": metadata.get(
                    "displayName", str(row.get("model_name") or identifier)
                ),
                "description": description or metadata.get("description", "Kiro model"),
                "contextWindow": context_window,
                "maxContextWindow": None,
                "source": "catalog",
            }
        )
    return models


def _merge_catalogs(
    live_rows: list[dict[str, Any]], cli_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Overlay live metadata on the complete account catalog.

    kiro-gateway's list can advertise stale aliases (for example
    ``claude-sonnet-4``) that the account cannot actually invoke. When the
    authenticated CLI answers, its catalog is authoritative and gateway-only
    extras are dropped. Without the CLI, the bundled 22-model account catalog
    is the base so the picker always shows every alias, with any unknown
    gateway-advertised model appended.
    """
    if cli_rows:
        base = [dict(row) for row in cli_rows]
        extras: list[dict[str, Any]] = []
    else:
        # The gateway only advertises a subset. Keep the full verified CLI
        # catalog visible, then append any future aliases it reports.
        base = [dict(row) for row in FALLBACK_MODELS]
        known = {row["id"] for row in base}
        extras = [dict(row) for row in live_rows if row["id"] not in known]

    live_by_id = {row["id"]: row for row in live_rows}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in base:
        identifier = row["id"]
        if identifier in seen:
            continue
        seen.add(identifier)
        current = dict(row)
        live = live_by_id.get(identifier)
        if live is not None:
            current["source"] = "live"
            if live.get("contextWindow") is not None:
                current["contextWindow"] = live["contextWindow"]
            if live.get("maxContextWindow") is not None:
                current["maxContextWindow"] = live["maxContextWindow"]
        merged.append(current)
    for row in extras:
        identifier = row["id"]
        if identifier in seen:
            continue
        seen.add(identifier)
        merged.append(dict(row))
    return merged


def fetch_models(
    *, force: bool = False, timeout: float = MODELS_TIMEOUT_SECONDS
) -> list[dict[str, Any]]:
    """Fetch the gateway catalog with a bounded TTL cache.

    The normal picker path never invokes ``kiro-cli``: a dead loopback
    listener fails the TCP preflight immediately and a live one answers from
    the gateway. ``force`` (used by ``/models catalog``) bypasses the cache
    and runs the authenticated CLI discovery, whose 22-model list is
    authoritative. Transport/HTTP errors propagate with the endpoint named
    but never the credential.
    """
    url = base_url()
    if not force:
        cached = _cache_get(url)
        if cached is not None:
            return cached

    with _FETCH_LOCK:
        if not force:
            cached = _cache_get(url)
            if cached is not None:
                return cached
        if _is_loopback_url(url) and not gateway_reachable(url):
            raise KiroGatewayUnavailable(
                f"Kiro local gateway is not reachable at {url}. "
                "Start it in a terminal or use /auth kiro start."
            )
        live_rows = _live_models(_fetch_catalog_rows(url, api_key=api_key(), timeout=timeout))
        cli_rows = _cli_models() if force else []
        models = _merge_catalogs(live_rows, cli_rows)
        _cache_put(url, models)
        return _copy_rows(models)
