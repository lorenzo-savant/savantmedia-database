"""
Import mirato dal bulk SCB (CC-BY-4.0): aziende AB nei settori
COMUNICAZIONE / CONSULENZA / FORMAZIONE.

Il bulk (Bolagsverket *e* SCB) NON contiene il numero di dipendenti, quindi il
filtro ">20 dipendenti" non è applicabile in import — `antal_anstallda` resta
None e va riempito in fase di arricchimento (web). Qui filtriamo per SETTORE
(codici SNI del primario Ng1) e forma giuridica = aktiebolag (JurForm=49):

  - Comunicazione : SNI sezione J (58–63)  Informations- och kommunikation
  - Consulenza    : SNI sezione M (69–75)  juridik/ekonomi/teknik (incl. PR/reklam)
  - Formazione    : SNI sezione P (85)       Utbildning

Solo net-new (org.nr non già in DB). Inserisce in `companies` + audit `sources`.

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.import_scb_industry --limit 500 --dry-run
    .venv/Scripts/python.exe -m scripts.import_scb_industry --limit 500
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import date
from pathlib import Path

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

from scripts.enrich_from_scb_full import sni_to_section, derive_lan, _norm_orgnr

console = Console()
ROOT = Path(__file__).resolve().parent.parent
LICENSE_LABEL = "CC-BY-4.0"
SCB_URL = "https://vardefulla-datamangder.bolagsverket.se/scb/scb_bulkfil.zip"

# Sezioni SNI bersaglio (primo 2 cifre del codice Ng1)
TARGET_SECTIONS = {"J (comunicazione)": range(58, 64),
                   "M (consulenza)": range(69, 76),
                   "P (formazione)": range(85, 86)}


def _sb() -> Client:
    load_dotenv(ROOT.parent / ".env")
    load_dotenv(ROOT.parent / ".env.local")
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    return create_client(url, os.environ["SUPABASE_SECRET_KEY"])


def _scb_txt() -> Path:
    bulk = ROOT / "data" / "bulk"
    cands = sorted(glob.glob(str(bulk / "scb_bulkfil_JE_*.utf8.txt"))) or \
        sorted(glob.glob(str(bulk / "scb_bulkfil_JE_*.txt")))
    if not cands:
        raise FileNotFoundError(f"Nessun bulk SCB in {bulk}")
    return Path(cands[0])


def _existing_orgnrs(sb: Client) -> set[str]:
    out: set[str] = set()
    start, page = 0, 1000
    while True:
        r = (sb.table("companies").select("organisationsnummer")
             .order("id").range(start, start + page - 1).execute())
        if not r.data:
            break
        for c in r.data:
            n = _norm_orgnr(c.get("organisationsnummer") or "")
            if n:
                out.add(n)
        if len(r.data) < page:
            break
        start += page
    return out


def main(limit: int, dry_run: bool) -> None:
    sb = _sb()
    console.rule("[bold]Import SCB — comunicazione / consulenza / formazione")
    existing = _existing_orgnrs(sb)
    console.print(f"[dim]Org.nr già in DB: {len(existing):,}[/]")

    scb = _scb_txt()
    con = duckdb.connect(":memory:")
    base = (f"read_csv('{scb}', delim='\t', header=true, ignore_errors=true, "
            "all_varchar=true)")
    cond = "try_cast(substr(lpad(Ng1,5,'0'),1,2) as int)"
    # Settori bersaglio J/M/P, MA escludi 70100 (huvudkontor/holding — non è un
    # servizio di consulenza/comunicazione, gonfierebbe il set di capogruppo).
    ind = (f"(({cond} BETWEEN 58 AND 63 OR {cond} BETWEEN 69 AND 75 OR {cond}=85) "
           "AND lpad(Ng1,5,'0') <> '70100')")
    console.print(f"[dim]Scanning {scb.name} via duckdb…[/]")
    rows = con.execute(f"""
        SELECT PeOrgNr, coalesce(Foretagsnamn, Namn) AS nm,
               Gatuadress, PostNr, PostOrt, Ng1, Ng2, Ng3, Ng4, Ng5
        FROM {base}
        WHERE JurForm='49' AND {ind}
          AND coalesce(Foretagsnamn, Namn) IS NOT NULL
          AND length(regexp_replace(PeOrgNr,'[^0-9]','')) >= 10
        ORDER BY PeOrgNr
    """).fetchall()
    console.print(f"[dim]Righe AB nei settori bersaglio: {len(rows):,}[/]")

    payloads: list[dict] = []
    seen: set[str] = set()
    for orgnr, nm, gata, postnr, postort, *ngs in rows:
        norm = _norm_orgnr(orgnr or "")
        if not norm or norm in existing or norm in seen:
            continue
        seen.add(norm)
        codes = [c for c in ngs if c and c.strip() and c != "0"]
        primary = codes[0] if codes else None
        letter, desc = sni_to_section(primary)
        stad = (postort or "").strip().title()
        digits = "".join(ch for ch in norm if ch.isdigit())
        payloads.append({
            "organisationsnummer": f"{digits[:6]}-{digits[6:]}",
            "foretagsnamn": nm.strip(),
            "bolagsnamn": nm.strip(),
            "domain": None,
            "antal_anstallda": None,   # non nel bulk — va arricchito (web)
            "storlek_kategori": None,
            "storlek_manuell": False,
            "adress_gata": (gata or "").strip(),
            "postnummer": (postnr or "").strip(),
            "stad": stad,
            "region": derive_lan(stad) or "",
            "land": "Sverige",
            "reception_telefon": "",
            "email_info": "",
            "sni_primary_kod": primary or "",
            "sni_alla_koder": codes,
            "sni_huvudgrupp": letter or "",
            "sni_branscher": desc or "",
            "sok_fler_kontakter": True,
            "interna_anteckningar": (
                f"Import SCB (CC-BY-4.0) settore {letter} {date.today().isoformat()} "
                "— target: comunicazione/consulenza/formazione"),
            "arkiverad": False,
            "arkiverad_av": "",
            "license_label": LICENSE_LABEL,
        })
        if len(payloads) >= limit:
            break

    # distribuzione per sezione
    dist: dict[str, int] = {}
    for p in payloads:
        dist[p["sni_huvudgrupp"]] = dist.get(p["sni_huvudgrupp"], 0) + 1
    console.print(f"[bold cyan]Net-new da inserire: {len(payloads)} "
                  f"(per sezione: {dist})[/]")

    if dry_run:
        for p in payloads[:10]:
            console.print(f"[dim]  {p['foretagsnamn'][:38]:38} "
                          f"SNI {p['sni_primary_kod']:6} {p['stad']}[/]")
        console.print("[bold yellow]DRY-RUN — nessuna scrittura[/]")
        return

    inserted = 0
    for i in range(0, len(payloads), 100):
        chunk = payloads[i:i + 100]
        try:
            res = sb.table("companies").insert(chunk).execute()
            for row in (res.data or []):
                sb.table("sources").insert({
                    "company_id": row["id"],
                    "field_name": "companies.organisationsnummer",
                    "source_url": SCB_URL, "scraper_tier": 0,
                    "license_label": LICENSE_LABEL,
                    "raw_excerpt": f"SCB industry import {row.get('foretagsnamn','')}"[:500],
                    "critic_note": "import_scb_industry.py — comunicazione/consulenza/formazione",
                }).execute()
            inserted += len(res.data or [])
            console.print(f"[green]inserted {inserted}/{len(payloads)}[/]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]insert batch @{i}: {exc}[/]")

    table = Table(title="Import SCB settori")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_row("net-new candidates", str(len(payloads)))
    table.add_row("inserted", str(inserted))
    for sect, n in dist.items():
        table.add_row(f"section {sect}", str(n))
    console.print(table)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    main(a.limit, a.dry_run)
