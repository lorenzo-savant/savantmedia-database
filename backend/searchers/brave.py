from __future__ import annotations

import asyncio
import logging
import random
import re

import httpx
from bs4 import BeautifulSoup

from .base import SearchResponse, SearchResult

log = logging.getLogger("savantsdatabas.searchers.brave")

_BRAVE_URL = "https://search.brave.com/search"
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]
_TIMEOUT = 20.0


async def brave_search(query: str, limit: int = 10) -> SearchResponse:
    """Search via Brave Search, con retry su rate-limit e rotazione UA."""
    ua = random.choice(_USER_AGENTS)
    params = {"q": query, "source": "web"}
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    }

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(_BRAVE_URL, params=params, headers=headers)
                if resp.status_code == 429:
                    wait = 5.0 * (attempt + 1)
                    log.debug("Brave 429 on %r, retrying in %.0fs", query, wait)
                    await asyncio.sleep(wait)
                    ua = random.choice(_USER_AGENTS)
                    headers["User-Agent"] = ua
                    continue
                resp.raise_for_status()
                return _parse_brave_html(resp.text, query, limit)
        except httpx.HTTPError as exc:
            if attempt < 2:
                await asyncio.sleep(3.0 * (attempt + 1))
                continue
            return SearchResponse(query=query, source="brave", error=str(exc))

    return SearchResponse(query=query, source="brave", error="exhausted retries")

    return _parse_brave_html(resp.text, query, limit)


def _parse_brave_html(html: str, query: str, limit: int) -> SearchResponse:
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []
    seen_urls: set[str] = set()

    for link in soup.select("a[href]"):
        href = link.get("href", "")
        if not href.startswith("http"):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)
        title_el = link.find(class_=re.compile(r"title|heading|snippet-title"))
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
        if not title or len(title) < 3:
            continue
        results.append(
            SearchResult(url=href, title=title[:200], source="brave")
        )
        if len(results) >= limit:
            break

    if not results:
        for a in soup.find_all("a", href=True):
            h = a["href"]
            if h.startswith("http") and h not in seen_urls:
                seen_urls.add(h)
                t = a.get_text(strip=True)
                if t and len(t) >= 3:
                    results.append(
                        SearchResult(url=h, title=t[:200], source="brave")
                    )
                    if len(results) >= limit:
                        break

    return SearchResponse(query=query, source="brave", results=results[:limit])
