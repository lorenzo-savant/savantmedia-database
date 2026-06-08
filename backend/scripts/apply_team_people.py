"""
Scrive nei `contacts` le persone pubbliche raccolte dal workflow team-pages.

Legge il JSON di risultato del workflow (result.companies[].people[]), ri-pulisce
i nomi col trim-titoli esteso di `harvest_team_pages._clean_name`, deduplica
contro i contatti esistenti (per azienda) e dentro il batch, inserisce in
`contacts` + audit in `sources` (tier 2). NON ri-scansiona nulla.

Usage (da backend/):
    python -m scripts.apply_team_people <output.json>            # dry-run (conta)
    python -m scripts.apply_team_people <output.json> --apply     # scrive
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from rich.console import Console
from rich.table import Table

from scripts.harvest_team_pages import _clean_name, _norm, _supabase

console = Console()


def _load_companies(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    res = data.get("result") if isinstance(data, dict) else None
    if isinstance(res, dict) and "companies" in res:
        return res.get("companies") or []
    return (data.get("companies") if isinstance(data, dict) else None) or []


def main(path: str, apply: bool) -> None:
    sb = _supabase()
    companies = _load_companies(path)
    console.print(
        f"[bold cyan]Apply team people — {len(companies)} aziende dal workflow "
        f"(apply={apply})[/]\n"
    )
    if not companies:
        console.print("[yellow]Nessuna azienda nel file di risultato.[/]")
        return

    # Nomi-contatto esistenti per azienda (dedup).
    ids = [c["company_id"] for c in companies if c.get("company_id")]
    existing: dict[str, set[str]] = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        r = sb.table("contacts").select("company_id, namn").in_("company_id", chunk).execute()
        for e in r.data or []:
            existing.setdefault(e["company_id"], set()).add(_norm(e.get("namn") or ""))

    inserted = skipped_dup = skipped_clean = errors = 0
    seen_batch: set[tuple[str, str]] = set()

    for c in companies:
        cid = c.get("company_id")
        if not cid:
            continue
        for p in c.get("people", []):
            namn = _clean_name(p.get("namn") or "")
            if not namn:
                skipped_clean += 1
                continue
            key = (cid, _norm(namn))
            if _norm(namn) in existing.get(cid, set()) or key in seen_batch:
                skipped_dup += 1
                continue
            seen_batch.add(key)

            if not apply:
                inserted += 1
                continue

            email = (p.get("email") or "").strip()
            has_email = bool(email)
            source_url = p.get("source_url") or f"https://{c.get('domain', '')}"
            try:
                ins = (
                    sb.table("contacts")
                    .insert(
                        {
                            "company_id": cid,
                            "namn": namn,
                            "roll": p.get("roll") or "",
                            "email": email,
                            "telefon": p.get("telefon") or "",
                            "linkedin_url": p.get("linkedin") or "",
                            "verifierad": has_email,
                            "verifieringsmetod": "foretagswebbplats" if has_email else None,
                            "verifieringskalla": source_url,
                            "verifierat_av": "agent:harvest_team_pages",
                            "verifierat_datum": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    .execute()
                )
                ncid = ins.data[0]["id"] if ins.data else None
                if ncid:
                    sb.table("sources").insert(
                        {
                            "company_id": cid,
                            "contact_id": ncid,
                            "field_name": "contacts.*",
                            "source_url": source_url,
                            "scraper_tier": 2,
                            "raw_excerpt": f"team-page: {namn} ({p.get('roll') or ''})",
                            "critic_note": "harvest_team_pages workflow — public om-oss/team",
                        }
                    ).execute()
                inserted += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                if errors <= 8:
                    console.print(f"[red]insert fail {namn}: {exc}[/]")

    table = Table(title="Apply team people")
    table.add_column("Metrica", style="cyan")
    table.add_column("Valore", justify="right", style="green")
    table.add_row("Inseriti" if apply else "Da inserire", str(inserted))
    table.add_row("Saltati (duplicati)", str(skipped_dup))
    table.add_row("Saltati (nome non valido)", str(skipped_clean))
    if apply:
        table.add_row("Errori", str(errors), style="red" if errors else None)
    console.print(table)
    if not apply:
        console.print("\n[yellow]DRY-RUN — niente scritto. Rilancia con --apply.[/]")


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", help="Path al JSON di risultato del workflow team-pages")
    p.add_argument("--apply", action="store_true", help="Scrivi davvero (default: dry-run)")
    args = p.parse_args()
    main(args.path, args.apply)


if __name__ == "__main__":
    _cli()
