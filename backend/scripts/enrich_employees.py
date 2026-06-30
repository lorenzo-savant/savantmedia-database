"""
Arricchimento `antal_anstallda` cost-zero via SNIPPET dei motori (Brave/Ecosia/
Bing). Il numero di dipendenti delle aziende svedesi è quasi sempre già
nello snippet SERP (allabolag/proff/ratsit indicizzati): "45 anställda",
"Antal anställda: 12", "20-49 anställda". NON scrapiamo le pagine /foretag/ di
allabolag (SPA + anti-bot — lezione vault), solo gli snippet.

Per ogni range ("20-49 anställda") salviamo il LIMITE INFERIORE (conservativo).
Aggiorna `antal_anstallda` + `storlek_kategori`. Idempotente (solo se vuoto).

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_employees --recent-hours 6 --limit 500 --dry-run
    .venv/Scripts/python.exe -m scripts.enrich_employees --recent-hours 6 --limit 500 --workers 3
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

from scrapers.multi_search import BraveClient, EcosiaClient, BingClient

console = Console()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_NBSP = " "
# "20-49 anställda" → lower bound 20
_RANGE = re.compile(r"(\d{1,5})\s*[-–—]\s*\d{1,5}\s*anst\w*", re.I)
# "Antal anställda: 45" / "antal anställda 45"
_LABELLED = re.compile(r"antal\s+anst\w*\D{0,12}(\d[\d ]{0,6}\d|\d)", re.I)
# "45 anställda"
_TRAILING = re.compile(r"(\d[\d ]{0,6}\d|\d)\s*anst\w*", re.I)


@dataclass
class Row:
    id: str
    foretagsnamn: str
    organisationsnummer: str
    stad: str


def _sb() -> Client:
    load_dotenv(os.path.join(ROOT, "..", ".env"))
    load_dotenv(os.path.join(ROOT, "..", ".env.local"))
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    return create_client(url, os.environ["SUPABASE_SECRET_KEY"])


def _classify(n: int | None) -> str | None:
    if n is None or n < 0:
        return None
    return "liten" if n <= 49 else "medel" if n <= 249 else "multinationell"


def _to_int(s: str) -> int | None:
    try:
        n = int(s.replace(_NBSP, "").replace(" ", ""))
    except ValueError:
        return None
    return n if 0 < n < 500_000 else None


def extract_employees(text: str, name: str) -> int | None:
    """Estrai n. dipendenti dallo snippet. Richiede che un token del nome sia
    presente (riduce i falsi positivi da aziende diverse nello snippet)."""
    if not text:
        return None
    t = text.replace(_NBSP, " ")
    low = t.lower()
    toks = [w for w in re.findall(r"[a-zåäö0-9]+", name.lower())
            if len(w) >= 3 and w not in ("aktiebolag", "bolag")]
    if toks and not any(tok in low for tok in toks[:3]):
        return None
    m = _RANGE.search(t)
    if m:
        return _to_int(m.group(1))
    for rx in (_LABELLED, _TRAILING):
        for m in rx.finditer(t):
            n = _to_int(m.group(1))
            if n is not None:
                return n
    return None


def _fetch_targets(sb: Client, limit: int, recent_hours: float | None) -> list[Row]:
    q = (sb.table("companies")
         .select("id, foretagsnamn, organisationsnummer, stad, antal_anstallda")
         .eq("arkiverad", False))
    if recent_hours:
        cutoff = (datetime.now(timezone.utc)
                  - __import__("datetime").timedelta(hours=recent_hours)).isoformat()
        q = q.gte("skapad_datum", cutoff)
    resp = q.order("foretagsnamn").range(0, max(limit * 4, 100) - 1).execute()
    rows: list[Row] = []
    for r in resp.data:
        if r.get("antal_anstallda") is not None:
            continue
        rows.append(Row(id=r["id"], foretagsnamn=r["foretagsnamn"],
                        organisationsnummer=r.get("organisationsnummer") or "",
                        stad=r.get("stad") or ""))
        if len(rows) >= limit:
            break
    return rows


async def _lookup(clients, row: Row) -> int | None:
    queries = [f'"{row.foretagsnamn}" anställda',
               f'{row.foretagsnamn} {row.stad} antal anställda']
    for q in queries:
        for cli in clients:
            try:
                results = await cli.search(q, limit=6)
            except Exception:
                continue
            for r in results:
                if not getattr(r, "ok", False):
                    continue
                blob = f"{getattr(r,'title','')} {getattr(r,'content_text','') or ''}"
                n = extract_employees(blob, row.foretagsnamn)
                if n is not None:
                    return n
        await asyncio.sleep(0.1)
    return None


async def _worker(name, queue, clients, sb, dry_run, stats):
    while True:
        try:
            row: Row = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            n = await _lookup(clients, row)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red][{name}] {row.foretagsnamn[:28]}: {exc}[/]")
            stats["errors"] += 1
            queue.task_done()
            continue
        if n is not None:
            stats["found"] += 1
            mark = ">20" if n >= 20 else "≤20"
            console.print(f"[green][{name}] {row.foretagsnamn[:30]:30} → "
                          f"{n} anställda ({mark})[/]")
            if not dry_run:
                try:
                    sb.table("companies").update(
                        {"antal_anstallda": n, "storlek_kategori": _classify(n)}
                    ).eq("id", row.id).execute()
                    sb.table("sources").insert({
                        "company_id": row.id,
                        "field_name": "companies.antal_anstallda",
                        "scraper_tier": 1,
                        "raw_excerpt": f"SERP snippet: {n} anställda"[:500],
                        "critic_note": "enrich_employees.py — Brave/Ecosia/Bing snippet",
                    }).execute()
                    stats["updated"] += 1
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]update {row.foretagsnamn[:24]}: {exc}[/]")
                    stats["errors"] += 1
        else:
            stats["empty"] += 1
        queue.task_done()


async def main(limit: int, workers: int, dry_run: bool,
               recent_hours: float | None) -> None:
    sb = _sb()
    targets = _fetch_targets(sb, limit, recent_hours)
    console.print(f"[bold cyan]Targets (no antal_anstallda): {len(targets)} "
                  f"recent_hours={recent_hours} dry_run={dry_run}[/]")
    if not targets:
        return
    clients = [BraveClient(), EcosiaClient(), BingClient()]
    queue: asyncio.Queue[Row] = asyncio.Queue()
    for t in targets:
        queue.put_nowait(t)
    stats = {"found": 0, "updated": 0, "empty": 0, "errors": 0, "gt20": 0}
    await asyncio.gather(*[
        asyncio.create_task(_worker(f"w{i+1}", queue, clients, sb, dry_run, stats))
        for i in range(workers)
    ])
    table = Table(title="enrich_employees (SERP snippet)")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k, v in stats.items():
        table.add_row(k, str(v))
    table.add_row("hit rate", f"{100*stats['found']/max(len(targets),1):.1f}%")
    console.print(table)
    if dry_run:
        console.print("[bold yellow]DRY-RUN — no DB writes[/]")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--recent-hours", type=float, default=None)
    a = p.parse_args()
    asyncio.run(main(a.limit, a.workers, a.dry_run, a.recent_hours))
