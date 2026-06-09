"""
Applica i risultati dei subagent di ricerca email decision maker (dm_result_*.json).

Schema per entry:
  {"id": "<company_id>",
   "dm": [{"namn","roll","email","email_method","source_url","linkedin","telefon"}]}

Per ogni DM trovato:
  - email normalizzata; SCARTATE le generiche (info@/kontakt@/...) — un DM deve
    avere email personale, verificata testualmente.
  - se esiste già un contatto con quel nome → UPDATE email (se vuota) + verifierad
  - altrimenti INSERT nuovo contatto DM (is_dm=true) con email verificata
  - audit row in `sources` (tier 2)

Idempotente: salta email già presenti.

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.apply_dm_results
    .venv/Scripts/python.exe -m scripts.apply_dm_results --dry-run
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
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
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
_GENERIC = {"info", "kontakt", "contact", "support", "hello", "hej", "post",
            "office", "press", "media", "careers", "career", "jobs", "sales",
            "hr", "admin", "marketing", "newsroom", "noreply", "no-reply",
            "order", "ekonomi", "faktura", "reception", "kundservice"}
_NAME_BAD = ("aktiebolag", "handelsbolag", "holding", " group", "partners ab",
             "stiftelsen", "föreningen", "ratsit", "allabolag", "hitta")
_METHOD_MAP = {
    "linkedin": "linkedin",
    "foretagswebbplats": "foretagswebbplats", "website": "foretagswebbplats",
    "team": "foretagswebbplats", "hemsida": "foretagswebbplats",
    "pressmeddelande": "pressmeddelande", "press": "pressmeddelande",
    "serpapi": "serpapi", "dork": "serpapi", "websearch": "serpapi",
    "serp": "serpapi", "google": "serpapi", "brave": "serpapi",
}


def _sb() -> Client:
    load_dotenv(ROOT.parent / ".env")
    load_dotenv(ROOT.parent / ".env.local")
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get(
        "NEXT_PUBLIC_SUPABASE_URL")
    return create_client(url, os.environ["SUPABASE_SECRET_KEY"])


def _norm_email(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().lower()
    if not _EMAIL_RE.match(s):
        return None
    local = s.split("@", 1)[0]
    if local in _GENERIC:
        return None
    return s


def _norm_phone(s: str | None) -> str | None:
    if not s:
        return None
    d = re.sub(r"[^\d+]", "", s)
    if d.startswith("00"):
        d = "+" + d[2:]
    if d.startswith("0") and not d.startswith("+"):
        d = "+46" + d[1:]
    return d if len(d) >= 6 else None


def _valid_name(namn: str) -> bool:
    if not namn or "\n" in namn:
        return False
    if not (5 <= len(namn) <= 50):
        return False
    if not (2 <= len(namn.split()) <= 4):
        return False
    low = namn.lower()
    return not any(b in low for b in _NAME_BAD)


def _method(s: str | None) -> str:
    return _METHOD_MAP.get((s or "").strip().lower(), "annan")


def _load() -> list[dict]:
    out: list[dict] = []
    for p in sorted(glob.glob(str(DATA / "dm_result_*.json"))):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
            if isinstance(d, list):
                out.extend(d)
                console.print(f"[dim]Loaded {len(d)} from {Path(p).name}[/]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]load {p}: {exc}[/]")
    return out


def main(dry_run: bool) -> None:
    entries = _load()
    if not entries:
        console.print("[yellow]No dm_result_*.json in data/[/]")
        return
    sb = _sb()
    now = datetime.now(timezone.utc).isoformat()
    stats = {"companies": 0, "emails_set": 0, "contacts_inserted": 0,
             "skipped": 0, "errors": 0}

    for e in entries:
        cid = e.get("id")
        dms = e.get("dm") or []
        if not cid or not dms:
            continue
        stats["companies"] += 1
        # contatti attuali dell'azienda
        try:
            cur = sb.table("contacts").select(
                "id, namn, email").eq("company_id", cid).execute().data
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]fetch contacts {cid}: {exc}[/]")
            stats["errors"] += 1
            continue
        by_name = {(c.get("namn") or "").strip().lower(): c for c in cur}

        for dm in dms:
            namn = (dm.get("namn") or "").strip()
            email = _norm_email(dm.get("email"))
            if not namn or not _valid_name(namn) or not email:
                stats["skipped"] += 1
                continue
            method = _method(dm.get("email_method"))
            src = (dm.get("source_url") or "").strip() or None
            li = (dm.get("linkedin") or "").strip() or None
            if li and "linkedin.com/in" not in li.lower():
                li = None
            tel = _norm_phone(dm.get("telefon"))
            roll = (dm.get("roll") or "VD").strip() or "VD"

            existing = by_name.get(namn.lower())
            try:
                if existing:
                    if (existing.get("email") or "").strip():
                        stats["skipped"] += 1
                        continue
                    payload = {
                        "email": email, "is_dm": True, "verifierad": True,
                        "verifieringsmetod": method,
                        "verifieringskalla": src or "",
                        "verifierat_av": "subagent:dm-email-2026-06-09",
                        "verifierat_datum": now,
                    }
                    if li:
                        payload["linkedin_url"] = li
                    if tel:
                        payload["telefon"] = tel
                    if not dry_run:
                        sb.table("contacts").update(payload).eq(
                            "id", existing["id"]).execute()
                        sb.table("sources").insert({
                            "company_id": cid, "contact_id": existing["id"],
                            "field_name": "contacts.email", "source_url": src,
                            "scraper_tier": 2,
                            "raw_excerpt": f"DM email {namn}={email}"[:500],
                            "critic_note": "apply_dm_results.py — verified DM email",
                        }).execute()
                    stats["emails_set"] += 1
                    console.print(f"[green]EMAIL {namn[:26]:26} → {email}[/]")
                else:
                    payload = {
                        "company_id": cid, "namn": namn, "roll": roll,
                        "email": email, "is_dm": True, "verifierad": True,
                        "verifieringsmetod": method,
                        "verifieringskalla": src or "",
                        "verifierat_av": "subagent:dm-email-2026-06-09",
                        "verifierat_datum": now,
                        "linkedin_url": li or "", "telefon": tel or "",
                    }
                    if not dry_run:
                        r = sb.table("contacts").insert(payload).execute()
                        if r.data:
                            sb.table("sources").insert({
                                "company_id": cid, "contact_id": r.data[0]["id"],
                                "field_name": "contacts.email", "source_url": src,
                                "scraper_tier": 2,
                                "raw_excerpt": f"new DM {namn}={email}"[:500],
                                "critic_note": "apply_dm_results.py — new verified DM",
                            }).execute()
                    stats["contacts_inserted"] += 1
                    console.print(f"[cyan]NEW DM {namn[:26]:26} → {email}[/]")
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]{namn}: {exc}[/]")
                stats["errors"] += 1

    table = Table(title="Apply DM email results")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k, v in stats.items():
        table.add_row(k, str(v))
    console.print(table)
    if dry_run:
        console.print("[bold yellow]DRY-RUN — no DB writes[/]")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    main(p.parse_args().dry_run)
