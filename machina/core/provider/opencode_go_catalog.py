"""OpenCode Go model catalog: live discovery with an offline snapshot.

The Go gateway publishes every model its subscription covers through
``/inference/go/v1/models``. Rows carry context/output limits, reasoning
efforts, modalities, and prices. The picker only offers chat models;
decision and embedding endpoints are not chat routes.

Discovery needs the signed-in account. The bundled snapshot keeps the
picker complete before sign-in and when the network is unavailable; a live
fetch refreshes it on disk for ``CACHE_TTL_SECONDS``.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from machina.core.config import config

MODELS_URL = "https://opencode.ai/inference/go/v1/models"
CACHE_TTL_SECONDS = 6 * 60 * 60
SNAPSHOT = Path(__file__).with_name("opencode_go_models.json")
SOURCE = "opencode-go"


def _optional_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _efforts(capabilities: dict[str, Any]) -> list[str]:
    reasoning = capabilities.get("reasoning")
    if not isinstance(reasoning, dict):
        return []
    if not reasoning.get("supported"):
        return []
    raw = reasoning.get("effort")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item.strip()]


def normalize_entry(entry: Any) -> dict[str, Any] | None:
    """One gateway model as the normalized picker/routing row."""
    if not isinstance(entry, dict):
        return None
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    if str(entry.get("type") or "chat") != "chat":
        return None
    capabilities = entry.get("capabilities")
    caps = capabilities if isinstance(capabilities, dict) else {}
    tools = caps.get("tools")
    limit = entry.get("limit")
    bounds = limit if isinstance(limit, dict) else {}
    return {
        "id": model_id.strip(),
        "displayName": str(entry.get("name") or model_id),
        "description": str(entry.get("description") or ""),
        "efforts": _efforts(caps),
        "contextWindow": _optional_int(bounds.get("context")),
        "maxOutputTokens": _optional_int(bounds.get("output")),
        "family": str(entry.get("family") or ""),
        "tools": bool(tools.get("supported")) if isinstance(tools, dict) else False,
        "reasoning": bool((caps.get("reasoning") or {}).get("supported"))
        if isinstance(caps.get("reasoning"), dict)
        else False,
        "releaseDate": str(entry.get("release_date") or ""),
        "source": SOURCE,
    }


def normalize_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in data:
        row = normalize_entry(entry)
        if row is None or row["id"] in seen:
            continue
        seen.add(row["id"])
        rows.append(row)
    return rows


def _home() -> Path:
    home = ""
    with suppress(Exception):
        home = str(config.get("machina_home") or "")
    return Path(home).expanduser() if home else Path.home() / ".machina"


def cache_path() -> Path:
    override = os.environ.get("MACHINA_OPENCODE_GO_CATALOG", "").strip()
    return Path(override).expanduser() if override else _home() / "cache" / "opencode-go-models.json"


@lru_cache(maxsize=1)
def _read_bundled() -> list[dict[str, Any]]:
    try:
        payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]


_cache_memo: tuple[str, float, list[dict[str, Any]], float] | None = None


def _read_cache() -> tuple[list[dict[str, Any]], float]:
    global _cache_memo
    path = cache_path()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = 0.0
    key = str(path)
    if _cache_memo is not None and _cache_memo[0] == key and _cache_memo[1] == stamp:
        return _cache_memo[2], _cache_memo[3]
    rows: list[dict[str, Any]] = []
    fetched_at = 0.0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("models"), list):
        rows = [
            row
            for row in payload["models"]
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
        raw = payload.get("fetched_at")
        fetched_at = float(raw) if isinstance(raw, (int, float)) else 0.0
    _cache_memo = (key, stamp, rows, fetched_at)
    return rows, fetched_at


def _write_cache(rows: list[dict[str, Any]], *, now: float) -> None:
    target = cache_path()
    temp = target.with_suffix(".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(
            json.dumps({"fetched_at": now, "models": rows}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, target)
    except OSError:
        with suppress(OSError):
            temp.unlink()


def cached_models(*, now: float | None = None, include_stale: bool = True) -> list[dict[str, Any]]:
    """Rows already on disk (fresh unless ``include_stale``), else the snapshot."""
    rows, fetched_at = _read_cache()
    if rows:
        if include_stale or (now or time.time()) - fetched_at < CACHE_TTL_SECONDS:
            return rows
    return _read_bundled()


def fetch_models(*, client: httpx.Client | None = None, timeout: float = 20.0) -> list[dict[str, Any]]:
    """Live catalog for the signed-in account; empty when unavailable."""
    from machina.security import opencode_go_auth

    tokens = opencode_go_auth.load_tokens()
    if not tokens or not tokens.get("access_token"):
        return []
    owns = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.get(
            MODELS_URL,
            headers={
                "Authorization": f"Bearer {tokens['access_token']}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        if response.status_code != 200:
            return []
        return normalize_payload(response.json())
    except (httpx.HTTPError, ValueError):
        return []
    finally:
        if owns:
            http.close()


def models(
    *, refresh: bool = False, client: httpx.Client | None = None, now: float | None = None
) -> list[dict[str, Any]]:
    """Fresh cached rows, live discovery, then stale cache, then snapshot."""
    stamp = time.time() if now is None else now
    rows, fetched_at = _read_cache()
    if rows and not refresh and stamp - fetched_at < CACHE_TTL_SECONDS:
        return rows
    live = fetch_models(client=client)
    if live:
        _write_cache(live, now=stamp)
        return live
    if rows:
        return rows
    return _read_bundled()


def context_window(model: str) -> int | None:
    """Documented window for one Go model, network-free."""
    name = str(model or "").strip()
    if not name:
        return None
    for row in cached_models():
        if row.get("id") == name:
            value = row.get("contextWindow")
            return int(value) if isinstance(value, int) and value > 0 else None
    return None
