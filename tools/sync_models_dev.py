"""Generate Machina's compact model snapshot from an anomalyco/models.dev checkout.

Usage: python tools/sync_models_dev.py /path/to/models.dev
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from datetime import date
from pathlib import Path

PROVIDERS = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "deepseek": "deepseek",
    "xai": "xai",
    "groq": "groq",
    "mistral": "mistral",
    "togetherai": "together",
    "openrouter": "openrouter",
    "opencode": "opencode",
    "kimi-code-plan-global": "kimi",
}
PER_PROVIDER = {
    "openai": 12,
    "anthropic": 12,
    "google": 12,
    "deepseek": 10,
    "xai": 12,
    "groq": 12,
    "mistral": 12,
    "togetherai": 12,
    "openrouter": 20,
    "opencode": 12,
    "kimi": 10,
}
OUTPUT = Path(__file__).resolve().parents[1] / "machina" / "core" / "provider" / "models_dev.json"


def _read(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _row(path: Path, root: Path, repo: Path) -> dict | None:
    override = _read(path)
    base_model = str(override.get("base_model") or "").strip()
    base = _read(repo / "models" / f"{base_model}.toml") if base_model else {}
    data = _merge(base, override)
    modalities = data.get("modalities") or {}
    if "text" not in (modalities.get("output") or []):
        return None
    model_id = path.relative_to(root).with_suffix("").as_posix().lower()
    if any(
        token in model_id
        for token in (
            "voice",
            "tts",
            "transcri",
            "audio",
            "speech",
            "whisper",
            "image",
            "imagine",
            "video",
            "lyria",
            "banana",
            "seedream",
            "embed",
            "rerank",
            "ocr",
            "moderation",
            "safeguard",
            "realtime",
            "live-",
            "live",
            "guard",
            "robotics",
        )
    ):
        return None
    expires = data.get("expiration_date")
    if expires and str(expires) < date.today().isoformat():
        return None
    limits = data.get("limit") or {}
    if not int(limits.get("context") or 0):
        return None
    caps = data.get("capabilities") or {}
    return {
        "id": path.relative_to(root).with_suffix("").as_posix(),
        "name": str(data.get("name") or path.stem),
        "description": str(data.get("description") or ""),
        "family": str(data.get("family") or ""),
        "release_date": str(data.get("release_date") or ""),
        "context": int(limits.get("context") or 0),
        "output": int(limits.get("output") or 0),
        "reasoning": bool((caps.get("reasoning") or {}).get("supported")),
        "tools": bool((caps.get("tools") or {}).get("supported")),
        "efforts": [
            str(value)
            for value in ((caps.get("reasoning") or {}).get("effort") or [])
            if isinstance(value, str)
        ],
    }


def main() -> int:
    import sys

    repo = Path(sys.argv[1] if len(sys.argv) > 1 else "models.dev")
    snapshot = {
        "schema": 1,
        "source": "https://github.com/anomalyco/models.dev",
        "generated_at": date.today().isoformat(),
        "providers": {},
    }
    for source_name, provider in PROVIDERS.items():
        root = repo / "providers" / source_name / "models"
        if not root.is_dir():
            root = repo / "models" / source_name
        rows = [
            row
            for path in sorted(root.rglob("*.toml"))
            if (row := _row(path, root, repo)) is not None
        ]
        rows.sort(key=lambda row: (row["release_date"], row["id"]), reverse=True)
        # Prefer current rows, but keep a stable, bounded list per provider.
        snapshot["providers"][provider] = rows[: PER_PROVIDER.get(provider, 20)]
    OUTPUT.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes, sha256 {digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
