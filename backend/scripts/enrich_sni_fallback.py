"""
Fallback SNI per aziende che SCB bulk classifica come "00000" (vilande) o
non riesce a matchare.

SCB assegna codice '00000' alle aziende registrate ma senza attività
dichiarata (vilande/holdingbolag). Per non avere `sni_branscher` vuoto sul
frontend, applichiamo heuristics keyword-based sul `foretagsnamn` per
inferire un settore plausibile. Le 16 aziende interessate al 2026-05-28 sono
tipicamente:

- Fastighets / Förvaltning → L (Fastighetsverksamhet)
- Holding / Invest → K (Finans- och försäkring)
- Parkering → H (Transport)
- Förlag / Dagbladets / Press → J (Information/kommunikation)
- Marin / Båt / Sjö → H (Transport)
- Textil / Verkstad / Industri → C (Tillverkning)
- Restaurang / Hotel / Bageri → I (Hotell/Restaurang)
- Default vilande → Z (custom: "Vilande / Holdingbolag")

L'`sni_primary_kod` rimane "00000" per tracciabilità — l'audit row in
`sources` annota che è stato un fallback heuristic, non un dato SCB.

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_sni_fallback
    .venv/Scripts/python.exe -m scripts.enrich_sni_fallback --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys
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

console = Console()


# Keyword → (huvudgrupp, branschbeskrivning, fallback_sni_kod_descrittivo)
# Ordine importante: il match si ferma al primo che hits.
_HEURISTICS: list[tuple[tuple[str, ...], str, str]] = [
    # Fastighets / immobiliare
    (
        ("fastighet", "fastighets", "fastighetsaktiebolag"),
        "L",
        "Fastighetsverksamhet (vilande / förvaltning av fastighet)",
    ),
    # Förvaltning / holding
    (
        ("förvaltning", "förvaltnings", "holding", "invest", "kapitalförvaltning"),
        "K",
        "Holdingverksamhet (förvaltning av tillgångar)",
    ),
    # Parkering / garage
    (
        ("parkering", "garage", "garaget"),
        "H",
        "Parkering och uppställning av motorfordon",
    ),
    # Förlag / press / media
    (
        ("förlag", "dagbladet", "dagbladets", "press", "nyheter", "media"),
        "J",
        "Utgivning av tidskrifter och böcker",
    ),
    # Marin / båt / sjö / hamn
    (
        ("marin", "båt", "sjö", "hamn", "färje", "rederi"),
        "H",
        "Sjötransport / marin verksamhet",
    ),
    # Textil / kläder / atelier
    (
        ("textil", "atelier", "klädes", "konfektion"),
        "C",
        "Tillverkning av textilier och kläder",
    ),
    # Verkstad / industri / maskin / metall
    (
        ("verkstad", "industri", "maskin", "metall", "tillverkning"),
        "C",
        "Tillverkning / verkstadsindustri",
    ),
    # Restaurang / hotel / bageri / café
    (
        ("restaurang", "hotel", "hotell", "bageri", "café", "konditori"),
        "I",
        "Restaurang- och hotellverksamhet",
    ),
    # Bil / motor / fordon (men inte parkering — det fångas innan)
    (
        ("bil ", "bilen ", "motor", "fordon", "bilservice"),
        "G",
        "Handel och reparation av motorfordon",
    ),
    # Bygg / entreprenad
    (
        ("bygg", "byggnads", "entreprenad", "schakt"),
        "F",
        "Byggverksamhet",
    ),
    # Handel / butik
    (
        ("handel", "butik", "köpmans", "grosshandel"),
        "G",
        "Handel",
    ),
    # Konsult / data / IT
    (
        ("konsult", "consulting", "data ", "it ", "system"),
        "M",
        "Konsultverksamhet",
    ),
    # Trafik / transport / åkeri
    (
        ("åkeri", "transport", "trafik", "logistik"),
        "H",
        "Transport och logistik",
    ),
]

_SNI_HUVUDGRUPPER_DESC: dict[str, str] = {
    "A": "Jordbruk, skogsbruk och fiske",
    "B": "Utvinning av mineral",
    "C": "Tillverkning",
    "D": "Försörjning av el, gas, värme och kyla",
    "E": "Vattenförsörjning, avlopp, avfall",
    "F": "Byggverksamhet",
    "G": "Handel; reparation av motorfordon",
    "H": "Transport och magasinering",
    "I": "Hotell- och restaurangverksamhet",
    "J": "Informations- och kommunikation",
    "K": "Finans- och försäkringsverksamhet",
    "L": "Fastighetsverksamhet",
    "M": "Juridik, ekonomi, vetenskap, teknik",
    "N": "Uthyrning, fastighetsservice, stödtjänster",
    "O": "Offentlig förvaltning och försvar",
    "P": "Utbildning",
    "Q": "Vård och omsorg; sociala tjänster",
    "R": "Kultur, nöje och fritid",
    "S": "Annan serviceverksamhet",
    "T": "Förvärvsarbete i hushåll",
    "U": "Internationella organisationer",
    # Default fallback for truly unknown vilande bolag
    "Z": "Vilande / Holdingbolag (okänd verksamhet)",
}


def _classify(name: str) -> tuple[str, str]:
    """Ritorna (huvudgrupp, branschbeskrivning)."""
    lower = " " + name.lower() + " "
    for keywords, section, desc in _HEURISTICS:
        for kw in keywords:
            # match come parola intera o prefisso composto
            if kw in lower:
                full = (
                    f"{_SNI_HUVUDGRUPPER_DESC[section]} — {desc}"
                )
                return section, full
    return "Z", _SNI_HUVUDGRUPPER_DESC["Z"]


def _supabase() -> Client:
    load_dotenv()
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )


def main(dry_run: bool) -> None:
    sb = _supabase()
    # Aziende residue: nessun huvudgrupp valido oppure sni_primary_kod 00000
    resp = (
        sb.table("companies")
        .select(
            "id, foretagsnamn, organisationsnummer, sni_primary_kod, "
            "sni_huvudgrupp, sni_branscher"
        )
        .eq("arkiverad", False)
        .execute()
    )
    todo = [
        r for r in resp.data
        if not (r.get("sni_huvudgrupp") or "").strip()
        or not (r.get("sni_branscher") or "").strip()
    ]
    console.print(f"[bold cyan]Companies needing SNI fallback: {len(todo)}[/]")
    if not todo:
        return

    table = Table(title="SNI fallback heuristic")
    table.add_column("Företag", style="white")
    table.add_column("Föreslagen huvudgrupp", style="green")
    table.add_column("Beskrivning", style="cyan", overflow="fold")

    stats = {"updated": 0, "fallback_z": 0, "errors": 0}
    for r in todo:
        section, desc = _classify(r["foretagsnamn"])
        if section == "Z":
            stats["fallback_z"] += 1
        table.add_row(r["foretagsnamn"][:40], section, desc[:60])
        if dry_run:
            continue
        try:
            sb.table("companies").update({
                "sni_huvudgrupp": section,
                "sni_branscher": desc,
            }).eq("id", r["id"]).execute()
            sb.table("sources").insert({
                "company_id": r["id"],
                "field_name": "companies.sni_branscher",
                "source_url": "internal:enrich_sni_fallback.py",
                "scraper_tier": 0,
                "raw_excerpt": (
                    f"primary_kod={r.get('sni_primary_kod') or 'null'} "
                    f"foretagsnamn='{r['foretagsnamn']}'"
                ),
                "critic_note": (
                    f"Heuristic fallback: section={section} "
                    f"(SCB hade ingen aktiv SNI)"
                ),
            }).execute()
            stats["updated"] += 1
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]ERR {r['foretagsnamn']}: {exc}[/]")
            stats["errors"] += 1

    console.print(table)
    summary = Table(title="Summary")
    summary.add_column("Metric", style="cyan")
    summary.add_column("Count", justify="right", style="green")
    for k, v in stats.items():
        summary.add_row(k, str(v))
    console.print(summary)
    if dry_run:
        console.print("[bold yellow]DRY-RUN — no DB writes[/]")


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)


if __name__ == "__main__":
    _cli()
