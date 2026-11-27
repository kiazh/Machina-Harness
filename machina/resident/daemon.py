"""Autonomous resident heartbeat.

`/agent start` unpauses Maki and starts this loop. It wakes only when there is
durable work: an active goal or a queued non-screen event. It never starts a
second turn while one is running, and it marks claimed events after submitting
the heartbeat so context observations do not create an infinite wake loop.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from machina.resident import runtime, store

DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 5


def configured_interval() -> int:
    raw = os.environ.get("MACHINA_RESIDENT_HEARTBEAT_SECONDS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_INTERVAL_SECONDS
    except ValueError:
        value = DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(value, 3_600))


class ResidentDaemon:
    def __init__(self, gateway: Any, *, interval: int | None = None) -> None:
        self.gateway = gateway
        self.interval = max(MIN_INTERVAL_SECONDS, min(interval or configured_interval(), 3_600))
        self._task: asyncio.Task | None = None
        self.last_tick: str | None = None
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, *, interval: int | None = None) -> None:
        if interval is not None:
            self.interval = max(MIN_INTERVAL_SECONDS, min(interval, 3_600))
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="maki-resident-heartbeat")

    def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "intervalSeconds": self.interval,
            "lastTick": self.last_tick,
            "lastError": self.last_error,
        }

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.interval)
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # keep the resident alive
                    self.last_error = f"{type(error).__name__}: {error}"[:300]
        except asyncio.CancelledError:
            return

    async def tick(self) -> None:
        profile = await store.get_profile()
        if profile is None or profile.get("paused"):
            return
        profile, session = await runtime.ensure_session()
        if self.gateway.turn_running(session.id):
            return
        events = await store.queued_events(limit=10)
        goals = await store.active_goals(limit=5)
        if not events and not goals:
            return

        lines = [
            "AUTONOMOUS HEARTBEAT.",
            f"Current time: {datetime.now(UTC).isoformat()}",
            "You are Maki, running unattended. Take at most one bounded step toward an "
            "active goal or a queued event. Use your grants; leave anything outside a "
            "grant queued for the user. Do not repeat work already done. If nothing "
            "needs action, reply exactly HEARTBEAT_OK and stop.",
        ]
        if goals:
            lines.append("Active goals:")
            for goal in goals:
                detail = str(goal.get("detail") or "").strip()
                lines.append(f"- {goal.get('title')}" + (f": {detail}" if detail else ""))
        if events:
            lines.append("Queued events:")
            for event in events:
                payload = event.get("payload") or {}
                summary = ", ".join(f"{key}={value}" for key, value in payload.items())
                lines.append(f"- {event.get('source')}/{event.get('kind')}: {summary}")
        text = "\n".join(lines)
        try:
            await self.gateway._prompt_submit({"sessionId": str(session.id), "text": text})
        except Exception:
            self.last_error = "heartbeat turn rejected"
            return
        await store.mark_events([str(event["id"]) for event in events], "done")
        self.last_tick = datetime.now(UTC).isoformat()
