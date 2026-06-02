"""
Tier 1.5 — Email discovery via Google dorking sui motori di ricerca.

Strategia (vault Lorenzo 2026-06-02):
Usare l'indice di Google/Brave/Bing/Ecosia come database di email pubbliche.
Per ogni azienda con dominio noto:

1. `"@<domain>"` (es. `"@savantmedia.se"`) — trova OGNI pagina indicizzata
   che contiene un'email su quel dominio. Risultati: leadership pages,
   pressmeddelanden, LinkedIn posts, etc.
2. `"<vd_namn>" "<domain>" email` — per un nome noto, trova menzioni
   specifiche.
3. `site:<domain> "vd" OR "VD" OR "verkställande direktör"` — limita al
   sito aziendale, cerca pagina del VD.

Tutto via HTML scraping cost-zero (Brave/Ecosia/Bing — Google ha capcha
spesso, lo evitiamo o usiamo se non triggera). I parser raccolgono tutte
le email che appaiono negli snippet SERP.

Niente API key. Rispetta robots.txt + rate limit del `_rate_limit.py`.

Usage:
    from scrapers.email_search import find_emails_on_domain
    emails = await find_emails_on_domain("savantmedia.se")
    # → ['info@savantmedia.se', 'lorenzo@savantmedia.se', ...]

    emails = await find_emails_for_person(
        "Erik Andersson", "savantmedia.se"
    )
    # → ['erik.andersson@savantmedia.se']
"""

from __future__ import annotations

import asyncio
import re
from typing import Iterable
from urllib.parse import quote_plus

from .base import ScrapeResult
from .httpbs import fetch_and_extract
from .multi_search import BraveClient, BingClient, EcosiaClient


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-åäöÅÄÖ]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _extract_emails_matching_domain(
    text: str, domain: str
) -> set[str]:
    """Estrae tutte le email del dominio target dal testo (snippet SERP)."""
    domain = domain.lower().strip(".")
    out: set[str] = set()
    for m in _EMAIL_RE.finditer(text or ""):
        em = m.group(0).lower()
        _, _, host = em.partition("@")
        if host == domain or host.endswith("." + domain):
            out.add(em)
    return out


async def _query_one_engine(
    client, query: str, domain: str, limit: int = 10
) -> set[str]:
    """Esegui query su un motore, dedup email matchanti il dominio."""
    try:
        results = await client.search(query, limit=limit)
    except Exception:
        return set()
    out: set[str] = set()
    for r in results:
        if not r.ok:
            continue
        # I parser HTML danno content_text+title dello snippet SERP
        bag = " ".join(filter(None, [r.title, r.content_text, r.url]))
        out.update(_extract_emails_matching_domain(bag, domain))
    return out


async def find_emails_on_domain(
    domain: str,
    engines: Iterable[str] = ("brave", "ecosia", "bing"),
    limit_per_engine: int = 10,
) -> set[str]:
    """Trova tutte le email indicizzate su un dominio specifico.

    Usa più query in parallelo:
    - `"@<domain>"`         — match diretto dell'email syntax negli snippet
    - `site:<domain> @`     — limita ai risultati ON quel dominio
    - `"<domain>" kontakt`  — pagine di contatto generiche
    """
    domain = domain.strip().lower().lstrip(".")
    queries = [
        f'"@{domain}"',
        f'site:{domain} "@{domain}"',
        f'site:{domain} kontakt',
    ]
    clients = []
    if "brave" in engines:
        clients.append(BraveClient())
    if "ecosia" in engines:
        clients.append(EcosiaClient())
    if "bing" in engines:
        clients.append(BingClient())

    tasks = []
    for client in clients:
        for q in queries:
            tasks.append(_query_one_engine(client, q, domain, limit_per_engine))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    found: set[str] = set()
    for r in results:
        if isinstance(r, Exception):
            continue
        found |= r
    return found


async def find_emails_for_person(
    person_name: str,
    domain: str,
    engines: Iterable[str] = ("brave", "ecosia", "bing"),
    limit_per_engine: int = 8,
) -> set[str]:
    """Trova email per una persona specifica nel dominio dato.

    Query mirate:
    - `"<person>" "@<domain>"`
    - `"<person>" "<domain>" kontakt email`
    - `site:<domain> "<person>"`
    """
    domain = domain.strip().lower().lstrip(".")
    person_quoted = f'"{person_name}"'
    queries = [
        f'{person_quoted} "@{domain}"',
        f'{person_quoted} "{domain}" email',
        f'site:{domain} {person_quoted}',
    ]
    clients = []
    if "brave" in engines:
        clients.append(BraveClient())
    if "ecosia" in engines:
        clients.append(EcosiaClient())
    if "bing" in engines:
        clients.append(BingClient())

    tasks = []
    for client in clients:
        for q in queries:
            tasks.append(_query_one_engine(client, q, domain, limit_per_engine))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    found: set[str] = set()
    for r in results:
        if isinstance(r, Exception):
            continue
        found |= r

    # Filtra per nome match nell'email local-part
    norm = _norm(person_name)
    parts = [p for p in norm.split() if len(p) >= 2]
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) >= 2 else ""

    scored: list[tuple[int, str]] = []
    for em in found:
        local = em.split("@", 1)[0].lower()
        local_norm = _norm(local)
        score = 0
        if last and last in local_norm:
            score += 4
        if first and first in local_norm:
            score += 2
        if last and first and (
            f"{first}.{last}" in local_norm
            or f"{first[0]}.{last}" in local_norm
        ):
            score += 3
        if score > 0:
            scored.append((score, em))
    scored.sort(reverse=True)
    return {em for _, em in scored}


def _norm(s: str) -> str:
    return (
        s.lower()
        .replace("å", "a").replace("ä", "a").replace("ö", "o")
        .replace("é", "e").replace("ü", "u")
    )
