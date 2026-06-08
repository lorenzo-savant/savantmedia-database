"""
T0 — Open Data import dal bulk Bolagsverket / SCB.

I bulk file sono CC-BY-4.0, pubblicati gratuitamente da Bolagsverket
dal febbraio 2025 per conformità con la Direttiva UE 2019/1024
(High-Value Datasets). Niente scraping, niente rate limit, niente
account: solo download + parse + insert.

Sub-comandi:
    inspect          Scarica (con cache) e mostra schema + prime righe
                     per capire la struttura del file.
    apply            Inserisce in Supabase (companies + sources audit).
                     Sempre con un filtro (--region / --orgnr / --limit)
                     per non saturare il free tier (500 MB).
    stats            Conta cosa c'è già nel DB per fonte 'bolagsverket_bulk'.

Esempi:
    python -m scripts.import_bolagsverket_bulk inspect
    python -m scripts.import_bolagsverket_bulk inspect --source scb
    python -m scripts.import_bolagsverket_bulk apply --region "Stockholms län" --limit 500
    python -m scripts.import_bolagsverket_bulk apply --orgnr 556677-1234
    python -m scripts.import_bolagsverket_bulk stats
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Vault lesson: Windows console + Swedish characters → force UTF-8.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

import duckdb
import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from supabase import Client, create_client

LICENSE_LABEL = "CC-BY-4.0"
SOURCE_TAG = "bolagsverket_bulk"
TIER = 0  # T0 = open data

console = Console()


# ── Config ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Source:
    key: str
    url_env: str
    default_url: str
    description: str


SOURCES: dict[str, Source] = {
    "bolagsverket": Source(
        key="bolagsverket",
        url_env="BOLAGSVERKET_BULK_URL",
        default_url=(
            "https://vardefulla-datamangder.bolagsverket.se/"
            "bolagsverket/bolagsverket_bulkfil.zip"
        ),
        description="Bolagsverket — registro aziende svedesi (org.nr, ragione sociale, indirizzo, amministratori)",
    ),
    "scb": Source(
        key="scb",
        url_env="SCB_BULK_URL",
        default_url=(
            "https://vardefulla-datamangder.bolagsverket.se/"
            "scb/scb_bulkfil.zip"
        ),
        description="SCB — Statistiska centralbyrån (categoria, dipendenti, fatturato range)",
    ),
}


def load_env() -> None:
    """Load env from backend/.env then repo-root .env / .env.local (whichever exist)."""
    here = Path(__file__).resolve()
    for p in (
        here.parent.parent / ".env",             # backend/.env
        here.parent.parent.parent / ".env",       # repo/.env
        here.parent.parent.parent / ".env.local",  # repo/.env.local
    ):
        if p.exists():
            load_dotenv(p)
    load_dotenv()  # PWD fallback


def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        console.print(
            "[red]ERROR[/red]: SUPABASE_URL/NEXT_PUBLIC_SUPABASE_URL and "
            "SUPABASE_SECRET_KEY required in .env / .env.local"
        )
        sys.exit(2)
    return create_client(url, key)


def cache_dir() -> Path:
    raw = os.environ.get("BULK_CACHE_DIR", "./data/bulk")
    p = Path(raw).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Download ─────────────────────────────────────────────────────────


def download_bulk(source: Source, force: bool = False) -> Path:
    """Download bulk zip with progress bar, cache locally."""
    url = os.environ.get(source.url_env, source.default_url)
    dest = cache_dir() / f"{source.key}_bulk.zip"

    if dest.exists() and not force:
        size_mb = dest.stat().st_size / (1024 * 1024)
        console.print(
            f"[dim]Using cached file: {dest.name} ({size_mb:.1f} MB)[/dim]"
        )
        return dest

    console.print(f"Downloading [cyan]{url}[/cyan]")
    with httpx.stream("GET", url, follow_redirects=True, timeout=None) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", "0")) or None
        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("download", total=total)
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))

    size_mb = dest.stat().st_size / (1024 * 1024)
    console.print(f"[green]Saved[/green] {dest.name} ({size_mb:.1f} MB)")
    return dest


# ── Inspection: discover format of the unknown bulk ──────────────────


def list_zip_members(zip_path: Path) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(zip_path) as zf:
        return zf.infolist()


def extract_first_file(zip_path: Path) -> Path | None:
    """Extract the first non-empty member next to the zip, return its path."""
    out_dir = zip_path.parent
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            if member.is_dir() or member.file_size == 0:
                continue
            target = out_dir / Path(member.filename).name
            with zf.open(member) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            return target
    return None


def inspect_file(data_file: Path, source_key: str) -> None:
    """Bolagsverket bulk: semicolon-separated, values annotated as `value$LABEL`."""
    size_mb = data_file.stat().st_size / (1024 * 1024)
    suffix = data_file.suffix.lower()
    console.print(
        f"\n[bold]File:[/bold] {data_file.name}  "
        f"[dim]({size_mb:.1f} MB, suffix={suffix or 'none'})[/dim]"
    )

    # First bytes (UTF-8 sniff)
    with open(data_file, "rb") as f:
        head = f.read(2048)
    head_txt = head.decode("utf-8", errors="replace")
    console.print("[bold]First 2 KB:[/bold]")
    console.print(f"[dim]{head_txt[:1500]}[/dim]")

    con = duckdb.connect()
    quoted = str(data_file).replace("'", "''")

    # Bolagsverket bulk = semicolon-delimited, quoted strings, header row
    try:
        schema_rows = con.execute(
            f"DESCRIBE SELECT * FROM read_csv_auto('{quoted}', delim=';', header=true, sample_size=50000, quote='\"')"
        ).fetchall()
        if schema_rows:
            console.print("\n[bold green]Parses as semicolon-delimited CSV[/bold green]")
            tbl = Table(title="Schema (header row)")
            tbl.add_column("Column")
            tbl.add_column("Type")
            for name, typ, *_ in schema_rows:
                tbl.add_row(str(name), str(typ))
            console.print(tbl)

            count = con.execute(
                f"SELECT COUNT(*) FROM read_csv('{quoted}', delim=';', header=true, quote='\"', ignore_errors=true)"
            ).fetchone()
            console.print(f"\n[dim]Approx total rows: {count[0] if count else '?'}[/dim]")

            sample = con.execute(
                f"SELECT * FROM read_csv('{quoted}', delim=';', header=true, quote='\"', ignore_errors=true) LIMIT 3"
            ).fetchdf()
            console.print("\n[bold]First 3 rows (raw, with $LABEL annotations):[/bold]")
            for i, row in sample.iterrows():
                console.print(f"\n[bold cyan]Row {i+1}:[/bold cyan]")
                for col in sample.columns:
                    val = row[col]
                    if val is not None and str(val) not in ("nan", ""):
                        console.print(f"  {col}: {val!r}")
            return
    except Exception as e:
        console.print(f"[dim]semicolon CSV: {e}[/dim]")

    console.print("[yellow]Could not parse. Inspect file manually.[/yellow]")


def cmd_inspect(source_key: str, force_download: bool) -> int:
    source = SOURCES[source_key]
    console.rule(f"[bold]Inspect {source.key}[/bold]")
    console.print(f"[dim]{source.description}[/dim]\n")

    zip_path = download_bulk(source, force=force_download)
    console.print("\n[bold]ZIP contents:[/bold]")
    members = list_zip_members(zip_path)
    tbl = Table()
    tbl.add_column("Name")
    tbl.add_column("Size (MB)", justify="right")
    for m in members[:20]:
        tbl.add_row(m.filename, f"{m.file_size / (1024 * 1024):.2f}")
    console.print(tbl)
    if len(members) > 20:
        console.print(f"[dim]... and {len(members) - 20} more files[/dim]")

    data_file = extract_first_file(zip_path)
    if not data_file:
        console.print("[red]No extractable member found in ZIP.[/red]")
        return 2

    inspect_file(data_file, source_key)
    return 0


# ── Apply: insert filtered rows into Supabase ────────────────────────


SWEDISH_REGIONS = {
    "stockholms län",
    "skåne län",
    "västra götalands län",
    "uppsala län",
    "östergötlands län",
    "jönköpings län",
    "hallands län",
    "örebro län",
    "södermanlands län",
    "dalarnas län",
    "gävleborgs län",
    "västmanlands län",
    "värmlands län",
    "västerbottens län",
    "norrbottens län",
    "kalmar län",
    "kronobergs län",
    "blekinge län",
    "västernorrlands län",
    "jämtlands län",
    "gotlands län",
}


def strip_label(value) -> str:
    """Bolagsverket bulk encodes values as `<value>$LABEL` or `<value>$LABEL$<date>`.
    Strip everything from first '$' onward."""
    if value is None:
        return ""
    s = str(value)
    idx = s.find("$")
    return s[:idx].strip() if idx >= 0 else s.strip()


def parse_postadress(raw) -> dict[str, str]:
    """postadress format: `<gatuadress>$<c/o>$<postort>$<postnummer>$<land>`.

    Single '$' between five positional fields; the c/o field (index 1) is
    often empty (`...$$...`). NB: do NOT split on '$$' — that drops the c/o
    boundary and, when c/o is non-empty, dumps the whole string into `gata`.
    The c/o line is intentionally discarded (no schema column for it).
    Tolerates rows with fewer fields. Returns only street/city/postnr/land."""
    if raw is None:
        return {"gata": "", "stad": "", "postnummer": "", "land": "Sverige"}
    f = [p.strip() for p in str(raw).split("$")]

    def at(i: int) -> str:
        return f[i] if i < len(f) else ""

    gata = at(0)          # gatuadress / box
    # at(1) = c/o — discarded
    stad = at(2)
    postnummer = at(3)
    land = at(4) or "SE-LAND"
    return {
        "gata": gata,
        "stad": stad,
        "postnummer": postnummer,
        "land": "Sverige" if land in ("SE-LAND", "SE", "") else land,
    }


# Postnummer prefix → region (heuristic). Source: Posten Sverige.
POSTNR_TO_REGION = {
    "10": "Stockholms län", "11": "Stockholms län", "12": "Stockholms län",
    "13": "Stockholms län", "14": "Stockholms län", "15": "Södermanlands län",
    "16": "Stockholms län", "17": "Stockholms län", "18": "Stockholms län",
    "19": "Stockholms län",
    "20": "Skåne län", "21": "Skåne län", "22": "Skåne län", "23": "Skåne län",
    "24": "Skåne län", "25": "Skåne län", "26": "Skåne län", "27": "Skåne län",
    "28": "Kronobergs län", "29": "Blekinge län",
    "30": "Hallands län", "31": "Hallands län",
    "33": "Jönköpings län", "34": "Kronobergs län", "35": "Kronobergs län",
    "36": "Kronobergs län", "37": "Blekinge län", "38": "Kalmar län",
    "39": "Kalmar län",
    "40": "Västra Götalands län", "41": "Västra Götalands län",
    "42": "Västra Götalands län", "43": "Västra Götalands län",
    "44": "Västra Götalands län", "45": "Västra Götalands län",
    "46": "Västra Götalands län", "47": "Västra Götalands län",
    "50": "Västra Götalands län", "51": "Västra Götalands län",
    "52": "Västra Götalands län", "53": "Västra Götalands län",
    "54": "Västra Götalands län", "55": "Jönköpings län",
    "56": "Jönköpings län", "57": "Jönköpings län",
    "58": "Östergötlands län", "59": "Östergötlands län", "60": "Östergötlands län",
    "61": "Södermanlands län", "62": "Gotlands län",
    "63": "Södermanlands län", "64": "Södermanlands län",
    "65": "Värmlands län", "66": "Värmlands län", "67": "Värmlands län",
    "68": "Örebro län", "69": "Örebro län",
    "70": "Örebro län", "71": "Örebro län", "72": "Västmanlands län",
    "73": "Västmanlands län", "74": "Uppsala län", "75": "Uppsala län",
    "76": "Stockholms län", "77": "Dalarnas län", "78": "Dalarnas län",
    "79": "Dalarnas län",
    "80": "Gävleborgs län", "81": "Gävleborgs län", "82": "Gävleborgs län",
    "83": "Jämtlands län", "84": "Jämtlands län",
    "85": "Västernorrlands län", "86": "Västernorrlands län",
    "87": "Västernorrlands län", "88": "Västernorrlands län", "89": "Västernorrlands län",
    "90": "Västerbottens län", "91": "Västerbottens län", "92": "Västerbottens län",
    "93": "Norrbottens län", "94": "Norrbottens län", "95": "Norrbottens län",
    "96": "Norrbottens län", "97": "Norrbottens län", "98": "Norrbottens län",
}


def region_from_postnr(postnr: str) -> str:
    p = "".join(c for c in (postnr or "") if c.isdigit())
    return POSTNR_TO_REGION.get(p[:2], "") if len(p) >= 2 else ""


def cmd_apply(
    source_key: str,
    limit: int | None,
    region_filter: str | None,
    orgnr_filter: str | None,
    dry_run: bool,
) -> int:
    """Apply with real Bolagsverket bulk schema."""
    source = SOURCES[source_key]
    console.rule(f"[bold]Apply {source.key}[/bold]")

    if not (limit or region_filter or orgnr_filter):
        console.print("[red]Aborting:[/red] specify at least one of --limit/--region/--orgnr.")
        return 2

    zip_path = download_bulk(source, force=False)
    data_file = extract_first_file(zip_path)
    if not data_file:
        console.print("[red]No extractable member.[/red]")
        return 2

    con = duckdb.connect()
    quoted = str(data_file).replace("'", "''")

    # Read with explicit params for Bolagsverket format.
    # all_varchar=true → no auto type detection, everything is text
    where_clauses = ["organisationsform LIKE 'AB%'"]  # only AB
    where_clauses.append("(avregistreringsdatum IS NULL OR avregistreringsdatum = '')")  # active
    if orgnr_filter:
        clean = orgnr_filter.replace("-", "").replace(" ", "")
        where_clauses.append(f"organisationsidentitet LIKE '{clean}%'")
    where_sql = " AND ".join(f"({c})" for c in where_clauses)

    # Use a generous LIMIT in SQL (pre-region-filter) so we get enough candidates
    sql_limit = (limit or 100) * 50 if region_filter else (limit or 100)

    query = f"""
        SELECT
          organisationsidentitet,
          organisationsnamn,
          organisationsform,
          postadress,
          verksamhetsbeskrivning,
          registreringsdatum
        FROM read_csv(
          '{quoted}',
          delim=';',
          quote='"',
          header=true,
          ignore_errors=true,
          null_padding=true,
          max_line_size=10000000,
          strict_mode=false,
          parallel=false,
          all_varchar=true
        )
        WHERE {where_sql}
        LIMIT {sql_limit}
    """
    console.print(f"[dim]Reading bulk (this can take a minute on 933 MB)...[/dim]")

    try:
        rows = con.execute(query).fetchdf()
    except Exception as e:
        console.print(f"[red]DuckDB read failed: {e}[/red]")
        return 2

    console.print(f"Pre-filter: [bold]{len(rows)}[/bold] AB rows read from bulk")

    # Post-filter by region (derived from postnummer prefix)
    if region_filter:
        target = region_filter.strip().lower()
        keep = []
        for _, row in rows.iterrows():
            addr = parse_postadress(row["postadress"])
            reg = region_from_postnr(addr["postnummer"])
            if reg.lower() == target:
                keep.append(row)
        rows = (
            con.from_df(__import__("pandas").DataFrame(keep)).df()
            if keep
            else rows.iloc[0:0]
        )
        console.print(f"Post-region filter '{region_filter}': [bold]{len(rows)}[/bold] rows")

    if limit and len(rows) > limit:
        rows = rows.head(limit)
        console.print(f"Trimmed to --limit {limit}: [bold]{len(rows)}[/bold] rows")

    if dry_run:
        console.print("[yellow]--dry-run, showing first 5:[/yellow]")
        for _, row in rows.head(5).iterrows():
            orgnr = strip_label(row["organisationsidentitet"])
            namn = strip_label(row["organisationsnamn"])
            addr = parse_postadress(row["postadress"])
            reg = region_from_postnr(addr["postnummer"])
            console.print(f"  • {orgnr}  {namn}  [{addr['stad']} {addr['postnummer']} → {reg}]")
        return 0

    sb = get_supabase()
    inserted = updated = failed = 0
    errors: list[str] = []

    for _, row in rows.iterrows():
        try:
            payload = map_row_to_company(row, source_key)
            orgnr = payload.get("organisationsnummer")

            existing_id: str | None = None
            if orgnr:
                resp = (
                    sb.table("companies")
                    .select("id")
                    .eq("organisationsnummer", orgnr)
                    .limit(1)
                    .execute()
                )
                if resp.data:
                    existing_id = resp.data[0]["id"]

            if existing_id:
                sb.table("companies").update(payload).eq("id", existing_id).execute()
                company_id = existing_id
                updated += 1
            else:
                resp = sb.table("companies").insert(payload).execute()
                company_id = resp.data[0]["id"] if resp.data else None
                inserted += 1

            if company_id:
                sb.table("sources").insert(
                    {
                        "company_id": company_id,
                        "field_name": "companies.*",
                        "source_url": os.environ.get(source.url_env, source.default_url),
                        "scraper_tier": TIER,
                        "raw_excerpt": SOURCE_TAG,
                        "license_label": LICENSE_LABEL,
                    }
                ).execute()
        except Exception as e:
            failed += 1
            if len(errors) < 10:
                errors.append(str(e)[:200])

    console.print()
    tbl = Table(title=f"Result — {source.key}")
    tbl.add_column("Metric")
    tbl.add_column("Count", justify="right")
    tbl.add_row("Inserted", str(inserted))
    tbl.add_row("Updated", str(updated))
    tbl.add_row("Failed", str(failed), style="red" if failed else None)
    console.print(tbl)
    for e in errors:
        console.print(f"[dim red]• {e}[/dim red]")
    return 0 if failed == 0 else 1


# ── Mapping (PLACEHOLDER — adjust after inspect) ─────────────────────
#
# Bolagsverket bulk column names are TBD until `inspect` is run.
# Once you see real column names, update the constants below and
# the body of map_row_to_company().
#
# The mapping below covers the most likely Swedish dataset naming.

ORGNR_COLUMN = "organisationsnummer"
REGION_COLUMN = "lan"  # spesso "lan" o "region"


def _get(row, *keys: str, default=None):
    for k in keys:
        if k in row.index and row[k] is not None:
            v = row[k]
            try:
                # pandas NaN check
                import math

                if isinstance(v, float) and math.isnan(v):
                    continue
            except Exception:
                pass
            return v
    return default


def map_row_to_company(row, source_key: str) -> dict:
    """Map a Bolagsverket bulk row to a `companies` payload."""
    orgnr_raw = strip_label(row["organisationsidentitet"])
    digits = "".join(c for c in orgnr_raw if c.isdigit())
    orgnr_norm = f"{digits[:6]}-{digits[6:]}" if len(digits) == 10 else None

    namn = strip_label(row["organisationsnamn"]) or "(okänt namn)"
    addr = parse_postadress(row["postadress"])
    region = region_from_postnr(addr["postnummer"])

    return {
        "organisationsnummer": orgnr_norm,
        "foretagsnamn": namn,
        "bolagsnamn": namn,
        "domain": None,
        "antal_anstallda": None,  # Bolagsverket bulk doesn't include this
        "storlek_kategori": None,
        "storlek_manuell": False,
        "adress_gata": addr["gata"],
        "postnummer": addr["postnummer"],
        "stad": addr["stad"].title() if addr["stad"] else "",
        "region": region,
        "land": "Sverige",
        "reception_telefon": "",
        "email_info": "",
        "sok_fler_kontakter": True,
        "interna_anteckningar": f"Importerad från Bolagsverket bulk (CC-BY-4.0) {__import__('datetime').date.today().isoformat()}",
        "arkiverad": False,
        "arkiverad_av": "",
        "license_label": LICENSE_LABEL,
    }


# ── Stats ────────────────────────────────────────────────────────────


def cmd_stats() -> int:
    sb = get_supabase()
    console.rule("[bold]T0 Open Data — DB stats[/bold]")

    # Companies imported via bulk (matched via sources audit)
    resp = (
        sb.table("sources")
        .select("company_id", count="exact")
        .eq("raw_excerpt", SOURCE_TAG)
        .execute()
    )
    bulk_count = resp.count or 0

    total = sb.table("companies").select("*", count="exact", head=True).execute().count or 0
    archived = (
        sb.table("companies")
        .select("*", count="exact", head=True)
        .eq("arkiverad", True)
        .execute()
        .count
        or 0
    )

    tbl = Table()
    tbl.add_column("Metric")
    tbl.add_column("Count", justify="right")
    tbl.add_row("Total companies in DB", str(total))
    tbl.add_row("  of which archived", str(archived))
    tbl.add_row(f"Source entries tagged '{SOURCE_TAG}'", str(bulk_count))
    console.print(tbl)
    return 0


# ── CLI entry ────────────────────────────────────────────────────────


def main() -> int:
    load_env()

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inspect = sub.add_parser("inspect", help="Download + show schema/sample")
    p_inspect.add_argument(
        "--source",
        choices=list(SOURCES.keys()),
        default="bolagsverket",
    )
    p_inspect.add_argument("--force", action="store_true", help="Re-download even if cached")

    p_apply = sub.add_parser("apply", help="Insert rows into Supabase")
    p_apply.add_argument(
        "--source",
        choices=list(SOURCES.keys()),
        default="bolagsverket",
    )
    p_apply.add_argument("--limit", type=int, default=None)
    p_apply.add_argument("--region", default=None, help="e.g. 'Stockholms län'")
    p_apply.add_argument("--orgnr", default=None, help="Filter by single org.nr")
    p_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written, but skip DB writes",
    )

    sub.add_parser("stats", help="Show DB stats for T0 bulk imports")

    args = parser.parse_args()

    if args.cmd == "inspect":
        return cmd_inspect(args.source, args.force)
    if args.cmd == "apply":
        if args.region and args.region.lower() not in SWEDISH_REGIONS:
            console.print(
                f"[yellow]Warning:[/yellow] '{args.region}' not in known Swedish "
                f"regions list — typo? Continuing anyway."
            )
        return cmd_apply(
            args.source,
            args.limit,
            args.region,
            args.orgnr,
            args.dry_run,
        )
    if args.cmd == "stats":
        return cmd_stats()
    return 2


if __name__ == "__main__":
    sys.exit(main())
