"""Resident buddy: the always-on notch agent's durable core."""

from __future__ import annotations

from machina.resident import store
from machina.resident.runtime import (
    core_memory_block,
    ensure_session,
    profile_status,
    resolve_approval,
)

__all__ = [
    "core_memory_block",
    "ensure_session",
    "profile_status",
    "resolve_approval",
    "store",
]
