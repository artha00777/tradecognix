"""Tiny in-memory sliding-window rate limiter (per process, no dependencies).

Good enough for a single-worker deployment (the trading scheduler already requires exactly one
worker). For several workers put a shared limiter (Redis, or the reverse proxy) in front.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import get_settings


def parse_rule(rule: str) -> tuple[int, float]:
    """"10/300" -> (10 requests, 300 seconds)."""
    count, _, window = rule.partition("/")
    return int(count), float(window or 60)


class RateLimiter:
    def __init__(self, max_keys: int = 50_000):
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def hit(self, key: str, limit: int, window: float, now: float | None = None) -> float | None:
        """Record a hit. Returns None if allowed, else seconds until the next slot frees up."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > self._max_keys:  # crude memory bound: drop everything stale
                for k in [k for k, q in self._hits.items() if not q or q[-1] < now - 3600]:
                    del self._hits[k]
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return max(0.0, q[0] + window - now)
            q.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce(key: str, rule: str) -> None:
    """Raise HTTP 429 when `key` exceeded `rule` ("count/seconds")."""
    if not get_settings().rate_limit_enabled:
        return
    limit, window = parse_rule(rule)
    retry = limiter.hit(key, limit, window)
    if retry is not None:
        raise HTTPException(429, "Too many requests, please slow down", headers={"Retry-After": str(int(retry) + 1)})
