from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

from scrapers.base import ScrapeResult

from .base import SearchResponse, SearchResult

log = logging.getLogger("savantsdatabas.searchers.google_maps")

_TIMEOUT = 45.0


async def maps_domain_search(
    company_name: str,
    city: str = "",
    *,
    storage_state_key: str = "google_maps",
) -> SearchResponse:
    """Search Google Maps via Playwright (T4) e recupera il dominio aziendale.

    Usa stealth_fetch per navigare Google Maps e fare parsing della pagina.
    Unica sessione persistente per cookie/sessione tra ricerche successive.
    """
    query = f"{company_name} {city}".strip()
    maps_url = f"https://www.google.com/maps/search/{quote(query)}/"

    try:
        from scrapers.playwright_t4 import stealth_fetch

        result = await stealth_fetch(
            maps_url,
            storage_state_key=storage_state_key,
            timeout=_TIMEOUT,
            headless=True,
            ignore_robots=True,
            enforce_rate_limit=False,
            wait_for_selector="a[href*='maps/place'], div[role='feed'], div.section-result",
        )
    except ImportError:
        return SearchResponse(query=query, source="google_maps", error="T4 Playwright not available")
    except Exception as exc:
        return SearchResponse(query=query, source="google_maps", error=str(exc))

    if not result.ok or not result.content_text:
        return SearchResponse(
            query=query, source="google_maps",
            error=result.error or "empty response",
        )

    return _extract_from_maps_html(result, query)


_URL_IN_TEXT = re.compile(r"https?://(?:www\.)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.[a-z]{2,})", re.IGNORECASE)


def _extract_from_maps_html(result: ScrapeResult, query: str) -> SearchResponse:
    """Estrae domini dalla risposta HTML di Google Maps."""
    text = result.content_text or ""
    html = result.raw_html_excerpt or ""

    domains: set[str] = set()
    for m in _URL_IN_TEXT.finditer(text + "\n" + html):
        domain = m.group(1).lower().strip("/")
        if _is_company_domain(domain):
            domains.add(domain)

    found = [
        SearchResult(url=f"https://{d}", title=d, source="google_maps")
        for d in sorted(domains)
    ]

    return SearchResponse(query=query, source="google_maps", results=found)


def _is_company_domain(domain: str) -> bool:
    """Filtra domini non aziendali (Google, social, mappe)."""
    blocklist = {
        "google.com", "google.se", "maps.google.com",
        "facebook.com", "instagram.com", "twitter.com", "x.com",
        "linkedin.com", "youtube.com", "youtu.be",
        "wikipedia.org", "wikidata.org",
    }
    base = domain.lower().split(":")[0].split("/")[0]
    if not base:
        return False
    if any(base == b or base.endswith("." + b) for b in blocklist):
        return False
    if "." not in base:
        return False
    return True
