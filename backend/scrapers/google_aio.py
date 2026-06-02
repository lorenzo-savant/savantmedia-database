"""
Tier 1.5 — Google AI Overview (AIO / SGE) snippet parser.

Google AI Overview è la risposta sintetica IA che appare sopra i risultati
classici. Quando presente, contiene spesso un paragrafo descrittivo + lista
di link "Källor". È utile per le PMI dormienti dove SearXNG/Brave/Bing non
trovano niente ma Google's AIO sintetizza Bolagsverket/allabolag.

Caveat:
- AIO appare solo per query in inglese o italiano nel rollout corrente;
  per query svedesi è incoerente (a volte appare, a volte no).
- L'HTML del box AIO cambia di frequente (`div[data-attrid="sge-result"]` /
  `div.ULSxyf` / `div.M8OgIe`). Parsiamo con multiple selectors a cascata.
- Se non c'è box AIO, ritorniamo None senza errore — si usa fall-through.
- Google a volte blocca con CAPTCHA o consent page. In quel caso il response
  contiene il marker `Before you continue to Google` e ritorniamo None.

Strategia per uso:
1. Lancia query strutturata: `"<foretagsnamn>" sammanfattning vad gör företaget`
2. Se AIO appare, estrai paragrafo + URLs source
3. Usa il paragrafo come `sni_branscher` fallback se SCB ha "00000"
4. Usa gli URLs source come candidati `domain`

Usage:
    from scrapers.google_aio import GoogleAIOClient
    aio = await GoogleAIOClient().fetch('Acme AB sammanfattning')
    # → GoogleAIOSnippet(text, sources)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .base import ScrapeResult
from .httpbs import fetch_and_extract


@dataclass
class GoogleAIOSnippet:
    text: str | None = None
    sources: list[str] = field(default_factory=list)
    source_url: str | None = None
    captcha_blocked: bool = False
    consent_page: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.text and self.text.strip()) or bool(self.sources)


class GoogleAIOClient:
    """Cerca il blocco AI Overview nella SERP di Google."""

    BASE = "https://www.google.com/search"

    # Selectors candidati in ordine di precedenza (più specifici prima)
    AIO_SELECTORS: tuple[str, ...] = (
        'div[data-attrid="sge-result"]',
        'div[data-attrid*="sge"]',
        'div.ULSxyf',
        'div.M8OgIe',
        'div[jsname="V4yzMd"]',
        # fallback generico — un box che contiene "AI Overview" o "AI-översikt"
        'div:has(> span:contains("AI Overview"))',
        'div:has(> span:contains("AI-översikt"))',
    )

    def _build_url(self, query: str) -> str:
        return (
            f"{self.BASE}?q={quote_plus(query)}"
            f"&hl=sv&gl=se&pws=0"
        )

    async def fetch(self, query: str) -> GoogleAIOSnippet:
        url = self._build_url(query)
        res = await fetch_and_extract(url, timeout=25.0)
        snip = GoogleAIOSnippet(source_url=url)
        if not res.ok:
            return snip
        html = res.raw_html_excerpt or res.content_text or ""

        # Bail-out conditions
        if "Before you continue to Google" in html or "/cookie" in (
            res.metadata.get("final_url", "") or ""
        ):
            snip.consent_page = True
            return snip
        if "unusual traffic" in html.lower() or "/sorry/" in (
            res.metadata.get("final_url", "") or ""
        ):
            snip.captcha_blocked = True
            return snip

        soup = BeautifulSoup(html, "html.parser")

        aio_block = None
        for sel in self.AIO_SELECTORS:
            try:
                aio_block = soup.select_one(sel)
            except Exception:
                # CSS4 :has selettore non supportato → ignora
                continue
            if aio_block:
                break

        if not aio_block:
            return snip

        # Testo: prendi tutto il testo del block, dedup spazi
        text = aio_block.get_text(" ", strip=True)
        if text:
            # remove noise tipo "AI Overview · Genererat av AI"
            text = re.sub(r"AI[\s-]?Overview\s*[·∙·]?\s*", "", text)
            text = re.sub(r"AI[\s-]?översikt\s*[·∙·]?\s*", "", text)
            text = re.sub(r"Genererat av AI", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 30:
                snip.text = text[:1500]

        # Source URLs: <a href="http..."> dentro al block, dedup
        seen: set[str] = set()
        for a in aio_block.find_all("a", href=True):
            href = a["href"]
            if not isinstance(href, str):
                continue
            if href.startswith("/url?"):
                # Google redirect: estrai param `q`
                m = re.search(r"[?&]q=([^&]+)", href)
                if m:
                    href = m.group(1)
            if not href.startswith("http"):
                continue
            if href in seen:
                continue
            seen.add(href)
            snip.sources.append(href)
            if len(snip.sources) >= 6:
                break

        return snip
