"""
Strategia "inferenza pattern" per email decision maker — SENZA scrivere nel DB.

Per le aziende con dominio noto + un DM/VD con nome ma senza email, deduce
l'email probabile del VD:
  1. Se sul dominio esiste GIÀ ≥1 email personale verificata (in `contacts`),
     ne impara il pattern (es. {first}.{last}) → candidato ALTA confidenza.
  2. Altrimenti genera i pattern svedesi più comuni, ranked → candidati MEDI.

Output: backend/data/dm_candidates.json  (lista candidati con confidenza/fonte)
NON tocca il DB (rispetta la regola "solo email testuali"). Serve come:
  - deliverable da rivedere/promuovere a mano,
  - lista di indirizzi esatti da cercare TESTUALMENTE coi subagenti (se un
    candidato appare pubblicato, allora diventa verificato e va nel DB).

NB: la verifica SMTP non è inclusa (porta 25 bloccata su questa macchina).

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.dm_pattern_candidates
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from supabase import create_client

console = Console()
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

_GENERIC = {"info", "kontakt", "contact", "support", "hello", "hej", "post",
            "office", "press", "media", "careers", "career", "jobs", "sales",
            "hr", "admin", "marketing", "newsroom", "noreply", "no-reply",
            "order", "ekonomi", "faktura", "reception", "kundservice"}


def _sb():
    load_dotenv(ROOT.parent / ".env")
    load_dotenv(ROOT.parent / ".env.local")
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get(
        "NEXT_PUBLIC_SUPABASE_URL")
    return create_client(url, os.environ["SUPABASE_SECRET_KEY"])


def _fetch_all(sb, table, select):
    rows, start, page = [], 0, 1000
    while True:
        r = (sb.table(table).select(select).order("id")
             .range(start, start + page - 1).execute())
        if not r.data:
            break
        rows.extend(r.data)
        if len(r.data) < page:
            break
        start += page
    return rows


def _ascii(s: str) -> str:
    s = s.lower().replace(" å", "a").replace("ä", "a").replace("ö", "o")
    s = (s.replace("å", "a").replace("ä", "a").replace("ö", "o")
         .replace("ü", "u").replace("é", "e").replace("è", "e"))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s)


def _name_parts(namn: str) -> tuple[str, str] | None:
    toks = [t for t in re.split(r"\s+", namn.strip()) if t]
    if len(toks) < 2:
        return None
    first = _ascii(toks[0])
    last = _ascii(toks[-1])
    if len(first) < 2 or len(last) < 2:
        return None
    return first, last


def _pattern_of(local: str, first: str, last: str) -> str | None:
    """Detect which pattern produced `local` for a known first/last."""
    candidates = {
        "{first}.{last}": f"{first}.{last}",
        "{first}_{last}": f"{first}_{last}",
        "{first}{last}": f"{first}{last}",
        "{f}{last}": f"{first[0]}{last}",
        "{f}.{last}": f"{first[0]}.{last}",
        "{first}.{l}": f"{first}.{last[0]}",
        "{first}": first,
        "{last}": last,
    }
    for pat, val in candidates.items():
        if local == val:
            return pat
    return None


_RANKED_PATTERNS = ["{first}.{last}", "{first}", "{f}{last}", "{first}{last}",
                    "{f}.{last}"]


def _apply_pattern(pat: str, first: str, last: str) -> str:
    return (pat.replace("{first}", first).replace("{last}", last)
               .replace("{f}", first[0]).replace("{l}", last[0]))


def main() -> None:
    sb = _sb()
    companies = [c for c in _fetch_all(
        sb, "companies", "id, foretagsnamn, domain, arkiverad")
        if not c.get("arkiverad") and (c.get("domain") or "").strip()]
    by_id = {c["id"]: c for c in companies}
    contacts = _fetch_all(
        sb, "contacts", "id, company_id, namn, roll, email, is_dm, verifierad")

    # learn pattern per domain from existing verified personal emails
    domain_pattern: dict[str, str] = {}
    ks_by_company: dict[str, list] = {}
    for k in contacts:
        ks_by_company.setdefault(k["company_id"], []).append(k)
        em = (k.get("email") or "").strip().lower()
        namn = (k.get("namn") or "").strip()
        if not em or "@" not in em or not k.get("verifierad"):
            continue
        local, _, host = em.partition("@")
        if local in _GENERIC:
            continue
        comp = by_id.get(k["company_id"])
        if not comp or host != (comp.get("domain") or "").lower():
            continue
        np = _name_parts(namn)
        if not np:
            continue
        pat = _pattern_of(local, np[0], np[1])
        if pat and host not in domain_pattern:
            domain_pattern[host] = pat

    candidates = []
    for c in companies:
        cid, dom = c["id"], (c["domain"] or "").lower()
        ks = ks_by_company.get(cid, [])
        has_dm_email = any(k.get("is_dm") and (k.get("email") or "").strip()
                           for k in ks)
        if has_dm_email:
            continue
        # find a DM/VD name without email
        dm = next((k for k in ks if k.get("is_dm") and (k.get("namn") or "").strip()
                   and not (k.get("email") or "").strip()), None)
        if not dm:
            dm = next((k for k in ks if (k.get("namn") or "").strip()
                       and not (k.get("email") or "").strip()), None)
        if not dm:
            continue
        np = _name_parts(dm["namn"])
        if not np:
            continue
        first, last = np
        if dom in domain_pattern:
            pat = domain_pattern[dom]
            candidates.append({
                "id": cid, "foretagsnamn": c["foretagsnamn"], "domain": dom,
                "vd_namn": dm["namn"], "roll": dm.get("roll") or "VD",
                "candidate_email": f"{_apply_pattern(pat, first, last)}@{dom}",
                "confidence": "high", "pattern_source": f"domain pattern {pat}",
            })
        else:
            cands = [f"{_apply_pattern(p, first, last)}@{dom}"
                     for p in _RANKED_PATTERNS]
            candidates.append({
                "id": cid, "foretagsnamn": c["foretagsnamn"], "domain": dom,
                "vd_namn": dm["namn"], "roll": dm.get("roll") or "VD",
                "candidate_email": cands[0],
                "alt_candidates": cands[1:],
                "confidence": "medium", "pattern_source": "common SE patterns",
            })

    DATA.mkdir(parents=True, exist_ok=True)
    out = DATA / "dm_candidates.json"
    out.write_text(json.dumps(candidates, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    hi = sum(1 for c in candidates if c["confidence"] == "high")
    table = Table(title="DM email candidates (NOT written to DB)")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_row("domains with learned pattern", str(len(domain_pattern)))
    table.add_row("candidates total", str(len(candidates)))
    table.add_row("  high confidence (known pattern)", str(hi))
    table.add_row("  medium (common patterns)", str(len(candidates) - hi))
    console.print(table)
    console.print(f"[bold]Wrote {out}[/]")
    console.print("[dim]Use these to dork the exact address textually; "
                  "promote to DB only if seen published.[/]")


if __name__ == "__main__":
    main()
