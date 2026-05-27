"""
Tier 2 — httpx + BeautifulSoup + trafilatura.

This is the workhorse for static / SSR-rendered pages: it's polite (random
User-Agent, Poisson delays), cheap, and gives the orchestrator both clean
markdown (for LLMs) and plain text (for regex).

Use T2 when the decision tree (`docs/ARCHITECTURE.md` §8) lands on
"static/SSR site". For JS-heavy pages escalate to T3 (crawl4ai) and for
anti-bot escalate to T4 (playwright-stealth).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import trafilatura
from bs4 import BeautifulSoup

from ._human_behavior import (
    human_delay,
    random_user_agent,
    realistic_accept_language,
)
from ._rate_limit import rate_limiter
from ._retry import with_retry
from ._robots import robots_policy
from .base import ScrapeResult

logger = logging.getLogger(__name__)

# URL substrings that strongly suggest "don't scrape me, kthx".
# Lightweight heuristic — not a robots.txt parser. The real robots.txt check
# can be added later as a sub-tier feature; for now we just bail on obvious
# anti-scrape paths.
_NOSCRAPE_HINTS: tuple[str, ...] = (
    "/login",
    "/signin",
    "/admin",
    "/wp-admin",
    "/cart",
    "/checkout",
    "/captcha",
    "/cdn-cgi/challenge-platform",
)


def _looks_like_noscrape(url: str) -> bool:
    """Return True if the URL has obvious 'do not scrape' patterns."""
    lower = url.lower()
    return any(hint in lower for hint in _NOSCRAPE_HINTS)


async def fetch_and_extract(
    url: str,
    timeout: float = 30.0,
    delay: bool = True,
    *,
    ignore_robots: bool = False,
    enforce_rate_limit: bool = True,
    max_attempts: int = 3,
) -> ScrapeResult:
    """Fetch `url` and extract clean text + markdown via trafilatura.

    Parameters
    ----------
    url:
        Absolute http(s) URL to fetch.
    timeout:
        Per-request timeout (seconds). 30s is generous on purpose — Swedish
        company sites are slow.
    delay:
        If True (default), sleep a Poisson-distributed delay (mean 2.5s)
        before the actual fetch. Set to ``False`` only for unit tests or
        when the caller is already orchestrating its own rate-limiting.
    ignore_robots:
        Opt-in only. Default False = respect robots.txt and return an error
        ScrapeResult if the host disallows the URL.
    enforce_rate_limit:
        Default True. Awaits a token from the per-domain bucket before fetch.
        Set False only when the caller (e.g. ``safe_fetch``) is already
        handling rate limiting.
    max_attempts:
        Passed to ``with_retry``. Default 3 retries on 429/5xx + network errors.

    Returns
    -------
    `ScrapeResult` with ``tier=2``. On any failure (network, HTTP non-2xx,
    parsing) the result has ``error`` populated and ``ok == False``.
    """
    if not url:
        return ScrapeResult(tier=2, url=url, error="Empty URL")

    if _looks_like_noscrape(url):
        return ScrapeResult(
            tier=2,
            url=url,
            error=f"URL matches noscrape heuristic — refusing to fetch: {url}",
        )

    # robots.txt enforcement (fail-open on fetch errors).
    if not ignore_robots:
        if not await robots_policy.is_allowed(url, "*"):
            return ScrapeResult(
                tier=2,
                url=url,
                error=f"robots.txt disallow for {url}",
                metadata={"policy_block": "robots"},
            )
    else:
        logger.warning("fetch_and_extract: ignoring robots.txt for %s", url)

    # Per-domain rate limiting.
    rate_limit_waited: float = 0.0
    if enforce_rate_limit:
        rate_limit_waited = await rate_limiter.acquire(url)

    if delay:
        await asyncio.sleep(human_delay(mean_seconds=2.5))

    headers = {
        "User-Agent": random_user_agent(),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,*/*;q=0.8"
        ),
        "Accept-Language": realistic_accept_language("SE"),
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    metadata: dict[str, Any] = {
        "rate_limit_waited_seconds": rate_limit_waited,
    }

    async def _do_fetch() -> tuple[str, dict[str, Any]]:
        """Inner fetch — extracted so ``with_retry`` can drive it."""
        async with httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            local_meta: dict[str, Any] = {
                "status_code": resp.status_code,
                "content_type": resp.headers.get("content-type"),
                "final_url": str(resp.url),
            }
            resp.raise_for_status()
            return resp.text, local_meta

    try:
        html, fetch_meta = await with_retry(
            _do_fetch,
            max_attempts=max_attempts,
            base_delay=2.0,
        )
        metadata.update(fetch_meta)
    except httpx.HTTPStatusError as exc:
        try:
            metadata.setdefault("status_code", exc.response.status_code)
        except AttributeError:
            pass
        return ScrapeResult(
            tier=2,
            url=url,
            metadata=metadata,
            error=f"HTTP {exc.response.status_code} for {url}",
        )
    except httpx.HTTPError as exc:
        return ScrapeResult(
            tier=2,
            url=url,
            metadata=metadata,
            error=f"Network error for {url}: {exc!s}",
        )

    # Title via BeautifulSoup — cheaper and more reliable than trafilatura
    # metadata for this single field.
    title: str | None = None
    try:
        soup = BeautifulSoup(html, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
    except Exception:  # noqa: BLE001 — never fail extraction on a bad title
        title = None

    # Trafilatura extractions — text + markdown
    content_text: str | None = None
    content_markdown: str | None = None
    try:
        content_text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception as exc:  # noqa: BLE001
        metadata["trafilatura_text_error"] = str(exc)

    try:
        content_markdown = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception as exc:  # noqa: BLE001
        metadata["trafilatura_md_error"] = str(exc)

    return ScrapeResult(
        tier=2,
        url=url,
        title=title,
        content_text=content_text,
        content_markdown=content_markdown,
        raw_html_excerpt=html[:500] if html else None,
        metadata=metadata,
    )
