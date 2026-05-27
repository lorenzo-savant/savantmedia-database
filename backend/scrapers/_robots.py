"""
robots.txt enforcement layer (Fase 16 — anti-fragile hardening).

Per `docs/ARCHITECTURE.md` §13: anti-fragile, not anti-bot. We **respect
robots.txt by default**, with an explicit override only for legitimate
internal use (e.g. testing our own domain). This module wraps the stdlib
``urllib.robotparser`` with an in-process TTL cache so repeated lookups
within a single orchestrator run don't hammer each host.

Failure mode
------------
robots.txt fetch failures (network error, 5xx, timeout) **fail open** —
we treat the URL as allowed, log a warning once per host, and continue.
Rationale: a transient DNS hiccup must not stall the pipeline, and the
downstream rate limiter + retry layers provide the real safety net.

Cache
-----
Simple ``{host: (timestamp, RobotFileParser)}`` dict with a 1h TTL. No
external dependency, no LRU bookkeeping — robots.txt churn at scale is
negligible and we'd rather take a slightly stale parse than re-fetch on
every URL.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Final
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

# Cache TTL — 1 hour is generous, robots.txt rarely changes intra-day.
_CACHE_TTL_SECONDS: Final[float] = 3600.0
_FETCH_TIMEOUT_SECONDS: Final[float] = 10.0


class RobotsPolicy:
    """In-process robots.txt cache + ``is_allowed`` / ``crawl_delay`` API.

    Thread/asyncio-safe for the only operations we perform: dict writes
    happen inside the same coroutine after an ``await`` so two coroutines
    racing on the same host will just do redundant fetches and overwrite
    each other's parser — both correct, just wasteful in the worst case.
    """

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl: float = float(ttl_seconds)
        self._cache: dict[str, tuple[float, RobotFileParser | None]] = {}
        # Track which hosts we've already warned about so the log stays
        # readable — one warning per host per process lifetime.
        self._warned_hosts: set[str] = set()
        self._fetch_locks: dict[str, asyncio.Lock] = {}

    # ─────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _hostkey(url: str) -> tuple[str, str] | None:
        """Return ``(scheme, host)`` for ``url`` or ``None`` if malformed."""
        try:
            p = urlparse(url)
        except (ValueError, AttributeError):
            return None
        if not p.scheme or not p.hostname:
            return None
        return (p.scheme.lower(), p.hostname.lower())

    def _warn_once(self, host: str, reason: str) -> None:
        if host in self._warned_hosts:
            return
        self._warned_hosts.add(host)
        logger.warning(
            "robots.txt fetch failed for %s (%s) — failing open (allowing)",
            host,
            reason,
        )

    async def _fetch_parser(
        self, scheme: str, host: str
    ) -> RobotFileParser | None:
        """Fetch + parse robots.txt for ``host``. Returns parser or None."""
        url = f"{scheme}://{host}/robots.txt"
        try:
            async with httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            self._warn_once(host, str(exc))
            return None
        except Exception as exc:  # noqa: BLE001 — never fail caller
            self._warn_once(host, f"unexpected: {exc!s}")
            return None

        # 4xx (incl. 404) → treat as "no robots.txt, everything allowed".
        # 5xx → fail open with a warning (server problem, not our policy).
        if resp.status_code >= 500:
            self._warn_once(host, f"HTTP {resp.status_code}")
            return None

        parser = RobotFileParser()
        parser.set_url(url)
        if resp.status_code == 404:
            # Empty rules = allow all
            parser.parse([])
            return parser

        try:
            parser.parse(resp.text.splitlines())
        except Exception as exc:  # noqa: BLE001
            self._warn_once(host, f"parse error: {exc!s}")
            return None
        return parser

    async def _get_parser(
        self, scheme: str, host: str
    ) -> RobotFileParser | None:
        """Cache-aware accessor — returns parser or None (fail-open marker)."""
        cache_key = f"{scheme}://{host}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None:
            ts, parser = cached
            if (now - ts) < self._ttl:
                return parser

        # Per-host lock so concurrent callers fetch once.
        lock = self._fetch_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            # Re-check after waiting on the lock — another coroutine may
            # have populated the cache while we were queued.
            cached = self._cache.get(cache_key)
            if cached is not None:
                ts, parser = cached
                if (time.monotonic() - ts) < self._ttl:
                    return parser
            parser = await self._fetch_parser(scheme, host)
            self._cache[cache_key] = (time.monotonic(), parser)
            return parser

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    async def is_allowed(self, url: str, user_agent: str = "*") -> bool:
        """Return True if ``url`` is allowed by robots.txt for ``user_agent``.

        Failure modes (DNS, 5xx, parse error, malformed URL) → returns True.
        That's intentional: we'd rather be lenient on infrastructure
        flakiness than block legitimate work. The rate limiter is the
        backstop.
        """
        key = self._hostkey(url)
        if key is None:
            return True  # malformed → caller will fail at fetch time anyway
        scheme, host = key
        parser = await self._get_parser(scheme, host)
        if parser is None:
            return True  # fail-open marker
        try:
            return parser.can_fetch(user_agent, url)
        except Exception:  # noqa: BLE001 — never fail caller on parser bug
            return True

    async def crawl_delay(
        self, url: str, user_agent: str = "*"
    ) -> float | None:
        """Return the ``Crawl-delay`` directive (seconds) if declared, else None."""
        key = self._hostkey(url)
        if key is None:
            return None
        scheme, host = key
        parser = await self._get_parser(scheme, host)
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(user_agent)
        except Exception:  # noqa: BLE001
            return None
        if delay is None:
            return None
        try:
            return float(delay)
        except (TypeError, ValueError):
            return None


# Module-level singleton — every scraper tier imports this.
robots_policy: RobotsPolicy = RobotsPolicy()


__all__ = ["RobotsPolicy", "robots_policy"]
