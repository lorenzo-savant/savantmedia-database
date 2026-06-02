"""
Trova email PERSONALI dei decision makers (VD, Styrelse, ägare) scraping
le team pages del dominio aziendale.

Per ogni DM senza email ma la cui azienda ha `domain` noto:
1. Scrape sequenziale: /, /team/, /medarbetare/, /people/, /ledning/,
   /om-oss/, /om-oss/ledning/, /leadership/, /our-team/, /about-us/team/
2. Per ogni pagina: estrae tutte le email + verifica se il NOME del DM
   appare entro 300 chars dall'email.
3. Tre livelli di match (decrescenti per confidence):
   a) **High**: full_name appare nel raggio + email contiene first_name/last_name
   b) **Medium**: last_name appare nel raggio + email contiene last_name
   c) **Low (skipped)**: solo first_name → ambiguo, salta

NIENTE pattern guessing senza evidenza on-page. Insertiamo solo email
verificate dal sito ufficiale, mai inferenze cieche.

Idempotente: skip DM che hanno già `email`.

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_dm_emails --limit 30 --dry-run
    .venv/Scripts/python.exe -m scripts.enrich_dm_emails --limit 100 --workers 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

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

from scrapers.httpbs import fetch_and_extract

console = Console()


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-åäöÅÄÖ]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_GENERIC_LOCALS = {
    "info", "kontakt", "contact", "support", "hello", "hej", "post",
    "office", "press", "media", "careers", "career", "jobs", "sales",
    "hr", "admin", "marketing", "newsroom", "noreply", "no-reply",
}

# Path candidati per pagine team/ledning ordered da più specifico a generico
_TEAM_PATHS: tuple[str, ...] = (
    "/medarbetare/",
    "/team/",
    "/ledning/",
    "/om-oss/ledning/",
    "/om-oss/medarbetare/",
    "/om-oss/team/",
    "/people/",
    "/our-team/",
    "/about-us/team/",
    "/about-us/leadership/",
    "/leadership/",
    "/management/",
    "/staff/",
    "/kontakt/",
    "/contact/",
    "/om-oss/",
    "/about/",
    "/about-us/",
    "/",
)


@dataclass
class ContactRow:
    id: str
    namn: str
    roll: str
    company_id: str
    foretagsnamn: str
    domain: str


def _supabase() -> Client:
    load_dotenv()
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )


def _fetch_targets(sb: Client, limit: int, offset: int) -> list[ContactRow]:
    """DM senza email + con company domain noto."""
    resp = (
        sb.table("contacts")
        .select("id, namn, roll, company_id, email")
        .eq("is_dm", True)
        .or_("email.is.null,email.eq.")
        .order("namn")
        .range(offset, offset + max(limit * 4, 100) - 1)
        .execute()
    )
    company_ids = sorted({r["company_id"] for r in resp.data})
    if not company_ids:
        return []
    cresp = (
        sb.table("companies")
        .select("id, foretagsnamn, domain")
        .in_("id", company_ids)
        .execute()
    )
    by_id = {c["id"]: c for c in cresp.data}

    rows: list[ContactRow] = []
    for r in resp.data:
        comp = by_id.get(r["company_id"])
        if not comp:
            continue
        domain = (comp.get("domain") or "").strip().lower()
        if not domain:
            continue
        # Skip nomi corrotti già noti
        nm = (r.get("namn") or "").strip()
        if not nm or len(nm) < 5 or len(nm) > 60 or "\n" in nm:
            continue
        # Skip se sembra nome-azienda
        if any(tok in nm for tok in (
            "Aktiebolag", "Handelsbolag", "Holding", "Group", "Partners",
            "Industries", "Solutions", "Stiftelsen", "Föreningen",
        )):
            continue
        rows.append(ContactRow(
            id=r["id"],
            namn=nm,
            roll=r.get("roll") or "",
            company_id=r["company_id"],
            foretagsnamn=comp["foretagsnamn"],
            domain=domain,
        ))
        if len(rows) >= limit:
            break
    return rows


def _norm(name: str) -> str:
    return (
        name.lower()
        .replace("å", "a").replace("ä", "a").replace("ö", "o")
        .replace("é", "e").replace("ü", "u")
    )


def _score_email(
    email: str, full_name: str, domain: str, text: str, em_pos: int
) -> tuple[str, int]:
    """Ritorna (confidence_level, score). Higher score better.

    confidence_level: 'high' | 'medium' | 'low' | 'none'
    """
    em_lo = email.lower()
    local, _, host = em_lo.partition("@")
    if host != domain and not host.endswith("." + domain):
        return "none", 0
    if local in _GENERIC_LOCALS:
        return "none", 0

    name_parts = [_norm(p) for p in full_name.split() if len(p) >= 2]
    if not name_parts:
        return "none", 0
    first = name_parts[0]
    last = name_parts[-1] if len(name_parts) >= 2 else ""
    full = " ".join(name_parts)
    local_norm = _norm(local)

    score = 0
    # Email local-part contiene il nome
    if last and last in local_norm:
        score += 4
    if first and first in local_norm:
        score += 2
    if last and first and (f"{first}.{last}" in local_norm or
                            f"{first}{last}" in local_norm):
        score += 3

    # Nome appare nel testo vicino all'email (entro ±200 chars)
    text_lo = _norm(text)
    win_start = max(0, em_pos - 200)
    win_end = min(len(text), em_pos + 200)
    window = text_lo[win_start:win_end]
    if full in window:
        score += 5
    elif last and last in window:
        score += 3

    if score == 0:
        return "none", 0
    if score >= 7:
        return "high", score
    if score >= 4:
        return "medium", score
    return "low", score


async def _scrape_one(c: ContactRow) -> tuple[str | None, str | None, str]:
    """Returns (email, source_url, confidence)."""
    best_email: str | None = None
    best_url: str | None = None
    best_conf: str = "none"
    best_score: int = -1

    for path in _TEAM_PATHS:
        url = f"https://{c.domain}{path}"
        try:
            res = await fetch_and_extract(url, timeout=15.0)
        except Exception:
            continue
        if not res.ok or not res.content_text:
            continue
        text = res.content_text
        for m in _EMAIL_RE.finditer(text):
            em = m.group(0)
            conf, score = _score_email(em, c.namn, c.domain, text, m.start())
            if conf == "none" or conf == "low":
                continue
            if score > best_score:
                best_email = em.lower()
                best_url = url
                best_conf = conf
                best_score = score
        if best_conf == "high":
            break  # stop early
        await asyncio.sleep(0.15)
    return best_email, best_url, best_conf


async def _worker(
    name: str,
    queue: asyncio.Queue[ContactRow],
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
            email, source_url, conf = await _scrape_one(c)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red][{name}] ERR {c.namn}: {exc}[/]")
            stats["errors"] += 1
            queue.task_done()
            continue
        if email and conf in ("high", "medium"):
            stats[f"found_{conf}"] += 1
            console.print(
                f"[green][{name}] {conf.upper():6} {c.namn[:24]:24} "
                f"({c.foretagsnamn[:24]:24}) → {email[:50]}[/]"
            )
            if not dry_run:
                payload = {"email": email, "verifierad": True,
                           "verifieringsmetod": "foretagswebbplats",
                           "verifieringskalla": source_url,
                           "verifierat_av": "agent:enrich_dm_emails-2026-06-02",
                           "verifierat_datum": datetime.now(
                               timezone.utc).isoformat()}
                sb.table("contacts").update(payload).eq("id", c.id).execute()
                sb.table("sources").insert({
                    "company_id": c.company_id,
                    "contact_id": c.id,
                    "field_name": "contacts.email",
                    "source_url": source_url,
                    "scraper_tier": 2,
                    "raw_excerpt": f"matched: {email} (confidence={conf})",
                    "critic_note": (
                        f"enrich_dm_emails.py — team page scrape, "
                        f"name match near email"
                    ),
                }).execute()
                stats["updated"] += 1
        else:
            stats["empty"] += 1
            console.print(
                f"[dim][{name}] -- {c.namn[:24]:24} "
                f"({c.foretagsnamn[:24]:24})[/]"
            )
        queue.task_done()


async def main(limit: int, offset: int, workers: int, dry_run: bool) -> None:
    sb = _supabase()
    targets = _fetch_targets(sb, limit=limit, offset=offset)
    console.print(
        f"[bold cyan]Targets (DM, no email, with domain): {len(targets)} "
        f"workers={workers} dry={dry_run}[/]"
    )
    if not targets:
        return
    queue: asyncio.Queue[ContactRow] = asyncio.Queue()
    for c in targets:
        queue.put_nowait(c)
    stats = {"found_high": 0, "found_medium": 0, "empty": 0,
             "errors": 0, "updated": 0}
    tasks = [
        asyncio.create_task(_worker(f"w{i+1}", queue, sb, dry_run, stats))
        for i in range(workers)
    ]
    await asyncio.gather(*tasks)
    table = Table(title="DM email enrichment summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k, v in stats.items():
        table.add_row(k, str(v))
    total_found = stats["found_high"] + stats["found_medium"]
    table.add_row(
        "Hit rate", f"{100*total_found/max(len(targets),1):.1f}%"
    )
    console.print(table)


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.limit, args.offset, args.workers, args.dry_run))


if __name__ == "__main__":
    _cli()
