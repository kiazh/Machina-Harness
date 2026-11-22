"""Project-local defaults for the CLI and launched UI.

A repository may contain ``machina.json`` or ``.machina.json``. Only a small
allow-list of non-secret, non-authority settings is accepted. Project files can
choose behavior (model, provider, formatter, snapshots, compaction), but they
cannot grant execution authority, change permissions, or inject credentials.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_CONFIG_NAMES = ("machina.json", ".machina.json")
PROJECT_CONFIG_KEYS = frozenset(
    {
        "model",
        "provider",
        "reasoning_effort",
        "formatter",
        "snapshots_enabled",
        "auto_compaction_enabled",
        "output_policy",
        "meta_harness_hint",
        "skill_query_expansion_enabled",
        "retrieval_token_budget",
        "memory_enabled",
        "rag_enabled",
        "browser_verification",
    }
)
ENV_DISABLE = "MACHINA_PROJECT_CONFIG"


def discover(start: Path | None = None) -> Path | None:
    """Find the nearest project config from *start* through its ancestors."""
    current = (start or Path.cwd()).resolve()
    candidates = [current, *current.parents]
    for directory in candidates:
        for name in PROJECT_CONFIG_NAMES:
            path = directory / name
            if path.is_file():
                return path
    return None


def load(path: Path) -> dict[str, Any]:
    """Load and validate a project config file.

    Unknown keys are rejected rather than silently ignored: a typo in a config
    file should not look like a working setting. Secrets and authority keys are
    always rejected.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise ValueError(f"project config is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("project config must be a JSON object")
    unknown = sorted(set(payload) - PROJECT_CONFIG_KEYS)
    if unknown:
        raise ValueError("project config contains unsupported keys: " + ", ".join(unknown))
    if payload.get("formatter") is not None and not isinstance(payload["formatter"], str):
        raise ValueError("project config formatter must be a string")
    for key in (
        "snapshots_enabled",
        "auto_compaction_enabled",
        "meta_harness_hint",
        "skill_query_expansion_enabled",
        "memory_enabled",
        "rag_enabled",
    ):
        if key in payload and not isinstance(payload[key], bool):
            raise ValueError(f"project config {key} must be a boolean")
    for key in ("model", "provider", "reasoning_effort", "output_policy", "browser_verification"):
        if key in payload and not isinstance(payload[key], str):
            raise ValueError(f"project config {key} must be a string")
    if "retrieval_token_budget" in payload:
        value = payload["retrieval_token_budget"]
        if type(value) is not int or value < 0:
            raise ValueError("project config retrieval_token_budget must be a non-negative integer")
    return payload


def apply(start: Path | None = None) -> Path | None:
    """Apply project defaults in memory and to the child-process environment.

    Returns the config path when applied. A malformed file is ignored with a
    warning rather than preventing Machina from starting; the CLI doctor can
    report the same file explicitly.
    """
    if os.environ.get(ENV_DISABLE, "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    path = discover(start)
    if path is None:
        return None
    try:
        values = load(path)
    except ValueError as error:
        print(f"Machina project config ignored: {error}", file=sys.stderr)
        return None
    from machina.core.config import config

    for key, value in values.items():
        try:
            config.set(key, value, persist=False)
        except Exception as error:
            print(
                f"Machina project config ignored {key}: {type(error).__name__}",
                file=sys.stderr,
            )
            continue
        # Launched UI/gateway children need the same project defaults. These
        # are non-secret keys; credentials and authority are never written.
        os.environ[f"MACHINA_{key.upper()}"] = str(value)
    return path
