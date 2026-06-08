"""
Rimuove i contatti-spazzatura dalla tabella `contacts`.

Criterio (conservativo): il `namn` NON supera `is_probable_person_name`
(menu, titoli pagina, nomi-azienda, ruoli, geografia) **E** non ha email.
Mai cancella un contatto che ha già un'email (anche se il nome è dubbio).

Default = dry-run (mostra cosa cancellerebbe). Con `--apply` cancella davvero.

Usage (da backend/):
    python -m scripts.cleanup_junk_contacts            # dry-run
    python -m scripts.cleanup_junk_contacts --apply    # cancella
"""

from __future__ import annotations

import argparse
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from dotenv import load_dotenv
from rich.console import Console
from supabase import Client, create_client

from pipeline._extract_emails import is_probable_person_name

console = Console()


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


def _all_contacts(sb: Client) -> list[dict]:
    """Pagina tutta la tabella contacts (id, namn, email)."""
    rows: list[dict] = []
    page = 0
    size = 1000
    while True:
        resp = (
            sb.table("contacts")
            .select("id, namn, roll, email")
            .order("namn")
            .range(page * size, page * size + size - 1)
            .execute()
        )
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < size:
            break
        page += 1
    return rows


def _is_junk(row: dict) -> bool:
    email = (row.get("email") or "").strip()
    if email:
        return False  # mai cancellare un contatto con email
    return not is_probable_person_name((row.get("namn") or "").strip())


def main(apply: bool) -> None:
    sb = _supabase()
    rows = _all_contacts(sb)
    junk = [r for r in rows if _is_junk(r)]

    console.print(
        f"[bold cyan]Contatti totali:[/] {len(rows)} · "
        f"[bold red]junk (nome non-persona + senza email):[/] {len(junk)}\n"
    )
    for r in junk[:40]:
        console.print(
            f"  [red]✗[/] {repr(r.get('namn'))[:40]:40} "
            f"roll={r.get('roll') or '-'}"
        )
    if len(junk) > 40:
        console.print(f"  … e altri {len(junk) - 40}")

    if not junk:
        console.print("\n[green]Nessun junk da rimuovere.[/]")
        return

    if not apply:
        console.print(
            "\n[yellow]DRY-RUN — niente cancellato. "
            "Rilancia con --apply per rimuovere.[/]"
        )
        return

    # Cancella in batch per id. Prima rimuove eventuali righe `sources`
    # collegate (FK contact_id) per non violare i vincoli.
    ids = [r["id"] for r in junk]
    deleted = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        try:
            sb.table("sources").delete().in_("contact_id", chunk).execute()
        except Exception as exc:  # noqa: BLE001 — sources potrebbe non avere righe
            console.print(f"[dim]sources cleanup: {exc}[/]")
        try:
            sb.table("contacts").delete().in_("id", chunk).execute()
            deleted += len(chunk)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]delete batch failed: {exc}[/]")
    console.print(f"\n[bold green]Cancellati {deleted} contatti junk.[/]")


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="Cancella davvero (default: dry-run)")
    args = p.parse_args()
    main(apply=args.apply)


if __name__ == "__main__":
    _cli()
