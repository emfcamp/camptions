"""Per-IP rate limiting for the public HTTP surface.

Two limits run side by side:

* `RateLimitMiddleware` — sliding-window counter on HTTP requests, scoped to
  the public API path prefixes. Adds `X-RateLimit-*` headers and returns
  HTTP 429 with `Retry-After` once the window is full.
* `WSConnectionLimiter` — caps the number of simultaneous WebSocket
  connections per client IP. Used inline in the WS handler because Starlette
  middleware doesn't trivially see WS opens.

No external dependencies — small project, single process. If we ever go
multi-process behind a load balancer this should move to Redis.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Iterable

from fastapi import Response, WebSocket
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp


def _client_ip(request: Request | WebSocket) -> str:
    """Best-effort client IP. Honour `X-Forwarded-For` (first hop) if set."""
    fwd = request.headers.get("x-forwarded-for") if request.headers else None
    if fwd:
        return fwd.split(",")[0].strip()
    client = request.client
    return client.host if client else "?"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window per-IP limiter scoped to `path_prefixes`."""

    def __init__(
        self,
        app: ASGIApp,
        limit: int,
        window_seconds: float = 60.0,
        path_prefixes: Iterable[str] = (),
    ) -> None:
        super().__init__(app)
        self.limit = limit
        self.window = window_seconds
        self.path_prefixes = tuple(path_prefixes)
        self._history: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next):
        if self.limit <= 0 or not self._matches(request.url.path):
            return await call_next(request)

        ip = _client_ip(request)
        now = time.monotonic()
        async with self._lock:
            q = self._history[ip]
            cutoff = now - self.window
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.limit:
                retry_after = max(1, int(q[0] + self.window - now))
                return Response(
                    content=(
                        f"Rate limit exceeded: {self.limit} requests per "
                        f"{int(self.window)}s. Retry in {retry_after}s.\n"
                    ),
                    status_code=429,
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(self.limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(time.time()) + retry_after),
                    },
                    media_type="text/plain",
                )
            q.append(now)
            remaining = self.limit - len(q)

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Window"] = f"{int(self.window)}s"
        return response

    def _matches(self, path: str) -> bool:
        if not self.path_prefixes:
            return True
        return any(path.startswith(p) for p in self.path_prefixes)


class WSConnectionLimiter:
    """Counts simultaneous WS connections per IP.

    Usage:
        if not await ws_limiter.acquire(websocket):
            await websocket.close(code=1013)  # Try Again Later
            return
        try:
            ...
        finally:
            ws_limiter.release(websocket)
    """

    def __init__(self, max_per_ip: int) -> None:
        self.max_per_ip = max_per_ip
        self._counts: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def acquire(self, websocket: WebSocket) -> bool:
        if self.max_per_ip <= 0:
            return True
        ip = _client_ip(websocket)
        async with self._lock:
            if self._counts[ip] >= self.max_per_ip:
                return False
            self._counts[ip] += 1
        return True

    async def release(self, websocket: WebSocket) -> None:
        if self.max_per_ip <= 0:
            return
        ip = _client_ip(websocket)
        async with self._lock:
            self._counts[ip] = max(0, self._counts[ip] - 1)
            if self._counts[ip] == 0:
                del self._counts[ip]
