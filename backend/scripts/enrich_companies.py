"""
Bulk enrichment delle aziende importate da Bolagsverket.

Le 500 aziende dal bulk T0 hanno solo `organisationsnummer + adress + città`.
Mancano: domain, reception_telefon, email_info, antal_anstallda, contatti DM.

Per ogni azienda senza domain lo script:

1. Multi-source search parallelo (SearXNG/DDG + Brave Search + Google Maps T4)
2. Valida i risultati: filtro contro siti aggregatori.
3. T2→T4 fetch escalation della homepage: + /kontakt/ + /om-oss/ + /about/.
4. Estrae con regex:
   - email aziendali (escludendo `info@`/`kontakt@` solo se trova specifiche)
   - telefoni svedesi (+46 / 0XX prefisso)
   - eventuali persone con ruoli DM (VD, CEO, ägare, grundare)
5. UPDATE `public.companies` con i campi popolati + INSERT audit `public.sources`.
6. Se trova DM contacts, INSERT in `public.contacts` con `is_dm=true`.

Idempotente: se `domain` è già popolato salta l'azienda.
Cache-first: se `enriched_at` e fresco salta l'azienda (TTL 30gg).

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_companies --limit 50
    .venv/Scripts/python.exe -m scripts.enrich_companies --offset 0 --limit 125 --workers 5
    .venv/Scripts/python.exe -m scripts.enrich_companies --dry-run --limit 10
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from supabase import Client, create_client

from scrapers.searxng import SearXNGClient

from enrichment.cache_gate import CacheGate
from enrichment.escalate import fetch_with_escalation
from searchers.multi_source import search_all_sources

console = Console()


# ── Regex ────────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-åäöÅÄÖ]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?:\+46[\s\-]?|0)(?:\d[\s\-\(\)]?){6,12}\d")

# Generic mailbox local-parts che vanno in email_info (azienda) ma non in contatti
_GENERIC_LOCALS = {
    "info", "kontakt", "contact", "support", "hello", "hej", "hi",
    "office", "press", "media", "careers", "career", "jobs",
    "sales", "hr", "admin", "noreply", "no-reply", "postmaster",
    "webmaster", "marketing", "newsroom",
}

# Hint di ruoli che identificano un DM
_DM_ROLE_HINTS = (
    "vd", "verkställande direktör", "vd ", "ceo", "ägare", "owner",
    "grundare", "founder", "co-founder", "managing director", "ägare/vd",
    "president", "ordförande", "vice vd", "vvd",
)

# Domini da SCARTARE come "homepage aziendale": aggregatori, social, directory
_BLACKLIST_DOMAINS = {
    "allabolag.se", "ratsit.se", "bolagsfakta.se", "merinfo.se",
    "hitta.se", "eniro.se", "linkedin.com", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "youtube.com",
    "google.com", "google.se", "bing.com", "duckduckgo.com",
    "wikipedia.org", "yelp.se", "yelp.com", "indeed.com", "indeed.se",
    "glassdoor.com", "glassdoor.se", "trustpilot.com", "se.linkedin.com",
    "bolagsverket.se", "scb.se", "skatteverket.se", "lansstyrelsen.se",
    "regeringen.se", "wikidata.org", "europages.com", "europages.se",
    "kompass.com", "dnb.com", "northdata.de", "northdata.com",
    "youtube.de", "x-default.com",
}


@dataclass
class CompanyRow:
    id: str
    foretagsnamn: str
    organisationsnummer: str
    stad: str
    domain: str | None
    reception_telefon: str
    email_info: str
    antal_anstallda: int | None


@dataclass
class Enrichment:
    domain: str | None = None
    homepage_url: str | None = None
    reception_telefon: str | None = None
    email_info: str | None = None
    extra_emails: list[str] = None  # type: ignore[assignment]
    extra_phones: list[str] = None  # type: ignore[assignment]
    dm_contacts: list[dict[str, str]] = None  # type: ignore[assignment]
    source_url: str | None = None
    raw_excerpt: str | None = None
    scraper_tier: int = 2

    def __post_init__(self) -> None:
        if self.extra_emails is None:
            self.extra_emails = []
        if self.extra_phones is None:
            self.extra_phones = []
        if self.dm_contacts is None:
            self.dm_contacts = []

    @property
    def has_anything(self) -> bool:
        return bool(
            self.domain or self.reception_telefon or self.email_info
            or self.dm_contacts
        )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _supabase() -> Client:
    load_dotenv()
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SECRET_KEY"]
    return create_client(url, key)


def _name_tokens(name: str) -> list[str]:
    """Estrae token significativi dal nome azienda (>= 3 char, no suffix legali)."""
    suffixes = {"ab", "aktiebolag", "hb", "kb", "ek", "för", "förening"}
    tokens = re.findall(r"[a-zåäö0-9]+", name.lower())
    return [t for t in tokens if len(t) >= 3 and t not in suffixes]


def _looks_like_company_domain(domain: str, company_name: str) -> bool:
    """Il dominio è plausibilmente la homepage dell'azienda?"""
    domain = domain.lower().lstrip(".")
    if domain in _BLACKLIST_DOMAINS:
        return False
    if any(domain.endswith("." + b) or domain == b for b in _BLACKLIST_DOMAINS):
        return False
    tokens = _name_tokens(company_name)
    if not tokens:
        return False
    base = domain.split(":")[0].split("/")[0]
    # Almeno uno dei primi 2 token del nome deve apparire nel dominio
    for tok in tokens[:3]:
        if tok in base:
            return True
    return False


def _extract_domain(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower().split("@")[-1]
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


def _normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
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


# ── Fetch & parse ────────────────────────────────────────────────────────────


async def _find_domain(
    company: CompanyRow, client: SearXNGClient,
    *, skip_brave: bool = False, skip_maps: bool = True,
) -> str | None:
    """Cerca il dominio su TUTTI i motori in parallelo (DDG, Brave, GMaps)."""
    results = await search_all_sources(
        company.foretagsnamn,
        company.stad,
        t1_client=client,
        skip_brave=skip_brave,
        skip_maps=skip_maps,
    )
    for r in results:
        dom = _extract_domain(r.url)
        if dom and _looks_like_company_domain(dom, company.foretagsnamn):
            return dom
    return None


def _extract_evidence(
    text: str,
    company_name: str,
    company_domain: str | None,
) -> tuple[str | None, str | None, list[dict[str, str]]]:
    """Da una pagina, ritorna (reception_phone, email_info, dm_contacts)."""
    if not text:
        return None, None, []

    lower = text.lower()
    emails = list(dict.fromkeys(_EMAIL_RE.findall(text)))
    phones_raw = list(dict.fromkeys(_PHONE_RE.findall(text)))

    # email_info: prima email generica sullo stesso dominio
    email_info: str | None = None
    for em in emails:
        local, _, dom = em.lower().partition("@")
        if company_domain and dom != company_domain.lower():
            continue
        if local in _GENERIC_LOCALS:
            email_info = em.lower()
            break
    if email_info is None:
        # fallback: prima email sul dominio
        for em in emails:
            _, _, dom = em.lower().partition("@")
            if company_domain and dom == company_domain.lower():
                email_info = em.lower()
                break

    # reception_phone: primo telefono nella pagina
    reception_phone = _normalize_phone(phones_raw[0]) if phones_raw else None

    # DM contacts: cerca pattern "<nome> <cognome>\n<ruolo>" o viceversa
    dm_contacts: list[dict[str, str]] = []
    # Pattern: capitalized name followed by role hint within 80 chars
    name_re = re.compile(
        r"([A-ZÅÄÖ][a-zåäö]{1,20}(?:\s+[A-ZÅÄÖ][a-zåäö'\-]{1,25}){1,3})"
    )
    seen_names: set[str] = set()
    for m in name_re.finditer(text):
        name = m.group(1).strip()
        if name.lower() in seen_names:
            continue
        # window ± 200 chars
        start = max(0, m.start() - 200)
        end = min(len(text), m.end() + 200)
        window = text[start:end].lower()
        role: str | None = None
        for hint in _DM_ROLE_HINTS:
            if hint in window:
                role = hint
                break
        if not role:
            continue
        # Ignora nomi che assomigliano a nomi di città / paesi comuni
        if any(
            w in name.lower()
            for w in ("stockholm", "göteborg", "malmö", "uppsala", "sverige")
        ):
            continue
        # Try to find a personal email near the name (same domain, name match)
        person_email: str | None = None
        first = name.split()[0].lower()
        last = name.split()[-1].lower() if len(name.split()) > 1 else ""
        for em in emails:
            em_low = em.lower()
            local, _, dom = em_low.partition("@")
            if company_domain and dom != company_domain.lower():
                continue
            if local in _GENERIC_LOCALS:
                continue
            if first in local or last in local:
                person_email = em_low
                break
        person_phone: str | None = None
        win_phones = _PHONE_RE.findall(text[start:end])
        if win_phones:
            person_phone = _normalize_phone(win_phones[0])
        seen_names.add(name.lower())
        dm_contacts.append(
            {
                "namn": name,
                "roll": role,
                "email": person_email or "",
                "telefon": person_phone or "",
            }
        )
        if len(dm_contacts) >= 5:
            break

    return reception_phone, email_info, dm_contacts


async def _scrape_pages(
    domain: str, company: CompanyRow
) -> Enrichment:
    """Tenta homepage + /kontakt + /om-oss e fonde l'evidence."""
    base = f"https://{domain}"
    paths = ["/", "/kontakt/", "/contact/", "/om-oss/", "/om/", "/about/"]
    enr = Enrichment(domain=domain, homepage_url=base)
    best_excerpt: str | None = None
    for path in paths:
        url = base + path
        try:
            res = await fetch_with_escalation(url, timeout=20.0, max_attempts=2)
        except Exception:
            continue
        if not res.ok or not res.content_text:
            continue
        rec_phone, em_info, dms = _extract_evidence(
            res.content_text, company.foretagsnamn, domain
        )
        # Aggiorna al tier piu alto usato (escalation tracking)
        if res.tier > enr.scraper_tier:
            enr.scraper_tier = res.tier
        if rec_phone and not enr.reception_telefon:
            enr.reception_telefon = rec_phone
            enr.source_url = url
        if em_info and not enr.email_info:
            enr.email_info = em_info
            enr.source_url = enr.source_url or url
        for dm in dms:
            if not any(c["namn"] == dm["namn"] for c in enr.dm_contacts):
                enr.dm_contacts.append(dm)
        if not best_excerpt and res.content_text:
            best_excerpt = res.content_text[:500]
        # Stop early se abbiamo tutto
        if enr.reception_telefon and enr.email_info and enr.dm_contacts:
            break
        await asyncio.sleep(0.2)
    enr.raw_excerpt = best_excerpt
    return enr


async def enrich_one(
    company: CompanyRow, t1: SearXNGClient,
    *, skip_brave: bool = False, skip_maps: bool = True,
) -> Enrichment:
    """Pipeline completa per una singola azienda."""
    domain = company.domain
    if not domain:
        domain = await _find_domain(company, t1, skip_brave=skip_brave, skip_maps=skip_maps)
        if not domain:
            return Enrichment()
    return await _scrape_pages(domain, company)


# ── DB I/O ───────────────────────────────────────────────────────────────────


def _fetch_targets(
    sb: Client, offset: int, limit: int, only_no_domain: bool
) -> list[CompanyRow]:
    q = sb.table("companies").select(
        "id, foretagsnamn, organisationsnummer, stad, domain, "
        "reception_telefon, email_info, antal_anstallda"
    ).eq("arkiverad", False).order("foretagsnamn")
    if only_no_domain:
        q = q.or_("domain.is.null,domain.eq.")
    resp = q.range(offset, offset + limit - 1).execute()
    rows: list[CompanyRow] = []
    for r in resp.data:
        rows.append(
            CompanyRow(
                id=r["id"],
                foretagsnamn=r["foretagsnamn"],
                organisationsnummer=r.get("organisationsnummer") or "",
                stad=r.get("stad") or "",
                domain=(r.get("domain") or None) or None,
                reception_telefon=r.get("reception_telefon") or "",
                email_info=r.get("email_info") or "",
                antal_anstallda=r.get("antal_anstallda"),
            )
        )
    return rows


def _persist(
    sb: Client, company: CompanyRow, enr: Enrichment
) -> tuple[int, int]:
    """Persiste l'arricchimento. Ritorna (campi_aggiornati, contatti_inseriti)."""
    update: dict[str, Any] = {}
    if enr.domain and not company.domain:
        update["domain"] = enr.domain
    if enr.reception_telefon and not company.reception_telefon:
        update["reception_telefon"] = enr.reception_telefon
    if enr.email_info and not company.email_info:
        update["email_info"] = enr.email_info

    fields_updated = 0
    if update:
        sb.table("companies").update(update).eq("id", company.id).execute()
        fields_updated = len(update)
        # Audit row per ogni campo
        for field, _val in update.items():
            sb.table("sources").insert(
                {
                    "company_id": company.id,
                    "field_name": f"companies.{field}",
                    "source_url": enr.source_url or (
                        f"https://{enr.domain}" if enr.domain else None
                    ),
                    "scraper_tier": enr.scraper_tier,
                    "raw_excerpt": (enr.raw_excerpt or "")[:500],
                    "critic_note": (
                        f"enrich_companies.py — multi-source search + "
                        f"T{enr.scraper_tier} scrape domain={enr.domain or '-'}"
                    ),
                }
            ).execute()

    contacts_inserted = 0
    for dm in enr.dm_contacts:
        if not dm.get("namn"):
            continue
        # Idempotenza: skip se contact con stesso nome+company già esiste
        existing = (
            sb.table("contacts")
            .select("id")
            .eq("company_id", company.id)
            .eq("namn", dm["namn"])
            .limit(1)
            .execute()
        )
        if existing.data:
            continue
        payload = {
            "company_id": company.id,
            "namn": dm["namn"],
            "roll": dm.get("roll") or "",
            "email": (dm.get("email") or "").lower(),
            "telefon": dm.get("telefon") or "",
            "is_dm": True,
            "verifierad": bool(dm.get("email")),
            "verifieringsmetod": "foretagswebbplats" if dm.get("email") else None,
            "verifieringskalla": enr.source_url or f"https://{enr.domain}",
            "verifierat_av": "agent:enrich_companies-2026-05-28"
            if dm.get("email") else "",
            "verifierat_datum": datetime.now(timezone.utc).isoformat()
            if dm.get("email") else None,
        }
        # Remove None values to let DB defaults apply
        payload = {k: v for k, v in payload.items() if v is not None}
        result = sb.table("contacts").insert(payload).execute()
        if result.data:
            contact_id = result.data[0]["id"]
            sb.table("sources").insert(
                {
                    "company_id": company.id,
                    "contact_id": contact_id,
                    "field_name": "contacts.namn",
                    "source_url": enr.source_url or f"https://{enr.domain}",
                    "scraper_tier": enr.scraper_tier,
                    "raw_excerpt": (enr.raw_excerpt or "")[:500],
                    "critic_note": (
                        f"enrich_companies.py — DM detected via role hint "
                        f"'{dm.get('roll', '')}'"
                    ),
                }
            ).execute()
            contacts_inserted += 1
    return fields_updated, contacts_inserted


# ── Orchestration ────────────────────────────────────────────────────────────


async def _worker(
    name: str,
    queue: asyncio.Queue[CompanyRow],
    sb: Client,
    t1: SearXNGClient,
    dry_run: bool,
    stats: dict[str, int],
    cache_gate: CacheGate | None = None,
    *, skip_brave: bool = False, skip_maps: bool = True,
) -> None:
    while True:
        try:
            company = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        # Cache-first gate: salta se già arricchita di recente
        if cache_gate:
            verdict = await cache_gate.check(company.id)
            if verdict.source == "cache":
                console.print(
                    f"[dim][{name}] SKIP {company.foretagsnamn[:40]:40} "
                    f"(cache HIT, last enriched={verdict.reason})[/]"
                )
                stats["cache_hit"] += 1
                queue.task_done()
                continue

        try:
            enr = await enrich_one(company, t1, skip_brave=skip_brave, skip_maps=skip_maps)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]ERR [{name}] {company.foretagsnamn}: {exc}[/]")
            stats["errors"] += 1
            queue.task_done()
            continue

        if enr.has_anything:
            stats["enriched"] += 1
            tag_dom = enr.domain or "—"
            tag_tel = "T" if enr.reception_telefon else "-"
            tag_em = "E" if enr.email_info else "-"
            tag_dm = f"DM:{len(enr.dm_contacts)}"
            console.print(
                f"[green][{name}] OK {company.foretagsnamn[:40]:40} "
                f"→ {tag_dom[:30]:30} {tag_tel}{tag_em} {tag_dm}[/]"
            )
            if not dry_run:
                fu, ci = _persist(sb, company, enr)
                stats["fields_updated"] += fu
                stats["contacts_inserted"] += ci
                if cache_gate and not dry_run:
                    cache_gate.mark_enriched(
                        company.id,
                        tier=2,
                        source_url=enr.source_url or f"https://{enr.domain}" if enr.domain else None,
                        fields_updated=fu,
                    )
        else:
            stats["empty"] += 1
            console.print(
                f"[dim][{name}] -- {company.foretagsnamn[:40]:40} "
                f"(no evidence)[/]"
            )
        queue.task_done()


async def main(
    offset: int, limit: int, workers: int, dry_run: bool, all_companies: bool,
    *, skip_brave: bool = False, with_maps: bool = False,
) -> None:
    sb = _supabase()
    cache_gate = CacheGate(sb)
    only_no_domain = not all_companies
    targets = _fetch_targets(sb, offset=offset, limit=limit,
                              only_no_domain=only_no_domain)
    console.print(
        f"[bold cyan]Fetched {len(targets)} companies "
        f"(offset={offset}, limit={limit}, only_no_domain={only_no_domain}) "
        f"workers={workers} dry_run={dry_run}[/]"
    )
    if not targets:
        return

    t1 = SearXNGClient()
    queue: asyncio.Queue[CompanyRow] = asyncio.Queue()
    for c in targets:
        queue.put_nowait(c)

    stats = {
        "enriched": 0, "empty": 0, "errors": 0, "cache_hit": 0,
        "fields_updated": 0, "contacts_inserted": 0,
    }

    skip_maps = not with_maps
    tasks = [
        asyncio.create_task(
            _worker(
                f"w{i+1}", queue, sb, t1, dry_run, stats, cache_gate,
                skip_brave=skip_brave, skip_maps=skip_maps,
            )
        )
        for i in range(workers)
    ]
    await asyncio.gather(*tasks)

    table = Table(title="Enrichment summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    table.add_row("Companies processed", str(len(targets)))
    table.add_row("Cache HIT (skipped)", str(stats["cache_hit"]))
    table.add_row("Enriched (≥1 field)", str(stats["enriched"]))
    table.add_row("Empty (no evidence)", str(stats["empty"]))
    table.add_row("Errors", str(stats["errors"]))
    table.add_row("Company fields updated", str(stats["fields_updated"]))
    table.add_row("DM contacts inserted", str(stats["contacts_inserted"]))
    console.print(table)
    if dry_run:
        console.print("[bold yellow]DRY-RUN — no DB writes[/]")


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--workers", type=int, default=5,
                   help="Concorrenza interna (default 5).")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--all", action="store_true",
                   help="Include anche aziende con domain già popolato.")
    p.add_argument("--no-brave", action="store_true",
                   help="Salta Brave Search, solo SearXNG/DDG.")
    p.add_argument("--with-maps", action="store_true",
                   help="Includi Google Maps T4 (più lento).")
    args = p.parse_args()
    asyncio.run(
        main(
            offset=args.offset,
            limit=args.limit,
            workers=args.workers,
            dry_run=args.dry_run,
            all_companies=args.all,
            skip_brave=args.no_brave,
            with_maps=args.with_maps,
        )
    )


if __name__ == "__main__":
    _cli()
