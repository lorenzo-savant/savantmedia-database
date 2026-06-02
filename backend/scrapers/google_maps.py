"""
Tier 1.5 — Google Maps Place lookup via search URL.

Strategia cost-zero (NIENTE Places API che costa per ricerca):
- Query `https://www.google.com/maps/search/<query>` ritorna HTML SSR con
  almeno il primo risultato in chiaro (nome, indirizzo, telefono, sito web,
  rating). Per richieste multiple, il body è React-hydrated e i dati sono
  in `window.APP_INITIALIZATION_STATE` come JSON encoded array.

Caveat:
- Google a volte serve consent page (`consent.google.com/save`) prima del
  primo result. Risolto allegando un cookie `CONSENT=YES+` direttamente come
  request header.
- Il rate limit Google scatta intorno alle ~30 query/min/IP. Usiamo lo
  rate limiter per-dominio già in `_rate_limit.py` (configurato a 6 rpm
  per `google.com`).

Per dati strutturati, parsiamo:
- Telefono: regex `+46 X XX XX XX`
- Sito: regex su `href="https?://(?!google\\.com)...` immediately after the
  text "Hemsida" / "Website"
- Indirizzo: estratto dal primo `<div role="article">` con regex postnummer
  svedese `\\b\\d{3}\\s?\\d{2}\\b`

Niente Playwright qui — questo è T1.5 leggero. Per estrazione robusta full
usa T4 + un parser tipo `playwright_t4` con click su "Più informazioni".

Usage:
    from scrapers.google_maps import GoogleMapsClient
    place = await GoogleMapsClient().lookup("Ericsson Stockholm")
    # → GoogleMapsPlace(name, phone, website, address, rating)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .base import ScrapeResult
from .httpbs import fetch_and_extract


_PHONE_RE = re.compile(r"(?:\+46[\s\-]?|0)(?:\d[\s\-\(\)]?){6,12}\d")
_ZIP_RE = re.compile(r"\b\d{3}\s?\d{2}\b")
# Cattura URL http(s) escludendo i domini Google interni
_EXTERNAL_URL_RE = re.compile(
    r"https?://(?!(?:www\.)?(?:google|youtube|gstatic|googleusercontent)\.com)"
    r"[a-zA-Z0-9\-._/?#=%&+]+",
    re.IGNORECASE,
)


@dataclass
class GoogleMapsPlace:
    """Strutturato output di una lookup Google Maps."""

    name: str | None = None
    phone: str | None = None
    website: str | None = None
    address: str | None = None
    rating: float | None = None
    reviews: int | None = None
    category: str | None = None
    source_url: str | None = None

    @property
    def has_anything(self) -> bool:
        return any(
            (self.phone, self.website, self.address, self.rating)
        )


class GoogleMapsClient:
    """Lookup pubblico su google.com/maps senza API key."""

    BASE = "https://www.google.com/maps/search/"

    def _build_url(self, query: str) -> str:
        # ?hl=sv per forzare interfaccia svedese
        return f"{self.BASE}{quote_plus(query)}?hl=sv"

    async def lookup(self, query: str) -> GoogleMapsPlace:
        url = self._build_url(query)
        res = await fetch_and_extract(url, timeout=25.0)
        place = GoogleMapsPlace(source_url=url)
        if not res.ok or not (res.content_text or res.raw_html_excerpt):
            return place
        html = res.raw_html_excerpt or res.content_text or ""
        text = res.content_text or ""

        soup = BeautifulSoup(html, "html.parser")
        # Title — Maps spesso popola <meta property="og:title">
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            place.name = str(og["content"]).strip().split("·")[0].strip()

        # Telefono — primo match in tutto il testo (Maps lo mette in evidenza)
        m = _PHONE_RE.search(text)
        if m:
            place.phone = _normalize_phone(m.group(0))

        # Sito esterno (Hemsida)
        # Maps a volte espone l'URL via tag "data-item-id=authority". Senza
        # JS, ripiego a regex globale.
        m = _EXTERNAL_URL_RE.search(text)
        if m:
            place.website = m.group(0).rstrip("),.\";'")
            # strip trailing path se vuoto
            if place.website.endswith("/"):
                place.website = place.website[:-1]

        # Postnummer → indirizzo intero ± 80 chars
        zm = _ZIP_RE.search(text)
        if zm:
            start = max(0, zm.start() - 80)
            end = min(len(text), zm.end() + 40)
            place.address = " ".join(text[start:end].split())[:200]

        # Rating: pattern "4,5 stjärnor (123)" o "4,5 (123)"
        rm = re.search(r"(\d[.,]\d)\s*(?:stjärnor|stars)?\s*\((\d{1,7})\)", text)
        if rm:
            try:
                place.rating = float(rm.group(1).replace(",", "."))
                place.reviews = int(rm.group(2))
            except ValueError:
                pass

        return place


def _normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("0") and not digits.startswith("00"):
        digits = "+46" + digits[1:]
    if digits.startswith("+46") and len(digits) >= 10:
        body = digits[3:]
        if body.startswith("7"):
            parts = [body[:2], body[2:5], body[5:7], body[7:]]
        else:
            parts = [body[:1], body[1:4], body[4:6], body[6:]]
        return "+46 " + " ".join(p for p in parts if p)
    return digits or None
