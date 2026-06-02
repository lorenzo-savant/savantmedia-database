"""
Bulk enrichment v2 — usa multi-engine `unified_company_lookup`.

Per ogni azienda con `domain` / `reception_telefon` / `email_info` mancante:

1. `unified_company_lookup(foretagsnamn, stad)`
   - Brave + Ecosia + Bing + SearXNG in parallelo → candidate domains
   - Google Maps Place → phone, website, address
2. Sceglie il primo `candidate_domain` valido (token nome aziendale presente).
3. Se domain trovato → T2 fetch della homepage per estrarre email_info (`info@<domain>`).
4. Se Maps ha phone → usa come reception_telefon.
5. UPDATE companies + INSERT audit `sources` (tier=1.5).

IMPORTANTE — NO PARSING DI DM CONTACTS qui. Il parser regex precedente
catturava nomi-menu/nomi-azienda erroneamente. La pipeline DM resta
delegata a `enrich_existing.py` (con vincoli più stretti) o subagent
WebSearch.

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_v2_unified --limit 10 --dry-run
    .venv/Scripts/python.exe -m scripts.enrich_v2_unified --limit 50 --workers 2
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from typing import Any

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

from scrapers import unified_company_lookup
from scrapers.httpbs import fetch_and_extract

console = Console()


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-åäöÅÄÖ]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_GENERIC_LOCALS = {
    "info", "kontakt", "contact", "support", "hello", "hej",
    "office", "press", "media", "careers", "career", "jobs",
    "sales", "hr", "admin", "marketing", "newsroom",
}


@dataclass
class CompanyRow:
    id: str
    foretagsnamn: str
    organisationsnummer: str
    stad: str
    domain: str
    reception_telefon: str
    email_info: str


def _supabase() -> Client:
    load_dotenv()
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )


def _name_tokens(name: str) -> list[str]:
    suffixes = {"ab", "aktiebolag", "hb", "kb", "ek", "för", "förening"}
    tokens = re.findall(r"[a-zåäö0-9]+", name.lower())
    return [t for t in tokens if len(t) >= 3 and t not in suffixes]


def _domain_matches_name(domain: str, company_name: str) -> bool:
    """Il dominio è plausibilmente dell'azienda? Almeno uno dei top-3 token."""
    tokens = _name_tokens(company_name)
    if not tokens:
        return False
    base = domain.lower().split(":")[0].split("/")[0]
    return any(t in base for t in tokens[:3])


def _fetch_targets(sb: Client, limit: int, offset: int) -> list[CompanyRow]:
    resp = (
        sb.table("companies")
        .select("id, foretagsnamn, organisationsnummer, stad, domain, "
                "reception_telefon, email_info")
        .eq("arkiverad", False)
        .order("foretagsnamn")
        .range(offset, offset + max(limit * 4, 100) - 1)
        .execute()
    )
    rows: list[CompanyRow] = []
    for r in resp.data:
        domain = (r.get("domain") or "").strip()
        tel = (r.get("reception_telefon") or "").strip()
        em = (r.get("email_info") or "").strip()
        if domain and tel and em:
            continue
        rows.append(CompanyRow(
            id=r["id"],
            foretagsnamn=r["foretagsnamn"],
            organisationsnummer=r.get("organisationsnummer") or "",
            stad=r.get("stad") or "",
            domain=domain,
            reception_telefon=tel,
            email_info=em,
        ))
        if len(rows) >= limit:
            break
    return rows


async def _extract_email_from_homepage(domain: str) -> str | None:
    """Fetch homepage + /kontakt e cerca info@<domain>."""
    for path in ("/", "/kontakt/", "/contact/", "/om-oss/"):
        url = f"https://{domain}{path}"
        try:
            res = await fetch_and_extract(url, timeout=15.0)
        except Exception:
            continue
        if not res.ok or not res.content_text:
            continue
        for em in _EMAIL_RE.findall(res.content_text):
            local, _, host = em.lower().partition("@")
            if host != domain.lower():
                continue
            if local in _GENERIC_LOCALS:
                return em.lower()
        # Fallback: prima email sul dominio anche se non generica
        for em in _EMAIL_RE.findall(res.content_text):
            local, _, host = em.lower().partition("@")
            if host == domain.lower():
                return em.lower()
        await asyncio.sleep(0.2)
    return None


async def _enrich_one(c: CompanyRow) -> dict[str, Any]:
    """Returns {domain, reception_telefon, email_info, source_url, errors}."""
    out: dict[str, Any] = {
        "domain": None,
        "reception_telefon": None,
        "email_info": None,
        "source_url": None,
        "engines": [],
        "errors": {},
    }

    lookup = await unified_company_lookup(
        c.foretagsnamn,
        stad=c.stad,
        use_maps=True,
        use_aio=False,
    )
    out["engines"] = lookup.engines_used
    out["errors"] = lookup.errors

    # 1) Scegli candidato domain valido
    if not c.domain:
        for cand in lookup.candidate_domains:
            if _domain_matches_name(cand, c.foretagsnamn):
                out["domain"] = cand
                out["source_url"] = f"https://{cand}"
                break

    # 2) Phone da Maps
    if not c.reception_telefon and lookup.maps_place and lookup.maps_place.phone:
        out["reception_telefon"] = lookup.maps_place.phone
        if not out["source_url"]:
            out["source_url"] = lookup.maps_place.source_url

    # 3) Email — solo se abbiamo un domain (nuovo o esistente)
    target_domain = out["domain"] or c.domain
    if target_domain and not c.email_info:
        em = await _extract_email_from_homepage(target_domain)
        if em:
            out["email_info"] = em

    return out


def _persist(sb: Client, c: CompanyRow, enr: dict[str, Any]) -> int:
    update: dict[str, Any] = {}
    if enr["domain"] and not c.domain:
        update["domain"] = enr["domain"]
    if enr["reception_telefon"] and not c.reception_telefon:
        update["reception_telefon"] = enr["reception_telefon"]
    if enr["email_info"] and not c.email_info:
        update["email_info"] = enr["email_info"]
    if not update:
        return 0
    sb.table("companies").update(update).eq("id", c.id).execute()
    for field_name in update:
        sb.table("sources").insert({
            "company_id": c.id,
            "field_name": f"companies.{field_name}",
            "source_url": enr["source_url"],
            "scraper_tier": 1,
            "raw_excerpt": (
                f"multi-engine ({','.join(enr['engines'])}); "
                f"value={update[field_name]}"
            )[:500],
            "critic_note": (
                "enrich_v2_unified.py — Brave+Ecosia+Bing+SearXNG+Maps"
            ),
        }).execute()
    return len(update)


async def _worker(
    name: str,
    queue: asyncio.Queue[CompanyRow],
    sb: Client,
    dry_run: bool,
    stats: dict[str, int],
) -> None:
    while True:
        try:
            c = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            enr = await _enrich_one(c)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red][{name}] ERR {c.foretagsnamn}: {exc}[/]")
            stats["errors"] += 1
            queue.task_done()
            continue
        if any((enr["domain"], enr["reception_telefon"], enr["email_info"])):
            stats["enriched"] += 1
            tag_d = "D" if enr["domain"] else "-"
            tag_t = "T" if enr["reception_telefon"] else "-"
            tag_e = "E" if enr["email_info"] else "-"
            eng = ",".join(enr["engines"])[:30]
            console.print(
                f"[green][{name}] OK {c.foretagsnamn[:34]:34} "
                f"→ {tag_d}{tag_t}{tag_e} [{eng}][/]"
            )
            if not dry_run:
                stats["fields_updated"] += _persist(sb, c, enr)
        else:
            stats["empty"] += 1
            console.print(
                f"[dim][{name}] -- {c.foretagsnamn[:34]:34}  (no evidence)[/]"
            )
        queue.task_done()


async def main(limit: int, offset: int, workers: int, dry_run: bool) -> None:
    sb = _supabase()
    targets = _fetch_targets(sb, limit=limit, offset=offset)
    console.print(
        f"[bold cyan]Targets: {len(targets)} (limit={limit} offset={offset} "
        f"workers={workers} dry_run={dry_run})[/]"
    )
    if not targets:
        return
    queue: asyncio.Queue[CompanyRow] = asyncio.Queue()
    for c in targets:
        queue.put_nowait(c)
    stats = {"enriched": 0, "empty": 0, "errors": 0, "fields_updated": 0}
    tasks = [
        asyncio.create_task(_worker(f"w{i+1}", queue, sb, dry_run, stats))
        for i in range(workers)
    ]
    await asyncio.gather(*tasks)
    table = Table(title="enrich_v2_unified summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    for k, v in stats.items():
        table.add_row(k, str(v))
    console.print(table)


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.limit, args.offset, args.workers, args.dry_run))


if __name__ == "__main__":
    _cli()
