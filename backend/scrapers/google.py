"""
Tier 1.5 — Google Search SERP scraper con anti-bot mimicking umano.

Google ha l'indice più ricco del web ma anche il blocco anti-bot più
aggressivo. Strategia per evitare CAPTCHA:

1. **CONSENT cookie pre-impostato** — bypassa la consent landing page che
   altrimenti redirige tutti i bot scrapers.
2. **User-Agent rotation** — pool di 10 UA Chrome/Firefox/Safari real.
3. **Sec-Ch-* headers** — Client Hints che Chrome 116+ invia sempre,
   notabile assenza = bot signature.
4. **Sec-Fetch-* headers** — Fetch Metadata che browser real popolano.
5. **Random delay 2-5s** prima di ogni request (mimicra reading time).
6. **Accept-Language sv-SE primario** — Google SE serve risultati svedesi
   più ricchi.

Cookie `CONSENT=YES+cb` skippa la consent.google.com landing che
altrimenti redirigerebbe la prima query. Cookie con `SE.sv` localizza.

Uso primario:
- `GoogleClient.search(query)` — SERP results (top 10)
- `GoogleClient.search_dork_emails(domain)` — query Google specifiche
  per trovare email su un dominio

Per la SERP AI Overview separata: vedi `google_aio.py`.
"""

from __future__ import annotations

import random
import re
from typing import Any
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from .base import ScrapeResult
from .httpbs import fetch_and_extract


# Pool di User-Agent Chromium/Firefox/Safari real, rotated per request.
_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 "
    "Firefox/131.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
)


def _pick_ua() -> str:
    return random.choice(_USER_AGENTS)


def _stealth_headers() -> dict[str, str]:
    """Headers che imitano una sessione Chrome desktop svedese reale."""
    ua = _pick_ua()
    is_chrome = "Chrome" in ua and "Firefox" not in ua
    headers = {
        "User-Agent": ua,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
        # Cookie consent pre-set: skip consent.google.com landing
        "Cookie": "CONSENT=YES+cb.20240101-12-p0.sv+FX+424; SOCS=CAESEwgDEgk0NzU3MTQ4NDQaAnN2IAEaBgiAtJSyBg",
    }
    if is_chrome:
        # Chrome Client Hints — assenza = bot signature
        chrome_ver = re.search(r"Chrome/(\d+)", ua)
        cv = chrome_ver.group(1) if chrome_ver else "130"
        platform = "Windows" if "Windows" in ua else (
            "macOS" if "Macintosh" in ua else "Linux"
        )
        headers.update({
            "Sec-Ch-Ua": (
                f'"Chromium";v="{cv}", "Not_A Brand";v="24", '
                f'"Google Chrome";v="{cv}"'
            ),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": f'"{platform}"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        })
    return headers


class GoogleClient:
    """Google SERP scraper con stealth headers + CONSENT cookie."""

    name = "google"
    BASE = "https://www.google.com/search"

    def _build_url(self, query: str, *, num: int = 10) -> str:
        # gl=se gl + hl=sv  per geolocalizzazione svedese
        # pws=0 per disabilitare personalizzazione (impatta retro-tracking)
        return (
            f"{self.BASE}?q={quote_plus(query)}"
            f"&hl=sv&gl=se&pws=0&num={num}"
        )

    async def search(
        self, query: str, limit: int = 10
    ) -> list[ScrapeResult]:
        url = self._build_url(query, num=limit)
        # fetch_and_extract attiva: rate_limit per-domain + human_delay. robots
        # bypassato qui: è una query al motore di ricerca, non crawling di siti
        # terzi (gli URL aziendali scaricati a valle rispettano robots.txt).
        res = await fetch_and_extract(url, timeout=25.0, ignore_robots=True)
        if not res.ok:
            return [
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=url,
                    error=f"google fetch failed: {res.error}",
                    metadata={"engine_backend": self.name},
                )
            ]
        html = res.raw_html_excerpt or res.content_text or ""
        if not html:
            return []
        # Bail-out su consent/CAPTCHA che siano sfuggiti al cookie
        if "Before you continue to Google" in html or "/sorry/" in (
            res.metadata.get("final_url", "") or ""
        ):
            return [
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=url,
                    error="google consent/CAPTCHA wall",
                    metadata={"engine_backend": self.name,
                              "policy_block": "consent_or_captcha"},
                )
            ]
        return self._parse(html, query)

    def _parse(self, html: str, query: str) -> list[ScrapeResult]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[ScrapeResult] = []
        # Google ha 3 layout possibili (desktop, tablet, semplificato).
        # Selettori robusti che funzionano nel desktop layout 2026:
        # - Risultati organici: div.g  OR  div[data-hveid] con <h3>
        # - Link: a[href^="http"] dentro
        # - Snippet body: div[data-sncf] OR div.VwiC3b
        candidates = soup.select(
            "div.g, div[data-hveid]:has(h3), div.tF2Cxc"
        )
        seen_urls: set[str] = set()
        for div in candidates:
            link = div.find("a", href=True)
            if not link:
                continue
            href = str(link.get("href") or "")
            if not href.startswith("http"):
                continue
            # Google a volte wrappa in `/url?q=...`
            if "google.com/url" in href:
                m = re.search(r"[?&]q=([^&]+)", href)
                if m:
                    href = quote_plus(m.group(1), safe=":/?&=%#")
            if href in seen_urls:
                continue
            seen_urls.add(href)
            title_el = div.find("h3")
            title = title_el.get_text(strip=True) if title_el else ""
            body_el = div.select_one(
                "div[data-sncf], div.VwiC3b, span[data-sncf], div.kvKEAb, "
                "div.IsZvec, span.aCOpRe"
            )
            body = body_el.get_text(" ", strip=True) if body_el else ""
            if not body:
                body = div.get_text(" ", strip=True)[:500]
            out.append(
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=href,
                    title=title[:200],
                    content_text=body[:500],
                    content_markdown=body[:500],
                    metadata={"engine_backend": self.name},
                )
            )
            if len(out) >= 10:
                break
        return out

    async def dork_emails_on_domain(
        self, domain: str, limit: int = 10
    ) -> set[str]:
        """Estrai email su dominio combinando 3 dork queries.

        Le SERP Google a volte espongono email nel snippet (rare ma succede
        per pagine team / kontakt che hanno l'email nel meta description).
        """
        from .email_search import _extract_emails_matching_domain  # local import to avoid circular

        queries = [
            f'"@{domain}"',
            f'site:{domain} "@{domain}"',
            f'site:{domain} (kontakt OR medarbetare OR ledning)',
        ]
        found: set[str] = set()
        for q in queries:
            try:
                results = await self.search(q, limit=limit)
            except Exception:
                continue
            for r in results:
                if not r.ok:
                    continue
                bag = " ".join(filter(None, [r.title, r.content_text, r.url]))
                found |= _extract_emails_matching_domain(bag, domain)
        return found
