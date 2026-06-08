"""
T0 — Arricchimento completo dal bulk SCB (öppna data, CC-BY-4.0).

A differenza di `enrich_sni_from_scb.py` (solo SNI), questo script popola in
un solo passaggio TUTTI i campi che il bulk SCB copre per org.nr:

  - adress_gata   ← Gatuadress
  - postnummer    ← PostNr
  - stad          ← PostOrt
  - sni_primary_kod / sni_alla_koder / sni_huvudgrupp / sni_branscher ← Ng1..Ng5

Poi deriva **senza rete** due campi:

  - region            ← da stad (self-join sulle righe che hanno già region,
                         poi mappa statica kommun/stad → län)
  - storlek_kategori  ← da antal_anstallda (liten<50 / medel<250 / multinationell)

Sorgente bulk: backend/data/bulk/  (file già scaricati e ri-encodati utf-8).
Override con BULK_DIR.

Idempotente: scrive solo campi vuoti. Audit row in `sources` per ogni campo.

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_from_scb_full --dry-run
    .venv/Scripts/python.exe -m scripts.enrich_from_scb_full
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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
SCB_URL = "https://vardefulla-datamangder.bolagsverket.se/scb/scb_bulkfil.zip"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BULK_DIR = ROOT / "data" / "bulk"


# ── SNI 2007 sezione → lettera + descrizione svedese ───────────────────────
_SNI_SECTIONS: list[tuple[int, int, str, str]] = [
    (1, 3, "A", "Jordbruk, skogsbruk och fiske"),
    (5, 9, "B", "Utvinning av mineral"),
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
    (97, 98, "T", "Förvärvsarbete i hushåll"),
    (99, 99, "U", "Verksamhet vid internationella organisationer"),
]


def sni_to_section(code: str | None) -> tuple[str | None, str | None]:
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


# ── stad/kommun → län (fallback statico) ───────────────────────────────────
# Coprire i kommuner/städer svedesi più comuni. Per il resto si usa il
# self-join sui dati esistenti; ciò che resta vuoto va alla fase web.
_CITY_LAN: dict[str, str] = {}


def _add(lan: str, *cities: str) -> None:
    for c in cities:
        _CITY_LAN[c.lower()] = lan


_add("Stockholms län", "Stockholm", "Solna", "Sundbyberg", "Nacka", "Täby",
     "Sollentuna", "Huddinge", "Järfälla", "Lidingö", "Danderyd", "Bromma",
     "Kista", "Spånga", "Vällingby", "Handen", "Haninge", "Tyresö", "Botkyrka",
     "Tumba", "Norsborg", "Vallentuna", "Upplands Väsby", "Åkersberga",
     "Österåker", "Sigtuna", "Märsta", "Södertälje", "Nynäshamn", "Värmdö",
     "Gustavsberg", "Ekerö", "Norrtälje", "Vaxholm", "Saltsjö-Boo",
     "Saltsjöbaden", "Älvsjö", "Hägersten", "Skärholmen", "Bandhagen",
     "Enskede", "Johanneshov", "Farsta", "Skarpnäck", "Upplands-Bro", "Kungsängen")
_add("Uppsala län", "Uppsala", "Enköping", "Bålsta", "Knivsta", "Tierp",
     "Östhammar", "Älvkarleby", "Heby")
_add("Södermanlands län", "Eskilstuna", "Nyköping", "Katrineholm", "Strängnäs",
     "Flen", "Oxelösund", "Trosa", "Gnesta", "Vingåker", "Mariefred")
_add("Östergötlands län", "Linköping", "Norrköping", "Motala", "Mjölby",
     "Finspång", "Söderköping", "Åtvidaberg", "Vadstena", "Kisa", "Valdemarsvik")
_add("Jönköpings län", "Jönköping", "Värnamo", "Nässjö", "Vetlanda", "Gislaved",
     "Tranås", "Eksjö", "Vaggeryd", "Sävsjö", "Habo", "Mullsjö", "Huskvarna")
_add("Kronobergs län", "Växjö", "Ljungby", "Älmhult", "Alvesta", "Tingsryd",
     "Markaryd", "Lessebo", "Lammhult")
_add("Kalmar län", "Kalmar", "Västervik", "Oskarshamn", "Nybro", "Vimmerby",
     "Mönsterås", "Hultsfred", "Emmaboda", "Borgholm", "Mörbylånga", "Torsås")
_add("Gotlands län", "Visby", "Gotland", "Slite", "Hemse")
_add("Blekinge län", "Karlskrona", "Karlshamn", "Ronneby", "Sölvesborg",
     "Olofström")
_add("Skåne län", "Malmö", "Helsingborg", "Lund", "Kristianstad", "Hässleholm",
     "Landskrona", "Trelleborg", "Ängelholm", "Ystad", "Eslöv", "Höganäs",
     "Kävlinge", "Staffanstorp", "Lomma", "Svedala", "Vellinge", "Sjöbo",
     "Hörby", "Höör", "Bjuv", "Åstorp", "Klippan", "Perstorp", "Simrishamn",
     "Tomelilla", "Bromölla", "Osby", "Östra Göinge", "Båstad", "Svalöv",
     "Skurup", "Burlöv", "Arlöv", "Limhamn")
_add("Hallands län", "Halmstad", "Varberg", "Falkenberg", "Kungsbacka",
     "Laholm", "Hyltebruk")
_add("Västra Götalands län", "Göteborg", "Borås", "Trollhättan", "Skövde",
     "Uddevalla", "Mölndal", "Vänersborg", "Lidköping", "Alingsås",
     "Mariestad", "Kungälv", "Lerum", "Partille", "Stenungsund", "Falköping",
     "Kungsbacka", "Ulricehamn", "Mark", "Kinna", "Åmål", "Lysekil",
     "Strömstad", "Tanum", "Munkedal", "Sotenäs", "Tjörn", "Orust", "Härryda",
     "Mölnlycke", "Bollebygd", "Tranemo", "Svenljunga", "Vårgårda", "Herrljunga",
     "Tidaholm", "Hjo", "Tibro", "Karlsborg", "Gullspång", "Töreboda",
     "Grästorp", "Essunga", "Vara", "Götene", "Lilla Edet", "Dals-Ed",
     "Bengtsfors", "Mellerud", "Färgelanda", "Hönö", "Västra Frölunda",
     "Hisings Backa", "Angered", "Floda", "Gråbo")
_add("Värmlands län", "Karlstad", "Kristinehamn", "Arvika", "Säffle", "Hammarö",
     "Filipstad", "Hagfors", "Sunne", "Torsby", "Kil", "Forshaga", "Grums",
     "Årjäng", "Storfors", "Munkfors", "Eda", "Charlottenberg")
_add("Örebro län", "Örebro", "Karlskoga", "Kumla", "Lindesberg", "Hallsberg",
     "Nora", "Askersund", "Degerfors", "Laxå", "Hällefors", "Ljusnarsberg",
     "Fjugesta", "Pålsboda")
_add("Västmanlands län", "Västerås", "Köping", "Sala", "Fagersta", "Arboga",
     "Hallstahammar", "Surahammar", "Norberg", "Kungsör", "Skinnskatteberg")
_add("Dalarnas län", "Falun", "Borlänge", "Mora", "Ludvika", "Avesta",
     "Hedemora", "Säter", "Leksand", "Rättvik", "Gagnef", "Vansbro", "Malung",
     "Orsa", "Älvdalen", "Smedjebacken", "Insjön")
_add("Gävleborgs län", "Gävle", "Sandviken", "Hudiksvall", "Bollnäs", "Söderhamn",
     "Ljusdal", "Ovanåker", "Nordanstig", "Ockelbo", "Hofors", "Edsbyn",
     "Bergsjö")
_add("Västernorrlands län", "Sundsvall", "Örnsköldsvik", "Härnösand",
     "Sollefteå", "Kramfors", "Timrå", "Ånge", "Matfors")
_add("Jämtlands län", "Östersund", "Åre", "Strömsund", "Krokom", "Sveg",
     "Bräcke", "Ragunda", "Berg", "Hammarstrand", "Järpen")
_add("Västerbottens län", "Umeå", "Skellefteå", "Lycksele", "Vännäs",
     "Robertsfors", "Nordmaling", "Vindeln", "Storuman", "Sorsele", "Malå",
     "Norsjö", "Bjurholm", "Dorotea", "Åsele", "Vilhelmina", "Holmsund")
_add("Norrbottens län", "Luleå", "Piteå", "Boden", "Kiruna", "Gällivare",
     "Kalix", "Haparanda", "Älvsbyn", "Arvidsjaur", "Arjeplog", "Jokkmokk",
     "Överkalix", "Övertorneå", "Pajala", "Töre")


def derive_lan(city: str | None) -> str | None:
    if not city:
        return None
    return _CITY_LAN.get(city.strip().lower())


def classify_storlek(antal: int | None) -> str | None:
    if antal is None or antal < 0:
        return None
    if antal <= 49:
        return "liten"
    if antal <= 249:
        return "medel"
    return "multinationell"


# ── env / supabase ─────────────────────────────────────────────────────────
def _supabase() -> Client:
    load_dotenv(ROOT.parent / ".env")
    load_dotenv(ROOT.parent / ".env.local")
    load_dotenv()
    url = (
        os.environ.get("SUPABASE_URL")
        or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    )
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        console.print("[red]Mancano SUPABASE_URL / SUPABASE_SECRET_KEY[/]")
        raise SystemExit(1)
    return create_client(url, key)


def _norm_orgnr(s: str) -> str:
    digits = "".join(c for c in (s or "") if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def _scb_txt() -> Path:
    bulk_dir = Path(os.environ.get("BULK_DIR") or str(DEFAULT_BULK_DIR))
    # Preferisci il file già ri-encodato utf-8
    cands = sorted(bulk_dir.glob("scb_bulkfil_JE_*.utf8.txt"))
    if cands:
        return cands[0]
    cands = sorted(bulk_dir.glob("scb_bulkfil_JE_*.txt"))
    if cands:
        return cands[0]
    raise FileNotFoundError(
        f"Nessun bulk SCB (scb_bulkfil_JE_*.txt) in {bulk_dir}"
    )


def _build_index(
    scb_txt: Path, targets: set[str]
) -> dict[str, dict[str, Any]]:
    con = duckdb.connect(":memory:")
    console.print(f"[dim]Scanning {scb_txt.name} via duckdb…[/]")
    sql = """
    SELECT PeOrgNr, Gatuadress, PostNr, PostOrt, Ng1, Ng2, Ng3, Ng4, Ng5
    FROM read_csv(?, delim='\t', header=true, ignore_errors=true,
                  all_varchar=true)
    WHERE PeOrgNr IS NOT NULL
    """
    rows = con.execute(sql, [str(scb_txt)]).fetchall()
    console.print(f"[dim]Bulk rows scanned: {len(rows):,}[/]")
    index: dict[str, dict[str, Any]] = {}
    for orgnr, gata, postnr, postort, n1, n2, n3, n4, n5 in rows:
        norm = _norm_orgnr(orgnr or "")
        if norm not in targets or norm in index:
            continue
        codes = [c for c in (n1, n2, n3, n4, n5)
                 if c and c.strip() and c != "0"]
        index[norm] = {
            "gata": (gata or "").strip(),
            "postnr": (postnr or "").strip(),
            "postort": (postort or "").strip(),
            "codes": codes,
        }
    console.print(f"[dim]Bulk index built: {len(index):,} matches[/]")
    return index


def main(dry_run: bool) -> None:
    sb = _supabase()
    resp = (
        sb.table("companies")
        .select("id, foretagsnamn, organisationsnummer, adress_gata, "
                "postnummer, stad, region, sni_primary_kod, antal_anstallda, "
                "storlek_kategori")
        .eq("arkiverad", False)
        .execute()
    )
    companies = resp.data
    console.print(f"[bold cyan]Active companies: {len(companies)}[/]")

    targets = {_norm_orgnr(c.get("organisationsnummer") or "")
               for c in companies}
    targets.discard("")
    index = _build_index(_scb_txt(), targets)

    # Mappa città→region dai dati esistenti (self-join, formato esatto)
    existing_city_region: dict[str, str] = {}
    for c in companies:
        st = (c.get("stad") or "").strip().lower()
        rg = (c.get("region") or "").strip()
        if st and rg and st not in existing_city_region:
            existing_city_region[st] = rg

    stats = {
        "checked": 0, "scb_matched": 0,
        "adress_gata": 0, "postnummer": 0, "stad": 0,
        "sni": 0, "region": 0, "storlek_kategori": 0,
        "rows_updated": 0, "errors": 0,
    }

    for c in companies:
        stats["checked"] += 1
        cid = c["id"]
        orgnr = _norm_orgnr(c.get("organisationsnummer") or "")
        scb = index.get(orgnr)
        update: dict[str, Any] = {}
        audits: list[tuple[str, str, int, str]] = []  # field, excerpt, tier, license

        cur_stad = (c.get("stad") or "").strip()

        if scb:
            stats["scb_matched"] += 1
            if not (c.get("adress_gata") or "").strip() and scb["gata"]:
                update["adress_gata"] = scb["gata"]
                audits.append(("adress_gata", scb["gata"], 0, LICENSE_LABEL))
            if not (c.get("postnummer") or "").strip() and scb["postnr"]:
                update["postnummer"] = scb["postnr"]
                audits.append(("postnummer", scb["postnr"], 0, LICENSE_LABEL))
            if not cur_stad and scb["postort"]:
                update["stad"] = scb["postort"].title()
                cur_stad = update["stad"]
                audits.append(("stad", scb["postort"], 0, LICENSE_LABEL))
            if not (c.get("sni_primary_kod") or "").strip() and scb["codes"]:
                primary = scb["codes"][0]
                letter, desc = sni_to_section(primary)
                update["sni_primary_kod"] = primary
                update["sni_alla_koder"] = scb["codes"]
                update["sni_huvudgrupp"] = letter or ""
                update["sni_branscher"] = desc or ""
                stats["sni"] += 1
                audits.append(("sni_primary_kod",
                               json.dumps({"Ng": scb["codes"]},
                                          ensure_ascii=False), 0, LICENSE_LABEL))

        # region (deriva da stad: prima self-join dati esistenti, poi statico)
        if not (c.get("region") or "").strip() and cur_stad:
            rg = existing_city_region.get(cur_stad.lower()) or derive_lan(cur_stad)
            if rg:
                update["region"] = rg
                audits.append(("region", f"derived from stad={cur_stad}",
                               1, ""))

        # storlek_kategori (deriva da antal_anstallda)
        if c.get("storlek_kategori") is None and c.get("antal_anstallda") is not None:
            sk = classify_storlek(c.get("antal_anstallda"))
            if sk:
                update["storlek_kategori"] = sk
                audits.append(("storlek_kategori",
                               f"derived from antal={c['antal_anstallda']}",
                               1, ""))

        if not update:
            continue

        for f in ("adress_gata", "postnummer", "stad", "region",
                  "storlek_kategori"):
            if f in update:
                stats[f] += 1

        if dry_run:
            stats["rows_updated"] += 1
            console.print(
                f"[dim]would-update {c.get('foretagsnamn','?')[:34]:34} "
                f"→ {','.join(update.keys())}[/]"
            )
            continue

        try:
            sb.table("companies").update(update).eq("id", cid).execute()
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]update {c.get('foretagsnamn')}: {exc}[/]")
            stats["errors"] += 1
            continue
        for field, excerpt, tier, lic in audits:
            sb.table("sources").insert({
                "company_id": cid,
                "field_name": f"companies.{field}",
                "source_url": SCB_URL if tier == 0 else None,
                "scraper_tier": tier,
                "license_label": lic or None,
                "raw_excerpt": excerpt[:500],
                "critic_note": "enrich_from_scb_full.py — SCB öppna data / derived",
            }).execute()
        stats["rows_updated"] += 1
        console.print(
            f"[green]OK {c.get('foretagsnamn','?')[:34]:34} "
            f"→ {','.join(update.keys())}[/]"
        )

    table = Table(title="SCB full enrichment")
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
    args = p.parse_args()
    main(dry_run=args.dry_run)


if __name__ == "__main__":
    _cli()
