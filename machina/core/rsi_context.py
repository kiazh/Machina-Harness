"""Per-turn growth/RSI context shared by the loop and context preparer."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

growth_episode: ContextVar[uuid.UUID | None] = ContextVar("growth_episode", default=None)
rsi_choice: ContextVar[dict | None] = ContextVar("rsi_choice", default=None)
rsi_skills: ContextVar[list[str] | None] = ContextVar("rsi_skills", default=None)

__all__ = ["growth_episode", "rsi_choice", "rsi_skills"]
