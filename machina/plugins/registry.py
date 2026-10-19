"""Plugin registry and failure-isolated hook dispatch."""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import inspect
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

HOOKS = frozenset(
    {"pre_agent_run", "post_agent_run", "on_tool_call", "on_tool_result", "on_memory_extract"}
)

#: Sync hooks run on a dedicated bounded pool. A hook that times out keeps its
#: worker thread running (threads cannot be cancelled); leaking those into the
#: default executor would starve unrelated ``asyncio.to_thread`` users (gateway
#: workspace/DB offloads) with no bound at all.
_PLUGIN_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="machina-plugin"
)

#: A plugin that fails this many times in a row is skipped for the cooldown.
#: A hung sync hook holds one of the four workers while it times out, so this
#: bounds how often the same plugin can re-hang the pool.
_PLUGIN_FAILURE_LIMIT = 3
_PLUGIN_COOLDOWN_SECONDS = 30.0


class PluginRegistry:
    def __init__(self, timeout_seconds: float | None = None) -> None:
        self._plugins: dict[str, Any] = {}
        self._failures: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        try:
            configured = (
                timeout_seconds
                if timeout_seconds is not None
                else float(os.environ.get("MACHINA_PLUGIN_TIMEOUT_SECONDS", "5"))
            )
            self.timeout_seconds = max(0.01, configured)
        except ValueError:
            self.timeout_seconds = 5.0

    def register(self, plugin: Any) -> None:
        name = getattr(plugin, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("plugin must have a non-empty name")
        if name in self._plugins:
            raise ValueError(f"plugin {name!r} is already registered")
        self._plugins[name] = plugin

    def unregister(self, name: str) -> None:
        self._plugins.pop(name, None)
        self._failures.pop(name, None)
        self._cooldown_until.pop(name, None)

    def list(self) -> list[str]:
        return sorted(self._plugins)

    def _record_outcome(self, name: str, *, failed: bool, now: float) -> None:
        """Circuit breaker: skip a plugin that keeps failing every call."""
        if not failed:
            self._failures.pop(name, None)
            return
        count = self._failures.get(name, 0) + 1
        if count < _PLUGIN_FAILURE_LIMIT:
            self._failures[name] = count
            return
        self._failures.pop(name, None)
        self._cooldown_until[name] = now + _PLUGIN_COOLDOWN_SECONDS
        logger.warning(
            "Plugin %s failed %d times in a row; skipped for %.0fs",
            name,
            _PLUGIN_FAILURE_LIMIT,
            _PLUGIN_COOLDOWN_SECONDS,
        )

    async def dispatch(self, hook: str, *args: Any) -> None:
        if hook not in HOOKS:
            raise ValueError(f"unknown plugin hook {hook!r}")
        now = time.monotonic()
        for name, plugin in tuple(self._plugins.items()):
            if now < self._cooldown_until.get(name, 0.0):
                continue
            method = getattr(plugin, hook, None)
            if method is None:
                continue
            failed = False
            try:
                if inspect.iscoroutinefunction(method):
                    result = method(*args)
                else:
                    loop = asyncio.get_running_loop()
                    result = await asyncio.wait_for(
                        loop.run_in_executor(_PLUGIN_EXECUTOR, functools.partial(method, *args)),
                        timeout=self.timeout_seconds,
                    )
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, timeout=self.timeout_seconds)
            except Exception:
                failed = True
                logger.exception("Plugin %s failed in %s", name, hook)
            self._record_outcome(name, failed=failed, now=time.monotonic())


plugin_registry = PluginRegistry()
