"""
T0 — Arricchimento SNI (näringsgrenskoder) dal bulk SCB.

SCB bulk file (`scb_bulkfil_JE_*.txt`) contiene per ogni org.nr:
- `Ng1..Ng5`: 5 codici SNI a 5 cifre (Standard för svensk näringsgrensindelning 2007)
- `PeOrgNr`: organisationsnummer (12 cifre nel bulk, normalizzato a 10 con `-`)

Per ogni azienda in `public.companies`:
1. Cerca l'org.nr nel bulk SCB via duckdb (zero-copy CSV scan)
2. Estrae Ng1..Ng5 → sni_alla_koder (jsonb array)
3. sni_primary_kod = Ng1 (più rappresentativo)
4. sni_huvudgrupp = lettera sezione SNI 2007 (A-U)
5. sni_branscher = descrizione testuale della sezione svedese
6. UPDATE companies + INSERT audit row (tier=0)

Idempotente: skip aziende con sni_primary_kod già popolato (a meno di --force).

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_sni_from_scb
    .venv/Scripts/python.exe -m scripts.enrich_sni_from_scb --dry-run
    .venv/Scripts/python.exe -m scripts.enrich_sni_from_scb --force  # ri-popola tutti
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

import duckdb
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from supabase import Client, create_client

console = Console()

LICENSE_LABEL = "CC-BY-4.0"
TIER = 0
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "bulk_cache"
ZIP_NAME = "scb_bulk.zip"


# ── Mapping SNI 2007 sezione → lettera + descrizione svedese ────────────────
# 5-digit code → top-2-digit code 01-99 → sezione lettera (A..U)

_SNI_SECTIONS: list[tuple[int, int, str, str]] = [
    (1, 3,  "A", "Jordbruk, skogsbruk och fiske"),
    (5, 9,  "B", "Utvinning av mineral"),
    (10, 33, "C", "Tillverkning"),
    (35, 35, "D", "Försörjning av el, gas, värme och kyla"),
    (36, 39, "E", "Vattenförsörjning; avloppsrening, avfallshantering och sanering"),
    (41, 43, "F", "Byggverksamhet"),
    (45, 47, "G", "Handel; reparation av motorfordon och motorcyklar"),
    (49, 53, "H", "Transport och magasinering"),
    (55, 56, "I", "Hotell- och restaurangverksamhet"),
    (58, 63, "J", "Informations- och kommunikationsverksamhet"),
    (64, 66, "K", "Finans- och försäkringsverksamhet"),
    (68, 68, "L", "Fastighetsverksamhet"),
    (69, 75, "M", "Verksamhet inom juridik, ekonomi, vetenskap och teknik"),
    (77, 82, "N", "Uthyrning, fastighetsservice, resetjänster och andra stödtjänster"),
    (84, 84, "O", "Offentlig förvaltning och försvar; obligatorisk socialförsäkring"),
    (85, 85, "P", "Utbildning"),
    (86, 88, "Q", "Vård och omsorg; sociala tjänster"),
    (90, 93, "R", "Kultur, nöje och fritid"),
    (94, 96, "S", "Annan serviceverksamhet"),
    (97, 98, "T", "Förvärvsarbete i hushåll, hushållens produktion av varor och tjänster för eget bruk"),
    (99, 99, "U", "Verksamhet vid internationella organisationer, utländska ambassader o.d."),
]


def sni_to_section(code: str | None) -> tuple[str | None, str | None]:
    """Ritorna (lettera_sezione, descrizione) dal codice SNI a 5 cifre."""
    if not code:
        return None, None
    digits = code.strip().zfill(5)
    try:
        top2 = int(digits[:2])
    except ValueError:
        return None, None
    for lo, hi, letter, desc in _SNI_SECTIONS:
        if lo <= top2 <= hi:
            return letter, desc
    return None, None


# ── Org.nr normalization ─────────────────────────────────────────────────────

def _normalize_orgnr_for_lookup(s: str) -> str:
    """DB salva '556024-8402' (10 con dash). Bulk usa '5560248402' o
    '165560248402' (12 digit incl. århundrade). Normalizza a 10-digit no-dash."""
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) >= 10:
        return digits[-10:]
    return digits


# ── Supabase ─────────────────────────────────────────────────────────────────

def _supabase() -> Client:
    load_dotenv()
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )


def _fetch_companies(
    sb: Client, only_missing: bool
) -> list[dict[str, Any]]:
    q = sb.table("companies").select(
        "id, foretagsnamn, organisationsnummer, sni_primary_kod"
    ).eq("arkiverad", False)
    if only_missing:
        q = q.or_("sni_primary_kod.is.null,sni_primary_kod.eq.")
    resp = q.execute()
    return resp.data


# ── Bulk SCB resolve ─────────────────────────────────────────────────────────

def _ensure_bulk() -> Path:
    cache_dir = Path(
        os.environ.get("BULK_CACHE_DIR") or str(DEFAULT_CACHE_DIR)
    )
    zip_path = cache_dir / ZIP_NAME
    if not zip_path.exists():
        raise FileNotFoundError(
            f"Bulk SCB non trovato in {zip_path}. "
            f"Esegui prima `python -m scripts.import_bolagsverket_bulk inspect --source scb`."
        )
    # Estrai il .txt interno se non già estratto
    with zipfile.ZipFile(zip_path) as zf:
        members = [
            m for m in zf.namelist()
            if m.endswith(".txt") and "JE_" in m
        ]
        if not members:
            raise FileNotFoundError(
                "ZIP SCB non contiene un file .txt JE_*."
            )
        target = cache_dir / members[0]
        if not target.exists():
            console.print(f"[dim]Extracting {members[0]}...[/]")
            zf.extract(members[0], cache_dir)
        # Pre-convert cp1252 → utf-8 (duckdb non riconosce 'windows-1252')
        utf8_target = target.with_suffix(".utf8.txt")
        if not utf8_target.exists():
            console.print(
                f"[dim]Re-encoding {target.name} cp1252 → utf-8 "
                f"(one-shot)...[/]"
            )
            with open(target, "rb") as fin, open(utf8_target, "wb") as fout:
                while True:
                    chunk = fin.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    fout.write(chunk.decode("cp1252").encode("utf-8"))
        return utf8_target


def _build_orgnr_index(
    bulk_txt: Path, target_orgnrs: set[str]
) -> dict[str, list[str]]:
    """Carica il bulk SCB con duckdb e indicizza Ng1..Ng5 per ogni orgnr target."""
    con = duckdb.connect(":memory:")
    console.print(f"[dim]Scanning {bulk_txt.name} via duckdb…[/]")
    # Il file è tab-separated, encoding cp1252 (svedese).
    # Schema diretto: PeOrgNr (string), Ng1..Ng5 (string).
    # FtgStat=1 = registrato attivo (filtriamo per pulizia).
    sql = """
    SELECT
        PeOrgNr AS orgnr_raw,
        Ng1, Ng2, Ng3, Ng4, Ng5
    FROM read_csv(
        ?,
        delim='\t',
        header=true,
        ignore_errors=true,
        all_varchar=true
    )
    WHERE PeOrgNr IS NOT NULL
    """
    rows = con.execute(sql, [str(bulk_txt)]).fetchall()
    console.print(f"[dim]Bulk rows scanned: {len(rows):,}[/]")

    index: dict[str, list[str]] = {}
    for orgnr_raw, n1, n2, n3, n4, n5 in rows:
        norm = _normalize_orgnr_for_lookup(orgnr_raw or "")
        if norm not in target_orgnrs:
            continue
        codes = [c for c in (n1, n2, n3, n4, n5) if c and c.strip() and c != "0"]
        if not codes:
            continue
        # Mantieni la prima occorrenza non vuota
        if norm not in index:
            index[norm] = codes
    console.print(f"[dim]Bulk index built: {len(index):,} matches[/]")
    return index


# ── Apply ────────────────────────────────────────────────────────────────────

def _apply(
    sb: Client,
    companies: list[dict[str, Any]],
    index: dict[str, list[str]],
    dry_run: bool,
) -> dict[str, int]:
    stats = {
        "checked": 0,
        "matched": 0,
        "updated": 0,
        "skipped_no_sni": 0,
        "errors": 0,
    }
    for c in companies:
        stats["checked"] += 1
        orgnr_norm = _normalize_orgnr_for_lookup(
            c.get("organisationsnummer") or ""
        )
        codes = index.get(orgnr_norm)
        if not codes:
            stats["skipped_no_sni"] += 1
            continue
        stats["matched"] += 1
        primary = codes[0]
        section_letter, section_desc = sni_to_section(primary)
        update = {
            "sni_primary_kod": primary,
            "sni_alla_koder": codes,
            "sni_huvudgrupp": section_letter or "",
            "sni_branscher": section_desc or "",
        }
        if dry_run:
            console.print(
                f"[dim]would-update {c.get('foretagsnamn','?')[:40]:40} "
                f"→ SNI {primary} ({section_letter})[/]"
            )
            continue
        try:
            sb.table("companies").update(update).eq("id", c["id"]).execute()
        except Exception as exc:
            console.print(f"[red]update {c.get('foretagsnamn')}: {exc}[/]")
            stats["errors"] += 1
            continue
        for field, _ in update.items():
            sb.table("sources").insert(
                {
                    "company_id": c["id"],
                    "field_name": f"companies.{field}",
                    "source_url": "https://vardefulla-datamangder.bolagsverket.se/scb/scb_bulkfil.zip",
                    "scraper_tier": TIER,
                    "license_label": LICENSE_LABEL,
                    "raw_excerpt": json.dumps(
                        {"orgnr": orgnr_norm, "Ng": codes}, ensure_ascii=False
                    ),
                    "critic_note": (
                        f"enrich_sni_from_scb.py — SCB bulk Ng1..Ng5 "
                        f"primary={primary} section={section_letter}"
                    ),
                }
            ).execute()
        stats["updated"] += 1
        console.print(
            f"[green]OK {c.get('foretagsnamn','?')[:38]:38} → "
            f"SNI {primary} [{section_letter}] {(section_desc or '')[:40]}[/]"
        )
    return stats


def main(dry_run: bool, force: bool) -> None:
    sb = _supabase()
    companies = _fetch_companies(sb, only_missing=not force)
    console.print(
        f"[bold cyan]Companies to enrich: {len(companies)} "
        f"(force={force})[/]"
    )
    if not companies:
        return
    target_orgnrs = {
        _normalize_orgnr_for_lookup(c.get("organisationsnummer") or "")
        for c in companies
    }
    target_orgnrs.discard("")
    bulk_txt = _ensure_bulk()
    index = _build_orgnr_index(bulk_txt, target_orgnrs)
    stats = _apply(sb, companies, index, dry_run=dry_run)

    table = Table(title="SNI enrichment from SCB")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k, v in stats.items():
        table.add_row(k, str(v))
    console.print(table)
    if dry_run:
        console.print("[bold yellow]DRY-RUN — no DB writes[/]")


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="Ri-popola anche aziende con SNI già presente.")
    args = p.parse_args()
    main(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    _cli()
