"""
Orchestrating policy layer (Fase 16 — anti-fragile hardening).

This module ties together the three hardening primitives:

- ``robots_policy`` — robots.txt check (deny → return error result).
- ``rate_limiter`` — per-domain token bucket (await a token).
- ``circuit_breaker`` — per-domain breaker (fast-fail when host is sick).
- ``with_retry`` — exponential backoff for transient failures.
- ``human_delay`` — Poisson inter-arrival jitter.

The intended call shape is::

    result = await safe_fetch(url, fetcher_callable=fetch_and_extract)

It is **not** mandatory — existing T2/T3/T4 callers keep their direct
imports — but it is the recommended entry point for new code paths
(orchestrator, batch worker, scripts). Individual scrapers (httpbs,
playwright_t4) also embed the same checks internally so callers that
prefer the lower-level API still get the safety net.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ._human_behavior import human_delay
from ._rate_limit import rate_limiter
from ._retry import circuit_breaker, with_retry
from ._robots import robots_policy
from .base import ScrapeResult

logger = logging.getLogger(__name__)


async def safe_fetch(
    url: str,
    *,
    fetcher_callable: Callable[..., Awaitable[ScrapeResult]],
    user_agent: str | None = None,
    ignore_robots: bool = False,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> ScrapeResult:
    """Run ``fetcher_callable(url, **kwargs)`` behind the full hardening stack.

    Pipeline (in order):

    1. **robots.txt** — if ``ignore_robots`` is False and the host disallows
       this UA, return a ScrapeResult with ``error="robots.txt disallow"``
       (no fetch happens, no exception raised).
    2. **Circuit breaker** — if the per-host breaker is open, fail fast with
       ``error="circuit breaker open"``.
    3. **Rate limiter** — await a token for this host (token bucket).
    4. **Human delay** — sleep a Poisson-distributed jitter on top of the
       rate limit (defence in depth against burst detection).
    5. **with_retry** — call ``fetcher_callable`` with exponential backoff
       on transient failures (HTTP 429/5xx, network errors).

    Parameters
    ----------
    url:
        Target URL. Passed as the first positional arg to ``fetcher_callable``.
    fetcher_callable:
        Async callable returning a ``ScrapeResult``. Typically
        ``fetch_and_extract`` (T2), ``crawl_and_extract`` (T3), or
        ``stealth_fetch`` (T4).
    user_agent:
        UA string for the robots.txt check. ``None`` → ``"*"`` (matches the
        default group).
    ignore_robots:
        Explicit opt-out — set True only for testing your own domain or
        when you have written permission. Logged at WARN level.
    max_attempts, base_delay:
        Passed to ``with_retry``.
    **kwargs:
        Forwarded to ``fetcher_callable``.

    Returns
    -------
    ``ScrapeResult``. On any failure (robots disallow, breaker open, retry
    exhaustion) the result carries ``error`` and ``ok == False`` — this
    function never raises.
    """
    if not url:
        return ScrapeResult(tier=0, url=url, error="Empty URL")

    ua_for_robots = user_agent or "*"

    # 1. robots.txt
    if ignore_robots:
        logger.warning("safe_fetch: ignoring robots.txt for %s (explicit opt-in)", url)
    else:
        allowed = await robots_policy.is_allowed(url, ua_for_robots)
        if not allowed:
            logger.info("safe_fetch: blocked by robots.txt: %s", url)
            return ScrapeResult(
                tier=0,
                url=url,
                error=f"robots.txt disallow for {url}",
                metadata={"policy_block": "robots"},
            )

    # 2. Circuit breaker
    if not circuit_breaker.allow(url):
        return ScrapeResult(
            tier=0,
            url=url,
            error=f"circuit breaker open for {url}",
            metadata={"policy_block": "circuit_breaker"},
        )

    # 3. Rate limit
    waited = await rate_limiter.acquire(url)

    # 4. Human delay (on top of the bucket — the bucket smooths long-term
    # rate, the Poisson jitter smooths short-term burst shape).
    import asyncio  # local import to keep top-level deps minimal
    await asyncio.sleep(human_delay(mean_seconds=1.5))

    # 5. Retry
    try:
        result = await with_retry(
            fetcher_callable,
            url,
            max_attempts=max_attempts,
            base_delay=base_delay,
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001 — surface any final-attempt failure
        circuit_breaker.record_failure(url)
        return ScrapeResult(
            tier=0,
            url=url,
            error=f"safe_fetch: exhausted retries: {exc!s}",
            metadata={
                "rate_limit_waited_seconds": waited,
                "policy_block": "retry_exhausted",
            },
        )

    # Track breaker outcome based on the ScrapeResult.ok flag — that way
    # both "raised" and "returned with error" failures count toward opening.
    if result.ok:
        circuit_breaker.record_success(url)
    else:
        circuit_breaker.record_failure(url)

    # Surface the rate-limit wait in metadata for observability.
    if isinstance(result.metadata, dict):
        result.metadata.setdefault("rate_limit_waited_seconds", waited)
    return result


__all__ = ["safe_fetch"]
