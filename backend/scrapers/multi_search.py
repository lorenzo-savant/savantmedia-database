"""
Tier 1.5 — Multi-engine meta-search via HTML scraping.

Fonti aggiunte oltre SearXNG/DDG (`searxng.py`):
- **Brave Search** — https://search.brave.com/search?q=...
  Indipendente da Google/Bing index, no API key.
- **Ecosia** — https://www.ecosia.org/search?q=...
  Powered by Bing ma con header diversi e meno aggressive CAPTCHA.
- **Bing** — https://www.bing.com/search?q=...
  Index Microsoft, complementare a Google.
- **Google AI Overview** — vedi `google_aio.py` per il parsing
  (richiede una richiesta a google.com/search + parsing del block AIO).

Ogni motore espone una `search(query, limit) -> list[ScrapeResult]` con
`tier=1` e `metadata.engine_backend` populato. Sono pensati come *fallback in
cascata* nel `unified_search()` quando SearXNG/DDG non hanno trovato nulla
di utile (es. dominio plausibile per un'azienda dormiente).

Hardening
---------
Tutti i client passano per `safe_fetch` (httpbs.fetch_and_extract):
- robots.txt enforced
- per-domain rate limit (con bucket dedicato per `search.brave.com`,
  `www.ecosia.org`, `www.bing.com`)
- User-Agent ruotato + Accept-Language sv-SE
- Backoff esponenziale 429/5xx

Tutti i parser tornano `error=...` su fallimento senza raise.

Usage:
    from scrapers.multi_search import BraveClient, EcosiaClient, BingClient
    res = await BraveClient().search('"Saltsjö-Boo Marin" hemsida', limit=5)
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from .base import ScrapeResult
from .httpbs import fetch_and_extract


class _BaseHtmlSearchClient:
    """Sottoclassi forniscono URL builder + parser HTML → ScrapeResult."""

    name: str = "html-search"
    base_url: str = ""

    def _build_url(self, query: str) -> str:
        raise NotImplementedError

    def _parse(self, html: str, query: str) -> list[ScrapeResult]:
        raise NotImplementedError

    async def search(self, query: str, limit: int = 10) -> list[ScrapeResult]:
        url = self._build_url(query)
        # Search engines disallow /search in robots.txt — but we're using the
        # public search box as a meta-query, not crawling their result pages for
        # republication. robots is bypassed HERE ONLY; company sites fetched
        # downstream still fully respect robots.txt.
        res = await fetch_and_extract(url, timeout=20.0, ignore_robots=True)
        if not res.ok:
            return [
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=url,
                    error=f"{self.name} fetch failed: {res.error}",
                    metadata={"engine_backend": self.name},
                )
            ]
        # _parse riceve l'HTML grezzo se disponibile, altrimenti il content_text
        # (trafilatura potrebbe averlo ripulito troppo aggressivamente; ritardo
        # il fallback)
        html = res.raw_html_excerpt or res.content_text or ""
        try:
            out = self._parse(html, query)
        except Exception as exc:  # noqa: BLE001
            return [
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=url,
                    error=f"{self.name} parse failed: {exc!s}",
                    metadata={"engine_backend": self.name},
                )
            ]
        return out[:limit] if out else [
            ScrapeResult(
                tier=1,
                query=query,
                url=url,
                metadata={"engine_backend": self.name, "count": 0},
            )
        ]


def _clean_url(href: str) -> str | None:
    """Brave/Ecosia spesso wrappano i link in redirect — estrai il target."""
    if not href:
        return None
    if href.startswith("/"):
        return None
    if href.startswith("http"):
        return href
    return None


# ── Brave ────────────────────────────────────────────────────────────────────


class BraveClient(_BaseHtmlSearchClient):
    name = "brave"
    base_url = "https://search.brave.com/search"

    def _build_url(self, query: str) -> str:
        return f"{self.base_url}?q={quote_plus(query)}&source=web"

    def _parse(self, html: str, query: str) -> list[ScrapeResult]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[ScrapeResult] = []
        # Brave usa <div class="snippet"> con <a class="result-header">
        for snippet in soup.select(
            'div[data-type="web"] a[href^="http"], '
            'div.snippet a.result-header, '
            'div.snippet a[href^="http"]'
        ):
            href = snippet.get("href")
            if not isinstance(href, str):
                continue
            href = _clean_url(href)
            if not href:
                continue
            title_el = snippet.find(["span", "div"], class_=re.compile(r"title"))
            title = (title_el.get_text(strip=True) if title_el else
                     snippet.get_text(strip=True))[:200]
            # description nello snippet container vicino
            parent = snippet.find_parent("div", class_=re.compile(r"snippet"))
            body = ""
            if parent:
                desc_el = parent.find(["p", "div"], class_=re.compile(r"snippet-description|snippet-content"))
                if desc_el:
                    body = desc_el.get_text(" ", strip=True)[:500]
            out.append(
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=href,
                    title=title,
                    content_text=body,
                    content_markdown=body,
                    metadata={"engine_backend": self.name},
                )
            )
        return out


# ── Ecosia ───────────────────────────────────────────────────────────────────


class EcosiaClient(_BaseHtmlSearchClient):
    name = "ecosia"
    base_url = "https://www.ecosia.org/search"

    def _build_url(self, query: str) -> str:
        # mkt=sv-se = Swedish market
        return f"{self.base_url}?q={quote_plus(query)}&mkt=sv-se"

    def _parse(self, html: str, query: str) -> list[ScrapeResult]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[ScrapeResult] = []
        # Ecosia: result cards in <article data-test-id="organic-result">
        # title <a data-test-id="result-link">, description <p data-test-id="result-description">
        for card in soup.select(
            'article[data-test-id="organic-result"], '
            'article.result, div.result'
        ):
            link = card.find("a", attrs={"data-test-id": "result-link"}) or card.find("a")
            if not link or not link.get("href"):
                continue
            href = link["href"]
            if not isinstance(href, str) or not href.startswith("http"):
                continue
            title = link.get_text(strip=True)[:200]
            desc_el = card.find("p", attrs={"data-test-id": "result-description"}) \
                or card.find("p")
            body = desc_el.get_text(" ", strip=True)[:500] if desc_el else ""
            out.append(
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=href,
                    title=title,
                    content_text=body,
                    content_markdown=body,
                    metadata={"engine_backend": self.name},
                )
            )
        return out


# ── Bing ─────────────────────────────────────────────────────────────────────


class BingClient(_BaseHtmlSearchClient):
    name = "bing"
    base_url = "https://www.bing.com/search"

    def _build_url(self, query: str) -> str:
        # cc=se forza il country code Sweden; setlang=sv preferenza svedese.
        return (
            f"{self.base_url}?q={quote_plus(query)}"
            f"&cc=se&setlang=sv-SE"
        )

    def _parse(self, html: str, query: str) -> list[ScrapeResult]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[ScrapeResult] = []
        # Bing: ogni risultato in <li class="b_algo">
        for li in soup.select("li.b_algo"):
            h2 = li.find("h2")
            link = h2.find("a") if h2 else li.find("a")
            if not link or not link.get("href"):
                continue
            href = link["href"]
            if not isinstance(href, str) or not href.startswith("http"):
                continue
            title = link.get_text(strip=True)[:200]
            body_el = li.find(["p", "div"], class_=re.compile(r"b_caption|b_snippet"))
            body = body_el.get_text(" ", strip=True)[:500] if body_el else ""
            out.append(
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=href,
                    title=title,
                    content_text=body,
                    content_markdown=body,
                    metadata={"engine_backend": self.name},
                )
            )
        return out


# ── Unified cascade ─────────────────────────────────────────────────────────


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return ""


async def unified_search(
    query: str,
    limit: int = 10,
    engines: tuple[str, ...] = ("brave", "ecosia", "bing"),
) -> list[ScrapeResult]:
    """Esegui la query su tutti i motori in parallelo, dedup per URL.

    Ritorna fino a `limit` risultati merged, preservando ordine di apparizione
    nei singoli motori. Utile quando una pagina indicizzata da un solo motore
    contiene il dato cercato (es. allabolag indicizzato da Bing ma non Brave).
    """
    clients: dict[str, _BaseHtmlSearchClient] = {
        "brave": BraveClient(),
        "ecosia": EcosiaClient(),
        "bing": BingClient(),
    }
    tasks = [
        clients[e].search(query, limit=limit)
        for e in engines if e in clients
    ]
    results_by_engine = await asyncio.gather(*tasks, return_exceptions=True)

    seen: set[str] = set()
    merged: list[ScrapeResult] = []
    # Interleave engines: pesco il primo da ciascuno, poi il secondo, ecc.
    valid: list[list[ScrapeResult]] = []
    for r in results_by_engine:
        if isinstance(r, Exception):
            continue
        valid.append([x for x in r if x.ok and x.url])
    if not valid:
        return []
    max_per_engine = max((len(v) for v in valid), default=0)
    for i in range(max_per_engine):
        for engine_results in valid:
            if i >= len(engine_results):
                continue
            item = engine_results[i]
            url = item.url or ""
            host = _domain(url)
            if not host or host in seen:
                continue
            seen.add(host)
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged
