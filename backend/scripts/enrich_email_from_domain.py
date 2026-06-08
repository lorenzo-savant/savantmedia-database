"""
Chirurgico: estrai `email_info` aziendale dalle homepages che già conoscono.

Mentre opencode lavora sulle 158 aziende SENZA domain (hit rate ~16%), questo
script processa il complementare: 324 aziende **CON** domain ma senza
`email_info`. Hit rate atteso 50-70% (l'email info@/kontakt@ è quasi sempre
sulla homepage o sulla /kontakt/).

Pipeline per azienda:
1. Fetch `https://<domain>/` (T2)
2. Se 0 email plausibili → prova `/kontakt/`, `/contact/`, `/om-oss/`
3. Scegli il match migliore:
   - priorità a `info@<domain>` / `kontakt@<domain>`
   - fallback a prima email su `<domain>` (anche non-generica)
4. UPDATE `companies.email_info` + audit `sources` (tier=2)

NIENTE parsing DM contacts. Solo `email_info`.

Idempotente: skip aziende che hanno già `email_info`.

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_email_from_domain --limit 50
    .venv/Scripts/python.exe -m scripts.enrich_email_from_domain --workers 4 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass

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
_PREFERRED_LOCALS = ("info", "kontakt", "contact", "hello", "hej", "post", "office")


@dataclass
class CompanyRow:
    id: str
    foretagsnamn: str
    domain: str


def _supabase() -> Client:
    load_dotenv()
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    load_dotenv(
        dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env.local")
    )
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        console.print("[red]Mancano NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SECRET_KEY[/]")
        raise SystemExit(1)
    return create_client(url, key)


def _fetch_targets(sb: Client, limit: int) -> list[CompanyRow]:
    """Aziende WITH domain BUT WITHOUT email_info."""
    # Over-fetch per dare margine se molti hanno domain vuoto o '/'
    resp = (
        sb.table("companies")
        .select("id, foretagsnamn, domain, email_info")
        .eq("arkiverad", False)
        .or_("email_info.is.null,email_info.eq.")
        .order("foretagsnamn")
        .limit(limit * 3)
        .execute()
    )
    rows: list[CompanyRow] = []
    for r in resp.data:
        dom = (r.get("domain") or "").strip().lower()
        if not dom:
            continue
        rows.append(CompanyRow(
            id=r["id"],
            foretagsnamn=r["foretagsnamn"],
            domain=dom,
        ))
        if len(rows) >= limit:
            break
    return rows


def _pick_email(text: str, domain: str) -> str | None:
    emails = list(dict.fromkeys(_EMAIL_RE.findall(text)))
    if not emails:
        return None
    # Round 1: preferred local @ domain
    for em in emails:
        local, _, host = em.lower().partition("@")
        if host == domain and local in _PREFERRED_LOCALS:
            return em.lower()
    # Round 2: any email on the same domain
    for em in emails:
        _, _, host = em.lower().partition("@")
        if host == domain:
            return em.lower()
    # Round 3: subdomain ok (es. mail.foretag.se)
    for em in emails:
        _, _, host = em.lower().partition("@")
        if host.endswith("." + domain):
            return em.lower()
    return None


async def _scrape_one(c: CompanyRow) -> tuple[str | None, str | None]:
    """Returns (email, source_url) or (None, None)."""
    paths = ["/", "/kontakt/", "/contact/", "/om-oss/", "/about/", "/about-us/"]
    for path in paths:
        url = f"https://{c.domain}{path}"
        try:
            res = await fetch_and_extract(url, timeout=15.0)
        except Exception:
            continue
        if not res.ok or not res.content_text:
            continue
        em = _pick_email(res.content_text, c.domain)
        if em:
            return em, url
        await asyncio.sleep(0.15)
    return None, None


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
            email, source_url = await _scrape_one(c)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red][{name}] ERR {c.foretagsnamn}: {exc}[/]")
            stats["errors"] += 1
            queue.task_done()
            continue
        if email:
            stats["found"] += 1
            console.print(
                f"[green][{name}] OK {c.foretagsnamn[:34]:34} → {email}[/]"
            )
            if not dry_run:
                sb.table("companies").update(
                    {"email_info": email}
                ).eq("id", c.id).execute()
                sb.table("sources").insert({
                    "company_id": c.id,
                    "field_name": "companies.email_info",
                    "source_url": source_url,
                    "scraper_tier": 2,
                    "raw_excerpt": f"matched: {email}",
                    "critic_note": (
                        "enrich_email_from_domain.py — homepage/kontakt scrape"
                    ),
                }).execute()
                stats["updated"] += 1
        else:
            stats["empty"] += 1
            console.print(
                f"[dim][{name}] -- {c.foretagsnamn[:34]:34}  (no email found)[/]"
            )
        queue.task_done()


async def main(limit: int, workers: int, dry_run: bool) -> None:
    sb = _supabase()
    targets = _fetch_targets(sb, limit=limit)
    console.print(
        f"[bold cyan]Targets (with domain, missing email): "
        f"{len(targets)} workers={workers} dry={dry_run}[/]"
    )
    if not targets:
        return
    queue: asyncio.Queue[CompanyRow] = asyncio.Queue()
    for c in targets:
        queue.put_nowait(c)
    stats = {"found": 0, "empty": 0, "errors": 0, "updated": 0}
    tasks = [
        asyncio.create_task(_worker(f"w{i+1}", queue, sb, dry_run, stats))
        for i in range(workers)
    ]
    await asyncio.gather(*tasks)
    table = Table(title="Email enrichment summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k, v in stats.items():
        table.add_row(k, str(v))
    table.add_row("Hit rate", f"{100*stats['found']/max(len(targets),1):.1f}%")
    console.print(table)


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.limit, args.workers, args.dry_run))


if __name__ == "__main__":
    _cli()
