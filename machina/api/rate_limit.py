"""Per-client HTTP request throttling for the API."""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window per-client request throttle.

    A limit of 0 disables throttling (explicit opt-out); negative values are
    normalized to 0. Client identity is the transport peer only — the
    ``X-Forwarded-For`` header is never trusted (trivially spoofable) unless
    the deployment strips/sets it at a trusted edge proxy.
    """

    def __init__(
        self,
        app,
        requests_per_minute: int | None = None,
        ready_requests_per_minute: int = 60,
    ) -> None:
        super().__init__(app)
        self.limit = (
            requests_per_minute
            if requests_per_minute is not None
            else int(os.environ.get("MACHINA_HTTP_RATE_LIMIT", "120"))
        )
        if self.limit < 0:
            self.limit = 0
        self.ready_limit = max(0, int(ready_requests_per_minute))
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._ready_calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._last_prune = 0.0

    def _prune(self, calls: dict[str, deque[float]], now: float) -> None:
        """Drop stale client buckets and cap the total bucket count."""
        if len(calls) <= 10_000 or now - self._last_prune < 60:
            return
        self._last_prune = now
        for key in [key for key, bucket in calls.items() if not bucket or now - bucket[-1] >= 60]:
            calls.pop(key, None)
        if len(calls) > 10_000:
            # Active peers can still exceed the cap during a short window;
            # evict the least-recently-used buckets so the map is bounded.
            oldest = sorted(calls, key=lambda key: calls[key][-1])[: len(calls) - 10_000]
            for key in oldest:
                calls.pop(key, None)

    async def _consume(self, calls: dict[str, deque[float]], client: str, now: float, limit: int):
        if limit <= 0:
            return None
        async with self._lock:
            bucket = calls[client]
            while bucket and now - bucket[0] >= 60:
                bucket.popleft()
            if len(bucket) >= limit:
                return JSONResponse(
                    {"detail": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, int(60 - (now - bucket[0]))))},
                )
            bucket.append(now)
            self._prune(calls, now)
        return None

    async def dispatch(self, request: Request, call_next):
        # /health is a constant response. /ready performs a database probe, so
        # it gets a separate bounded probe budget instead of sharing the
        # low-volume API budget and without becoming an unbounded DB amplifier.
        if request.url.path == "/health" or self.limit == 0:
            return await call_next(request)
        if request.url.path == "/ready":
            client = request.client.host if request.client else "unknown"
            limited = await self._consume(
                self._ready_calls, client, time.monotonic(), self.ready_limit
            )
            return limited if limited is not None else await call_next(request)
        # Use the transport peer, never an untrusted forwarded header.
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        async with self._lock:
            calls = self._calls[client]
            while calls and now - calls[0] >= 60:
                calls.popleft()
            if len(calls) >= self.limit:
                return JSONResponse(
                    {"detail": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, int(60 - (now - calls[0]))))},
                )
            calls.append(now)
            # Compact at most once per window, not on every request after the
            # cap is crossed: rebuilding a 10k-entry map under the lock on
            # every request serializes the limiter during exactly the flood it
            # is meant to shed.
            self._prune(calls, now)
        return await call_next(request)
