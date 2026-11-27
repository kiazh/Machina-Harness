"""Local notch token (0600) shared by the gateway API and the native shell.

The notch is local, but it reaches the gateway over loopback HTTP. This token
is a local-only credential: the API accepts it only on ``/rpc`` and only
enables the ``resident.*`` surface while it matches. It is never accepted for
host credentials or external-harness execution.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from machina.core.config import config

TOKEN_ENV = "MACHINA_NOTCH_TOKEN"


def notch_token_path() -> Path:
    override = os.environ.get("MACHINA_NOTCH_TOKEN_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    home = ""
    try:
        home = str(config.get("machina_home") or "")
    except Exception:
        home = ""
    root = Path(home).expanduser() if home else Path.home() / ".machina"
    return root / "notch.token"


def read_notch_token() -> str | None:
    value = os.environ.get(TOKEN_ENV, "").strip()
    if value:
        return value
    try:
        token = notch_token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def ensure_notch_token() -> str:
    existing = read_notch_token()
    if existing:
        return existing
    token = secrets.token_urlsafe(32)
    path = notch_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return token


def is_notch_token(presented: str | None) -> bool:
    expected = read_notch_token()
    if not expected or not presented:
        return False
    try:
        return hmac.compare_digest(presented.strip().encode("utf-8"), expected.encode("utf-8"))
    except (TypeError, UnicodeError):
        return False
