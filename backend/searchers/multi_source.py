from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable
from typing import Any
from urllib.parse import urlparse

from scrapers.searxng import SearXNGClient

from .base import SearchResponse, SearchResult
from .brave import brave_search

log = logging.getLogger("savantsdatabas.searchers.multi_source")

_BLACKLIST_DOMAINS: frozenset[str] = frozenset({
    "allabolag.se", "ratsit.se", "bolagsfakta.se", "merinfo.se",
    "hitta.se", "eniro.se", "linkedin.com", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "youtube.com",
    "google.com", "google.se", "bing.com", "duckduckgo.com",
    "wikipedia.org", "yelp.se", "yelp.com", "indeed.com", "indeed.se",
    "glassdoor.com", "glassdoor.se", "trustpilot.com",
    "bolagsverket.se", "scb.se", "skatteverket.se",
    "wikidata.org", "europages.com", "kompass.com",
})


def _looks_like_company_domain(domain: str, company_name: str) -> bool:
    domain = domain.lower().lstrip(".")
    if domain in _BLACKLIST_DOMAINS:
        return False
    if any(domain.endswith("." + b) or domain == b for b in _BLACKLIST_DOMAINS):
        return False
    tokens = re.findall(r"[a-z0-9]+", company_name.lower())
    tokens = [t for t in tokens if len(t) >= 3 and t not in {"ab", "aktiebolag", "hb", "kb", "ek"}]
    if not tokens:
        return False
    base = domain.split(":")[0].split("/")[0]
    for tok in tokens[:3]:
        if tok in base:
            return True
    return False


def _build_queries(company_name: str, city: str) -> list[str]:
    q = []
    name = company_name.strip()
    c = city.strip()
    if c:
        q.append(f'"{name}" {c} site:.se')
    q.append(f'"{name}" hemsida')
    q.append(f'"{name}" website kontakt')
    if c:
        q.append(f"{name} {c}")
    return q


def _extract_domain(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower().split("@")[-1]
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


async def search_all_sources(
    company_name: str,
    city: str = "",
    *,
    t1_client: SearXNGClient | None = None,
    skip_brave: bool = False,
    skip_maps: bool = True,
) -> list[SearchResult]:
    """Cerca il dominio aziendale su TUTTI i motori in parallelo.

    Lancia contemporaneamente:
    - SearXNG/DDG (T1 esistente)
    - Brave Search
    - Google Maps (opzionale, usa T4 Playwright, piu lento)
    """
    queries = _build_queries(company_name, city)
    tasks: list[Awaitable[Any]] = []

    if t1_client is None:
        t1_client = SearXNGClient()
    for q in queries:
        tasks.append(t1_client.search(q, limit=8))

    if not skip_brave:
        for q in queries:
            tasks.append(brave_search(q, limit=8))

    if not skip_maps:
        try:
            from .google_maps import maps_domain_search
            tasks.append(maps_domain_search(company_name, city))
        except ImportError:
            pass

    raw = await asyncio.gather(*tasks, return_exceptions=True)

    seen: dict[str, SearchResult] = {}

    for resp in raw:
        if isinstance(resp, Exception):
            log.debug("search source error: %s", resp)
            continue
        if isinstance(resp, SearchResponse):
            for r in resp.results:
                _add_if_good(r, company_name, seen)
        elif isinstance(resp, list):
            for item in resp:
                _process_scrape_result(item, company_name, seen)

    return list(seen.values())


def _process_scrape_result(
    item: Any,
    company_name: str,
    seen: dict[str, SearchResult],
) -> None:
    if not hasattr(item, "url") or not item.url:
        return
    ok = getattr(item, "ok", False)
    if not ok:
        return
    domain = _extract_domain(item.url)
    if not domain:
        return
    if not _looks_like_company_domain(domain, company_name):
        return
    if domain in seen:
        return
    source = "searxng"
    if hasattr(item, "metadata") and item.metadata:
        source = item.metadata.get("engine_backend", "searxng")
    seen[domain] = SearchResult(
        url=f"https://{domain}",
        title=getattr(item, "title", None) or domain,
        source=source,
    )


def _add_if_good(
    r: SearchResult,
    company_name: str,
    seen: dict[str, SearchResult],
) -> None:
    if not r.url:
        return
    domain = _extract_domain(r.url)
    if not domain:
        return
    if not _looks_like_company_domain(domain, company_name):
        return
    if domain in seen:
        return
    seen[domain] = SearchResult(
        url=f"https://{domain}",
        title=r.title or domain,
        source=r.source,
    )
