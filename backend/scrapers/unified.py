"""
Tier 1.5 — Unified company lookup con cascade fallback.

Per ogni azienda risolve in parallelo:
- **SearXNG/DDG** (default — engine multipli)
- **Brave Search HTML** (no API key, indipendente da Google)
- **Ecosia HTML** (Bing-powered ma più clemente al rate limit)
- **Bing HTML** (Microsoft index)
- **Google Maps Place** (telefono, sito, indirizzo, rating)
- **Google AI Overview** (sintesi IA + sources) — opzionale, può triggerare CAPTCHA

Tutti i sub-tasks girano in `asyncio.gather` con `return_exceptions=True`,
quindi un blocco non rompe gli altri. Il deduper si basa sul domain del URL
trovato; il caller decide il primo candidato che `looks_like_company_domain`.

Niente API key. Niente cost. Rispetta robots.txt e rate limits.

Usage:
    from scrapers.unified import unified_company_lookup
    lookup = await unified_company_lookup("Spotify AB", "Stockholm")
    if lookup.candidate_domains:
        primary = lookup.candidate_domains[0]
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .base import ScrapeResult
from .multi_search import BraveClient, EcosiaClient, BingClient
from .searxng import SearXNGClient
from .google_maps import GoogleMapsClient, GoogleMapsPlace
from .google_aio import GoogleAIOClient, GoogleAIOSnippet
from .exa_search import ExaClient, exa_enabled


@dataclass
class CompanyLookup:
    """Aggregato dei risultati cross-engine per una singola azienda."""

    foretagsnamn: str
    stad: str = ""

    serp_results: list[ScrapeResult] = field(default_factory=list)
    candidate_domains: list[str] = field(default_factory=list)
    maps_place: GoogleMapsPlace | None = None
    aio: GoogleAIOSnippet | None = None
    engines_used: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


_BLACKLIST = {
    "allabolag.se", "ratsit.se", "bolagsfakta.se", "merinfo.se",
    "hitta.se", "eniro.se", "linkedin.com", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "youtube.com",
    "google.com", "google.se", "bing.com", "duckduckgo.com",
    "wikipedia.org", "se.linkedin.com", "bolagsverket.se",
    "scb.se", "europages.com", "europages.se", "kompass.com",
    "dnb.com", "northdata.de", "northdata.com",
}


def _is_aggregator(host: str) -> bool:
    return host in _BLACKLIST or any(
        host.endswith("." + b) for b in _BLACKLIST
    )


async def _safe(coro, label: str, errors: dict[str, str]):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        errors[label] = f"{type(exc).__name__}: {exc}"
        return None


async def unified_company_lookup(
    foretagsnamn: str,
    stad: str = "",
    *,
    use_maps: bool = True,
    use_aio: bool = False,
    limit_per_engine: int = 6,
) -> CompanyLookup:
    """Esegui lookup parallel multi-engine per un'azienda svedese.

    Parameters
    ----------
    foretagsnamn:
        Nome ufficiale (es. "Spotify AB").
    stad:
        Città dell'HQ (per filtrare risultati locali ambigui).
    use_maps:
        Se True, lookup parallelo su Google Maps per telefono/sito/indirizzo.
    use_aio:
        Se True, prova a estrarre AI Overview. Potrebbe triggerare CAPTCHA
        — usalo solo come fallback ultimo.
    """
    out = CompanyLookup(foretagsnamn=foretagsnamn, stad=stad)
    base_query = f'"{foretagsnamn}"' + (f" {stad}" if stad else "")
    site_se_query = f'{base_query} hemsida'

    maps = GoogleMapsClient()
    aio = GoogleAIOClient()

    tasks: dict[str, asyncio.Task] = {}

    # Strategia websearch: se EXA_API_KEY è presente, Exa è il backend primario
    # (no ban per-IP) e SALTIAMO i client HTML rate-limitati. Altrimenti
    # fallback su SearXNG/Brave/Ecosia/Bing come prima.
    if exa_enabled():
        tasks["exa"] = asyncio.create_task(
            _safe(
                ExaClient().search(base_query, limit=limit_per_engine, category="company"),
                "exa", out.errors,
            )
        )
    else:
        searxng = SearXNGClient()
        brave = BraveClient()
        ecosia = EcosiaClient()
        bing = BingClient()
        tasks["searxng"] = asyncio.create_task(
            _safe(searxng.search(site_se_query, limit=limit_per_engine),
                  "searxng", out.errors)
        )
        tasks["brave"] = asyncio.create_task(
            _safe(brave.search(site_se_query, limit=limit_per_engine),
                  "brave", out.errors)
        )
        tasks["ecosia"] = asyncio.create_task(
            _safe(ecosia.search(site_se_query, limit=limit_per_engine),
                  "ecosia", out.errors)
        )
        tasks["bing"] = asyncio.create_task(
            _safe(bing.search(site_se_query, limit=limit_per_engine),
                  "bing", out.errors)
        )
    if use_maps:
        tasks["maps"] = asyncio.create_task(
            _safe(maps.lookup(base_query), "maps", out.errors)
        )
    if use_aio:
        aio_q = f"{base_query} sammanfattning vad gör företaget"
        tasks["aio"] = asyncio.create_task(
            _safe(aio.fetch(aio_q), "aio", out.errors)
        )

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    by_engine = dict(zip(tasks.keys(), results, strict=True))

    # Aggregate SERP results + domain candidates (Exa first — most authoritative)
    seen_domains: set[str] = set()
    for engine in ("exa", "brave", "ecosia", "bing", "searxng"):
        r = by_engine.get(engine)
        if r is None or isinstance(r, Exception):
            continue
        out.engines_used.append(engine)
        for item in r:
            if not item.ok or not item.url:
                continue
            out.serp_results.append(item)
            dom = _domain(item.url)
            if not dom or _is_aggregator(dom) or dom in seen_domains:
                continue
            seen_domains.add(dom)
            out.candidate_domains.append(dom)

    # Maps
    maps_res = by_engine.get("maps")
    if maps_res is not None and not isinstance(maps_res, Exception):
        out.maps_place = maps_res
        if maps_res and maps_res.website:
            dom = _domain(maps_res.website)
            if dom and not _is_aggregator(dom) and dom not in seen_domains:
                seen_domains.add(dom)
                # In testa — Maps è generalmente molto affidabile
                out.candidate_domains.insert(0, dom)

    # AIO
    aio_res = by_engine.get("aio")
    if aio_res is not None and not isinstance(aio_res, Exception):
        out.aio = aio_res
        if aio_res:
            for src in aio_res.sources:
                dom = _domain(src)
                if dom and not _is_aggregator(dom) and dom not in seen_domains:
                    seen_domains.add(dom)
                    out.candidate_domains.append(dom)

    return out
