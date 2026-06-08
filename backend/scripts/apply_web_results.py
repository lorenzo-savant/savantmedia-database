"""
Applica al DB i risultati JSON prodotti dai subagent web (web_result_*.json).

Per ogni entry (chiave: id azienda):
  - companies.domain / reception_telefon / email_info / antal_anstallda
    (solo se attualmente vuoti; phone/domain/email normalizzati)
  - storlek_kategori derivata da antal_anstallda se trovato
  - contacts: se vd_namn presente → INSERT contatto is_dm=true (dedup per nome)
    con email/linkedin/telefon se forniti; verifierad=true se email|linkedin
  - sources: audit row per ogni campo (tier 2)

Idempotente: skippa campi già popolati.

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.apply_web_results
    .venv/Scripts/python.exe -m scripts.apply_web_results --dry-run
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
_NAME_BAD = ("aktiebolag", "handelsbolag", "holding", "group", "partners",
             "stiftelsen", "föreningen", "ratsit", "allabolag", "hitta",
             " ab", "kontakt", "info", "verksamhet", "bolag")


def _sb() -> Client:
    load_dotenv(ROOT.parent / ".env")
    load_dotenv(ROOT.parent / ".env.local")
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get(
        "NEXT_PUBLIC_SUPABASE_URL"
    )
    return create_client(url, os.environ["SUPABASE_SECRET_KEY"])


def _norm_domain(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().lower().replace("https://", "").replace("http://", "").lstrip("/")
    if s.startswith("www."):
        s = s[4:]
    s = s.split("/")[0].split(":")[0].strip()
    # plausibilità minima
    if "." not in s or " " in s or len(s) < 4:
        return None
    return s or None


def _norm_phone(s: str | None) -> str | None:
    if not s:
        return None
    digits = re.sub(r"[^\d+]", "", s)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("0") and not digits.startswith("+"):
        digits = "+46" + digits[1:]
    if digits.startswith("+46") and len(digits) >= 10:
        body = digits[3:]
        if body.startswith("7"):
            parts = [body[:2], body[2:5], body[5:7], body[7:]]
        else:
            parts = [body[:1], body[1:4], body[4:6], body[6:]]
        return "+46 " + " ".join(p for p in parts if p)
    return digits if len(digits) >= 6 else None


def _norm_email(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().lower()
    return s if _EMAIL_RE.match(s) else None


def _norm_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if v >= 0 else None
    m = re.search(r"\d+", str(v).replace(" ", ""))
    return int(m.group()) if m else None


def _classify_storlek(n: int | None) -> str | None:
    if n is None or n < 0:
        return None
    return "liten" if n <= 49 else "medel" if n <= 249 else "multinationell"


def _valid_name(namn: str) -> bool:
    if not namn or "\n" in namn:
        return False
    if not (5 <= len(namn) <= 50):
        return False
    toks = namn.split()
    if not (2 <= len(toks) <= 4):
        return False
    low = namn.lower()
    return not any(b in low for b in _NAME_BAD)


def _load() -> list[dict]:
    entries: list[dict] = []
    for p in sorted(glob.glob(str(DATA / "web_result_*.json"))):
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            if isinstance(data, list):
                entries.extend(data)
                console.print(f"[dim]Loaded {len(data)} from {Path(p).name}[/]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]load {p}: {exc}[/]")
    return entries


def _audit(sb: Client, **kw: Any) -> None:
    sb.table("sources").insert(kw).execute()


def main(dry_run: bool) -> None:
    entries = _load()
    if not entries:
        console.print("[yellow]No web_result_*.json files in data/[/]")
        return
    # dedup per id mantenendo la prima entry non vuota
    by_id: dict[str, dict] = {}
    for e in entries:
        cid = e.get("id")
        if not cid:
            continue
        if cid not in by_id:
            by_id[cid] = e
    console.print(f"[bold cyan]{len(by_id)} unique company results[/]")

    sb = _sb()
    ids = list(by_id.keys())
    cur: dict[str, dict] = {}
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        r = sb.table("companies").select(
            "id, foretagsnamn, domain, reception_telefon, email_info, "
            "antal_anstallda, storlek_kategori"
        ).in_("id", chunk).execute()
        for c in r.data:
            cur[c["id"]] = c

    stats = {"checked": 0, "comp_updated": 0, "fields": 0,
             "contacts_inserted": 0, "skipped_set": 0, "errors": 0}

    now = datetime.now(timezone.utc).isoformat()
    for cid, e in by_id.items():
        stats["checked"] += 1
        comp = cur.get(cid)
        if not comp:
            continue
        src = (e.get("source_url") or "").strip() or None
        dom = _norm_domain(e.get("domain"))
        tel = _norm_phone(e.get("reception_telefon"))
        em = _norm_email(e.get("email_info"))
        anst = _norm_int(e.get("antal_anstallda"))

        update: dict[str, Any] = {}
        if dom and not (comp.get("domain") or "").strip():
            update["domain"] = dom
        if tel and not (comp.get("reception_telefon") or "").strip():
            update["reception_telefon"] = tel
        if em and not (comp.get("email_info") or "").strip():
            update["email_info"] = em
        if anst is not None and comp.get("antal_anstallda") is None:
            update["antal_anstallda"] = anst
            if comp.get("storlek_kategori") is None:
                sk = _classify_storlek(anst)
                if sk:
                    update["storlek_kategori"] = sk

        if update:
            if not dry_run:
                try:
                    sb.table("companies").update(update).eq("id", cid).execute()
                    for f in update:
                        if f == "storlek_kategori":
                            continue
                        _audit(sb, company_id=cid, field_name=f"companies.{f}",
                               source_url=src, scraper_tier=2,
                               raw_excerpt=f"web subagent: {f}={update[f]}"[:500],
                               critic_note="apply_web_results.py — WebSearch+WebFetch subagent")
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]update {comp.get('foretagsnamn')}: {exc}[/]")
                    stats["errors"] += 1
                    continue
            stats["comp_updated"] += 1
            stats["fields"] += len([f for f in update if f != "storlek_kategori"])
            console.print(f"[green]OK {comp.get('foretagsnamn','?')[:36]:36} "
                          f"→ {','.join(k for k in update if k!='storlek_kategori')}[/]")

        # VD / contatto
        vd = (e.get("vd_namn") or "").strip()
        if vd and _valid_name(vd):
            vd_email = _norm_email(e.get("vd_email"))
            vd_li = (e.get("vd_linkedin") or "").strip() or None
            if vd_li and "linkedin.com/in" not in vd_li.lower():
                vd_li = None
            vd_tel = _norm_phone(e.get("vd_telefon"))
            try:
                ex = sb.table("contacts").select("id").eq(
                    "company_id", cid).ilike("namn", vd).limit(1).execute()
                if not ex.data:
                    payload = {
                        "company_id": cid, "namn": vd, "roll": "VD",
                        "is_dm": True,
                        "email": vd_email or "",
                        "linkedin_url": vd_li or "",
                        "telefon": vd_tel or "",
                        "verifierad": bool(vd_email or vd_li),
                        "verifieringsmetod": "linkedin" if vd_li else (
                            "foretagswebbplats" if vd_email else "annan"),
                        "verifieringskalla": src or "",
                        "verifierat_av": "subagent:web-2026-06-08",
                    }
                    if payload["verifierad"]:
                        payload["verifierat_datum"] = now
                    if not dry_run:
                        r = sb.table("contacts").insert(payload).execute()
                        if r.data:
                            _audit(sb, company_id=cid, contact_id=r.data[0]["id"],
                                   field_name="contacts.namn", source_url=src,
                                   scraper_tier=2,
                                   raw_excerpt=f"web subagent VD={vd}"[:500],
                                   critic_note="apply_web_results.py — VD by subagent")
                    stats["contacts_inserted"] += 1
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]contact {vd}: {exc}[/]")
                stats["errors"] += 1

    table = Table(title="Apply web subagent results")
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
