"""Live capability inventory for installed external agent harnesses.

`interop.detect()` answers "installed / registered / can run". This module adds
the missing "what can it actually do?" layer: available models, agents, MCP
servers, and feature probes. Results are cached with provenance and staleness;
credentials and auth tokens are never persisted.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from machina import interop
from machina.memory.redaction import redact_secrets
from machina.observability.diagnostics import record_failure

CACHE_TTL_SECONDS = 600.0
CACHE_ENV = "MACHINA_HARNESS_FEATURES_FILE"
_MAX_LINES = 200
_MAX_TEXT = 4000
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


@dataclass
class HarnessProfile:
    platform: str
    label: str
    installed: bool
    version: str | None = None
    binary: str | None = None
    running: bool = False
    registered: bool = False
    models: tuple[str, ...] = ()
    agents: tuple[dict[str, str], ...] = ()
    mcp_servers: tuple[str, ...] = ()
    auth_providers: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    plugins: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    detected_at: str = ""
    source: str = "probe"

    @property
    def features(self) -> dict[str, Any]:
        return {
            "models": len(self.models),
            "agents": len(self.agents),
            "mcp_servers": len(self.mcp_servers),
            "auth_providers": len(self.auth_providers),
            "commands": len(self.commands),
            "plugins": len(self.plugins),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "label": self.label,
            "installed": self.installed,
            "version": self.version,
            "binary": self.binary,
            "running": self.running,
            "registered": self.registered,
            "models": list(self.models),
            "agents": list(self.agents),
            "mcp_servers": list(self.mcp_servers),
            "auth_providers": list(self.auth_providers),
            "commands": list(self.commands),
            "plugins": list(self.plugins),
            "features": self.features,
            "notes": list(self.notes),
            "detected_at": self.detected_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> HarnessProfile:
        return cls(
            platform=str(payload.get("platform") or ""),
            label=str(payload.get("label") or payload.get("platform") or ""),
            installed=bool(payload.get("installed")),
            version=payload.get("version"),
            binary=payload.get("binary"),
            running=bool(payload.get("running")),
            registered=bool(payload.get("registered")),
            models=tuple(str(item) for item in payload.get("models") or ()),
            agents=tuple(
                {str(k): str(v) for k, v in entry.items()}
                for entry in payload.get("agents") or ()
                if isinstance(entry, dict)
            ),
            mcp_servers=tuple(str(item) for item in payload.get("mcp_servers") or ()),
            auth_providers=tuple(str(item) for item in payload.get("auth_providers") or ()),
            commands=tuple(str(item) for item in payload.get("commands") or ()),
            plugins=tuple(str(item) for item in payload.get("plugins") or ()),
            notes=tuple(str(item) for item in payload.get("notes") or ()),
            detected_at=str(payload.get("detected_at") or ""),
            source=str(payload.get("source") or "cache"),
        )


def cache_path() -> Path:
    override = os.environ.get(CACHE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    home = ""
    try:
        from machina.core.config import config

        home = str(config.get("machina_home") or "")
    except Exception:
        home = ""
    root = Path(home).expanduser() if home else Path.home() / ".machina"
    return root / "harness_features.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean(text: str) -> str:
    stripped = _ANSI_RE.sub("", str(text or "")).strip()
    try:
        return redact_secrets(stripped).text[:_MAX_TEXT]
    except Exception:
        return "[redaction unavailable]"


def _probe(binary: str, args: tuple[str, ...], *, timeout: float = 10.0) -> interop._ProcessResult:
    name = Path(binary).name.lower()
    if "codex" in name:
        key = "codex"
    elif "hermes" in name:
        key = "hermes"
    else:
        key = "opencode"
    spec = interop.PLATFORMS.get(key)
    env = interop._child_env(spec) if spec is not None else interop._child_process().sanitized_env(None)
    env["NO_COLOR"] = "1"
    return interop._run([binary, *args], env=env, timeout=timeout)


def _parse_lines(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for line in _clean(text).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        values.append(line[:300])
        if len(values) >= _MAX_LINES:
            break
    return tuple(values)


def _parse_models(text: str) -> tuple[str, ...]:
    models: list[str] = []
    for line in _parse_lines(text):
        # opencode models prints provider/model, one per line.
        token = line.split()[0].strip()
        if "/" in token and len(token) <= 200:
            models.append(token)
    return tuple(dict.fromkeys(models))


def _parse_agents(text: str) -> tuple[dict[str, str], ...]:
    try:
        data = json.loads(text)
    except ValueError:
        return ()
    if not isinstance(data, list):
        return ()
    agents: list[dict[str, str]] = []
    for entry in data[:_MAX_LINES]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("id") or entry.get("name") or "").strip()
        if not name:
            continue
        agents.append(
            {
                "id": _clean(name)[:120],
                "name": _clean(str(entry.get("name") or name))[:120],
                "description": _clean(str(entry.get("description") or ""))[:300],
                "mode": _clean(str(entry.get("mode") or ""))[:40],
            }
        )
    return tuple(agents)


def _parse_auth(text: str) -> tuple[str, ...]:
    providers: list[str] = []
    for line in _parse_lines(text):
        token = line.split()[0].strip()
        if token and token.lower() not in {"provider", "providers"}:
            providers.append(token[:80])
    return tuple(dict.fromkeys(providers))


def _parse_mcp(text: str) -> tuple[str, ...]:
    names: list[str] = []
    for line in _parse_lines(text):
        token = line.split()[0].strip().lstrip("-* ")
        if token and token.lower() not in {"name", "mcp", "servers", "server", "status"}:
            names.append(token[:120])
    return tuple(dict.fromkeys(names))


def _open_features(binary: str, profile: HarnessProfile) -> None:
    notes: list[str] = []
    try:
        result = _probe(binary, ("models",), timeout=10.0)
        profile.models = _parse_models(result.output or result.stderr)
        if result.returncode not in (0, None):
            notes.append("opencode models probe failed")
    except Exception as error:
        record_failure("harness_inventory.opencode.models", error)
        notes.append("opencode models probe unavailable")
    try:
        result = _probe(binary, ("debug", "agents"), timeout=10.0)
        profile.agents = _parse_agents(result.output)
        if not profile.agents and result.returncode not in (0, None):
            notes.append("opencode agents probe failed")
    except Exception as error:
        record_failure("harness_inventory.opencode.agents", error)
        notes.append("opencode agents probe unavailable")
    try:
        result = _probe(binary, ("auth", "list"), timeout=8.0)
        profile.auth_providers = _parse_auth(result.output)
    except Exception:
        pass
    try:
        result = _probe(binary, ("mcp", "list"), timeout=10.0)
        profile.mcp_servers = _parse_mcp(result.output)
    except Exception:
        pass
    profile.notes = (*profile.notes, *notes)


def _codex_features(binary: str, profile: HarnessProfile) -> None:
    notes: list[str] = []
    try:
        result = _probe(binary, ("mcp", "list", "--json"), timeout=10.0)
        try:
            rows = json.loads(result.output)
            if isinstance(rows, list):
                profile.mcp_servers = tuple(
                    str(row.get("name"))
                    for row in rows
                    if isinstance(row, dict) and row.get("name")
                )
        except ValueError:
            profile.mcp_servers = _parse_mcp(result.output)
    except Exception:
        notes.append("codex MCP probe unavailable")
    try:
        result = _probe(binary, ("plugin", "list"), timeout=8.0)
        profile.plugins = _parse_lines(result.output)
    except Exception:
        pass
    profile.notes = (*profile.notes, *notes)


def _hermes_features(binary: str, profile: HarnessProfile) -> None:
    try:
        result = _probe(binary, ("mcp", "list"), timeout=10.0)
        profile.mcp_servers = _parse_mcp(result.output)
    except Exception:
        pass


def _discover_one(key: str, detected: Any) -> HarnessProfile:
    spec = interop.PLATFORMS[key]
    profile = HarnessProfile(
        platform=key,
        label=spec.label,
        installed=bool(detected.installed),
        version=detected.version,
        binary=detected.binary,
        running=bool(detected.running),
        registered=bool(detected.registered),
        detected_at=_now_iso(),
        source="probe",
    )
    if not profile.installed or not profile.binary:
        profile.notes = tuple(detected.notes or ())
        return profile
    if key == "opencode":
        _open_features(profile.binary, profile)
    elif key == "codex":
        _codex_features(profile.binary, profile)
    elif key == "hermes":
        _hermes_features(profile.binary, profile)
    profile.notes = tuple(dict.fromkeys((*profile.notes, *detected.notes)))
    return profile


def discover(*, platforms: tuple[str, ...] | None = None, use_parallel: bool = True) -> dict:
    """Probe installed harnesses and return capability profiles.

    This is the explicit refresh path. Callers on a latency-sensitive status
    path should use :func:`cached` instead.
    """
    keys = platforms or tuple(interop.PLATFORMS)
    if use_parallel and len(keys) > 1:
        detected = interop.detect_parallel(keys)
    else:
        detected = interop.detect(keys)
    profiles = {key: _discover_one(key, detected[key]) for key in keys if key in detected}
    _write_cache(profiles)
    return {key: profile.to_dict() for key, profile in profiles.items()}


def _write_cache(profiles: dict[str, HarnessProfile]) -> None:
    path = cache_path()
    payload = {
        "version": 1,
        "generated_at": _now_iso(),
        "profiles": {key: profile.to_dict() for key, profile in profiles.items()},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".harness-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception as error:
        record_failure("harness_inventory.write_cache", error)


def read_cache() -> dict[str, Any]:
    path = cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"profiles": {}, "generated_at": None, "stale": True, "source": "absent"}
    if not isinstance(payload, dict):
        return {"profiles": {}, "generated_at": None, "stale": True, "source": "invalid"}
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    generated = payload.get("generated_at")
    age = None
    stale = True
    if isinstance(generated, str):
        try:
            age = max(0.0, time.time() - datetime.fromisoformat(generated).timestamp())
            stale = age > CACHE_TTL_SECONDS
        except ValueError:
            stale = True
    return {
        "profiles": profiles,
        "generated_at": generated,
        "age_seconds": age,
        "stale": stale,
        "source": "cache",
    }


def cached(*, refresh: bool = False, platforms: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Return cached capability profiles, refreshing only when requested."""
    if refresh:
        profiles = discover(platforms=platforms)
        return {"profiles": profiles, "generated_at": _now_iso(), "stale": False, "source": "probe"}
    return read_cache()


def clear_cache() -> None:
    try:
        cache_path().unlink()
    except OSError:
        pass


def summary() -> dict[str, Any]:
    """Compact cached view for harness_status (no subprocess spawns)."""
    payload = read_cache()
    profiles = payload.get("profiles") or {}
    compact: dict[str, Any] = {}
    for key, entry in profiles.items():
        if not isinstance(entry, dict):
            continue
        profile = HarnessProfile.from_dict(entry)
        compact[key] = {
            "installed": profile.installed,
            "version": profile.version,
            "registered": profile.registered,
            "features": profile.features,
            "detected_at": profile.detected_at,
        }
    return {
        "generated_at": payload.get("generated_at"),
        "stale": payload.get("stale"),
        "profiles": compact,
    }


__all__ = [
    "CACHE_TTL_SECONDS",
    "HarnessProfile",
    "cache_path",
    "cached",
    "clear_cache",
    "discover",
    "read_cache",
    "summary",
]
