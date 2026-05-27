"""
Per-domain token-bucket rate limiter (Fase 16 — anti-fragile hardening).

Per `docs/ARCHITECTURE.md` §13: stay below detection threshold rather than
bypass it. This module implements a token bucket per hostname:

- Each bucket refills at ``rpm/60`` tokens per second.
- Each bucket has a ``burst`` capacity (max tokens stored).
- ``acquire()`` consumes one token, blocking if none available.

Defaults are tuned conservatively — 30 rpm, burst 5 — and overridden per
host where the vault has hard-won evidence (allabolag = 6 rpm, LinkedIn
= 4 rpm). Overrides via env vars: ``SCRAPE_RPM_DEFAULT``,
``SCRAPE_BURST_DEFAULT``.

The bucket is in-process only — across multiple worker processes you'd
need a shared store (Redis), which the design doc explicitly defers until
proven necessary.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse


def _env_float(name: str, default: float) -> float:
    """Read ``name`` from env as float, falling back to ``default``."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Conservative defaults — tunable via env without code changes.
_DEFAULT_RPM: float = _env_float("SCRAPE_RPM_DEFAULT", 30.0)
_DEFAULT_BURST: int = _env_int("SCRAPE_BURST_DEFAULT", 5)


# Per-domain overrides codified from vault lessons (`docs/ARCHITECTURE.md` §13).
# Keys are matched as either exact hostnames or suffix patterns (``*.host``).
_DOMAIN_OVERRIDES: dict[str, tuple[float, int]] = {
    # allabolag /bransch-sök is SSR-friendly but the host blocks fast bursts.
    # Vault: 6 rpm with burst 2 keeps us under their threshold.
    "allabolag.se": (6.0, 2),
    # LinkedIn public pages — extremely sensitive, single concurrent request.
    "linkedin.com": (4.0, 1),
    "www.linkedin.com": (4.0, 1),
    # Bolagsverket is open data with explicit CC-BY-4.0 terms — generous.
    "bolagsverket.se": (60.0, 10),
}


# Suffix overrides (``*.host`` style): match any host that ends with ``.host``.
_SUFFIX_OVERRIDES: list[tuple[str, tuple[float, int]]] = [
    (".bolagsverket.se", (60.0, 10)),
]


@dataclass
class _Bucket:
    """One token bucket. Tokens refill continuously based on ``rate_per_sec``."""

    rate_per_sec: float
    capacity: float
    tokens: float = field(init=False)
    last_refill: float = field(init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)
        self.last_refill = time.monotonic()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.last_refill)
        if elapsed <= 0.0:
            return
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)
        self.last_refill = now


class DomainRateLimiter:
    """Per-hostname token bucket. ``acquire(url)`` blocks until a token is free."""

    def __init__(
        self,
        default_rpm: float = _DEFAULT_RPM,
        default_burst: int = _DEFAULT_BURST,
    ) -> None:
        self.default_rpm: float = float(default_rpm)
        self.default_burst: int = int(default_burst)
        self._buckets: dict[str, _Bucket] = {}
        # Per-host overrides — start from the curated defaults, allow runtime
        # additions via ``set_limit``.
        self._overrides: dict[str, tuple[float, int]] = dict(_DOMAIN_OVERRIDES)
        self._suffix_overrides: list[tuple[str, tuple[float, int]]] = list(
            _SUFFIX_OVERRIDES
        )

    # ─────────────────────────────────────────────────────────────────
    # Configuration
    # ─────────────────────────────────────────────────────────────────

    def set_limit(self, host: str, rpm: float, burst: int) -> None:
        """Set or override the limit for ``host`` (exact match, lowercased)."""
        host = host.lower().lstrip(".")
        self._overrides[host] = (float(rpm), int(burst))
        # Drop any existing bucket so the new config takes effect next acquire.
        self._buckets.pop(host, None)

    def _resolve_limit(self, host: str) -> tuple[float, int]:
        """Find the most-specific limit configured for ``host``."""
        host = host.lower()
        if host in self._overrides:
            return self._overrides[host]
        stripped = host.removeprefix("www.")
        if stripped in self._overrides:
            return self._overrides[stripped]
        for suffix, limits in self._suffix_overrides:
            if host.endswith(suffix):
                return limits
        return (self.default_rpm, self.default_burst)

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def _get_bucket(self, host: str) -> _Bucket:
        bucket = self._buckets.get(host)
        if bucket is not None:
            return bucket
        rpm, burst = self._resolve_limit(host)
        rate = rpm / 60.0
        bucket = _Bucket(rate_per_sec=rate, capacity=float(max(1, burst)))
        self._buckets[host] = bucket
        return bucket

    async def acquire(self, url: str) -> float:
        """Acquire one token for the host of ``url``. Returns waited seconds.

        - Returns the wait time applied (0.0 if there was an immediate token).
        - On malformed URL: returns 0.0 (no host → no bucket → no limit).
        """
        try:
            host = (urlparse(url).hostname or "").lower()
        except (ValueError, AttributeError):
            return 0.0
        if not host:
            return 0.0

        bucket = self._get_bucket(host)
        async with bucket.lock:
            now = time.monotonic()
            bucket._refill(now)
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return 0.0
            # Compute exact wait until next token available.
            deficit = 1.0 - bucket.tokens
            wait_seconds = deficit / bucket.rate_per_sec if bucket.rate_per_sec > 0 else 1.0

        # Sleep outside the lock so other coroutines on other hosts run.
        await asyncio.sleep(wait_seconds)
        async with bucket.lock:
            bucket._refill(time.monotonic())
            # We waited explicitly for one token; consume it.
            bucket.tokens = max(0.0, bucket.tokens - 1.0)
        return wait_seconds


# Module-level singleton.
rate_limiter: DomainRateLimiter = DomainRateLimiter()


__all__ = ["DomainRateLimiter", "rate_limiter"]
