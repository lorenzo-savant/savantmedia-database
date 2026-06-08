"""
Prova la NUOVA ricerca email (SERP dork + fetch pagina + de-offuscazione + ranking).

Read-only: NON scrive nel DB. Serve solo a vedere cosa trova la nuova
`scrapers.email_search.find_emails_on_domain` sulle prime N aziende con domain.

Pipeline per azienda (tutta la catena nuova):
1. dork SERP `"@domain"` / `site:domain ...` su Brave+Ecosia+Bing
2. estrazione email dagli snippet (con de-offuscazione: snabel-a/punkt/[at]/[dot])
3. escalation: scarica le pagine on-domain / kontakt / medarbetare ed estrae dal
   testo completo (qui sta il grosso del recall)
4. ranking personale-first via `rank_domain_emails` (info@/kontakt@ in fondo)

Usage (da backend/):
    python -m scripts.test_email_search --limit 50
    python -m scripts.test_email_search --limit 10 --only-missing --workers 4
    python -m scripts.test_email_search --limit 5 --no-pages   # solo snippet, veloce

Env richieste (lette da .env / .env.local in root):
    NEXT_PUBLIC_SUPABASE_URL  (o SUPABASE_URL)
    SUPABASE_SECRET_KEY
"""

from __future__ import annotations

import argparse
import asyncio
import os
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

from scrapers.email_search import find_emails_on_domain, rank_domain_emails

console = Console()


@dataclass
class CompanyRow:
    id: str
    foretagsnamn: str
    domain: str
    email_info: str


def _supabase() -> Client:
    # Cerca la root del repo (dove stanno .env / .env.local) salendo da backend/.
    load_dotenv()
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    load_dotenv(
        dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env.local")
    )
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        console.print(
            "[red]Mancano NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SECRET_KEY "
            "in .env o .env.local[/]"
        )
        raise SystemExit(1)
    return create_client(url, key)


def _normalize_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    d = d.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return d.split("/", 1)[0].split("?", 1)[0].strip(".")


def _fetch_targets(sb: Client, limit: int, only_missing: bool) -> list[CompanyRow]:
    """Prime `limit` aziende con domain (opzionalmente solo senza email_info)."""
    q = (
        sb.table("companies")
        .select("id, foretagsnamn, domain, email_info")
        .eq("arkiverad", False)
        .order("foretagsnamn")
        .limit(limit * 4)  # over-fetch: molti hanno domain vuoto
    )
    if only_missing:
        q = q.or_("email_info.is.null,email_info.eq.")
    resp = q.execute()

    rows: list[CompanyRow] = []
    for r in resp.data:
        dom = _normalize_domain(r.get("domain") or "")
        if not dom:
            continue
        rows.append(
            CompanyRow(
                id=r["id"],
                foretagsnamn=r["foretagsnamn"],
                domain=dom,
                email_info=(r.get("email_info") or ""),
            )
        )
        if len(rows) >= limit:
            break
    return rows


async def _probe_one(c: CompanyRow, fetch_pages: bool) -> list[tuple[str, float]]:
    try:
        emails = await find_emails_on_domain(c.domain, fetch_pages=fetch_pages)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]ERR {c.foretagsnamn}: {exc}[/]")
        return []
    return rank_domain_emails(emails, c.domain)


async def _worker(
    name: str,
    queue: "asyncio.Queue[CompanyRow]",
    fetch_pages: bool,
    stats: dict[str, int],
) -> None:
    while True:
        try:
            c = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        ranked = await _probe_one(c, fetch_pages)
        if ranked:
            stats["found"] += 1
            top = ranked[0][0]
            extra = f" (+{len(ranked) - 1})" if len(ranked) > 1 else ""
            already = "  [dim](redan i DB)[/]" if c.email_info else ""
            console.print(
                f"[green][{name}] OK[/] {c.foretagsnamn[:30]:30} "
                f"[cyan]{c.domain:24}[/] → {top}{extra}{already}"
            )
        else:
            stats["empty"] += 1
            console.print(
                f"[dim][{name}] -- {c.foretagsnamn[:30]:30} {c.domain:24} (inga)[/]"
            )
        queue.task_done()


async def main(limit: int, workers: int, only_missing: bool, fetch_pages: bool) -> None:
    sb = _supabase()
    targets = _fetch_targets(sb, limit=limit, only_missing=only_missing)
    console.print(
        f"[bold cyan]Provo ricerca email su {len(targets)} aziende[/] "
        f"(workers={workers}, fetch_pages={fetch_pages}, only_missing={only_missing})\n"
    )
    if not targets:
        console.print("[yellow]Nessun target con domain trovato.[/]")
        return

    queue: "asyncio.Queue[CompanyRow]" = asyncio.Queue()
    for c in targets:
        queue.put_nowait(c)
    stats = {"found": 0, "empty": 0}
    await asyncio.gather(
        *[
            asyncio.create_task(_worker(f"w{i+1}", queue, fetch_pages, stats))
            for i in range(workers)
        ]
    )

    table = Table(title="Riepilogo ricerca email (read-only)")
    table.add_column("Metrica", style="cyan")
    table.add_column("Valore", justify="right", style="green")
    table.add_row("Aziende provate", str(len(targets)))
    table.add_row("Con almeno 1 email", str(stats["found"]))
    table.add_row("Senza risultati", str(stats["empty"]))
    table.add_row("Hit rate", f"{100 * stats['found'] / max(len(targets), 1):.1f}%")
    console.print(table)


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=50, help="Numero di aziende (default 50)")
    p.add_argument("--workers", type=int, default=3, help="Concorrenza (default 3)")
    p.add_argument(
        "--only-missing",
        action="store_true",
        help="Solo aziende senza email_info nel DB",
    )
    p.add_argument(
        "--no-pages",
        action="store_true",
        help="Disabilita l'escalation fetch-pagina (solo snippet SERP, più veloce)",
    )
    args = p.parse_args()
    asyncio.run(
        main(
            limit=args.limit,
            workers=args.workers,
            only_missing=args.only_missing,
            fetch_pages=not args.no_pages,
        )
    )


if __name__ == "__main__":
    _cli()
