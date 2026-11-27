"""Abuse and quota guards for a public demo that uses one shared free-tier LLM key.

Design: the provider's own limits are the real budget. Local counters reset when a free host restarts,
so they are only a second line; the first line is the quota the provider reports on every response.
Nothing here is persisted and no question or PDF text is stored.
"""
import os
import threading
import time
from collections import defaultdict, deque


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Budget:
    """Decides whether one more AI answer may be attempted."""

    def __init__(self):
        self.lock = threading.Lock()
        self.day = time.strftime("%Y-%m-%d", time.gmtime())
        self.calls_today = 0                     # model calls made by this process today (UTC)
        self.remaining_requests = None           # provider-reported requests left today, if seen
        self.remaining_tokens = None             # provider-reported tokens left this minute, if seen
        self.blocked_until = 0.0                 # set after a provider 429

    @property
    def daily_cap(self):
        return _int("PAGECITE_DAILY_AI_CALLS", 300)

    @property
    def reserve_requests(self):
        return _int("PAGECITE_RESERVE_REQUESTS", 150)   # keep this many of the provider's daily requests for other uses

    def _roll(self):
        d = time.strftime("%Y-%m-%d", time.gmtime())
        if d != self.day:
            self.day, self.calls_today, self.remaining_requests = d, 0, None

    def allow(self):
        """Return (ok, reason)."""
        with self.lock:
            self._roll()
            if time.time() < self.blocked_until:
                return False, "the shared AI quota is busy right now"
            if self.calls_today >= self.daily_cap:
                return False, "today's AI limit for this demo is used up"
            if self.remaining_requests is not None and self.remaining_requests <= self.reserve_requests:
                return False, "the shared AI quota for today is nearly used up"
            return True, ""

    def record_call(self, headers=None):
        with self.lock:
            self._roll()
            self.calls_today += 1
            if headers is not None:
                for attr, key in (("remaining_requests", "x-ratelimit-remaining-requests"),
                                  ("remaining_tokens", "x-ratelimit-remaining-tokens")):
                    v = headers.get(key)
                    try:
                        setattr(self, attr, int(float(v)))
                    except (TypeError, ValueError):
                        pass

    def record_429(self, retry_after=60):
        with self.lock:
            self.blocked_until = time.time() + min(max(float(retry_after), 5), 900)


class RateLimiter:
    """Sliding-window limiter keyed by a string (client IP, or document id)."""

    def __init__(self, limit, window):
        self.limit, self.window, self.hits = limit, window, defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key, now=None):
        now = time.time() if now is None else now
        with self.lock:
            q = self.hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self.hits) > 5000:             # bound memory
                for k in [k for k, v in self.hits.items() if not v or v[-1] <= now - self.window][:2500]:
                    self.hits.pop(k, None)
            return True


def client_ip(xff, peer, trusted_hops=None):
    """Client IP behind a proxy chain.

    X-Forwarded-For is client-controlled on its left side: every proxy appends the address it saw,
    so entries the client wrote sit to the LEFT of those our own proxies added. Count `trusted_hops`
    entries from the RIGHT (the proxies in front of us) and take the one before them. With 0 hops
    configured (no proxy) the header is ignored entirely.
    """
    hops = _int("PAGECITE_TRUSTED_HOPS", 0) if trusted_hops is None else trusted_hops
    if hops <= 0 or not xff:
        return peer
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    return parts[-hops] if len(parts) >= hops else peer


BUDGET = Budget()
PER_IP = RateLimiter(_int("PAGECITE_AI_PER_IP_PER_HOUR", 20), 3600)
PER_DOC = RateLimiter(_int("PAGECITE_AI_PER_DOC_PER_HOUR", 15), 3600)   # not spoofable: keyed by server-issued doc id
