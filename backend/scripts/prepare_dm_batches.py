"""
Prepara batch JSON per la caccia alle email VERIFICATE dei decision maker (DM).

Seleziona le aziende attive che NON hanno ancora un DM con email, priorità a
quelle con `domain` (resa più alta: serve un dominio per verificare @dominio).
Per ognuna include i contatti già noti (nome+ruolo) così il subagent può
trovare la loro email, oppure individuare il DM se manca.

Output:
    backend/data/dm_batch_001.json ... (lista) + dm_batches_index.json

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.prepare_dm_batches --size 18 --only-domain
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from dotenv import load_dotenv
from rich.console import Console
from supabase import create_client

console = Console()
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _sb():
    load_dotenv(ROOT.parent / ".env")
    load_dotenv(ROOT.parent / ".env.local")
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get(
        "NEXT_PUBLIC_SUPABASE_URL"
    )
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


def main(size: int, only_domain: bool, no_domain: bool,
         recent_hours: float | None = None) -> None:
    sb = _sb()
    cutoff = None
    if recent_hours:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=recent_hours)).isoformat()
    companies = [c for c in _fetch_all(
        sb, "companies",
        "id, foretagsnamn, organisationsnummer, stad, domain, sni_branscher, "
        "arkiverad, skapad_datum"
    ) if not c.get("arkiverad")
        and (not cutoff or (c.get("skapad_datum") or "") >= cutoff)]
    contacts = _fetch_all(
        sb, "contacts", "id, company_id, namn, roll, email, is_dm"
    )
    by_company: dict[str, list] = {}
    for k in contacts:
        by_company.setdefault(k["company_id"], []).append(k)

    targets = []
    for c in companies:
        ks = by_company.get(c["id"], [])
        has_dm_email = any(
            k.get("is_dm") and (k.get("email") or "").strip() for k in ks
        )
        if has_dm_email:
            continue
        domain = (c.get("domain") or "").strip()
        if only_domain and not domain:
            continue
        if no_domain and domain:
            continue
        known = [
            {"namn": k["namn"], "roll": k.get("roll") or "",
             "has_email": bool((k.get("email") or "").strip())}
            for k in ks if (k.get("namn") or "").strip()
        ]
        targets.append({
            "id": c["id"],
            "foretagsnamn": c["foretagsnamn"],
            "organisationsnummer": c.get("organisationsnummer") or "",
            "stad": c.get("stad") or "",
            "domain": domain or None,
            "sni_branscher": c.get("sni_branscher") or "",
            "known_people": known,
            "need_dm": len([p for p in known]) == 0,
        })

    # priorità: con dominio prima, poi con un DM già noto (solo email da trovare)
    targets.sort(key=lambda t: (t["domain"] is None, t["need_dm"],
                                t["foretagsnamn"]))
    console.print(f"[bold cyan]Companies needing a verified DM email: "
                  f"{len(targets)} (only_domain={only_domain})[/]")

    DATA.mkdir(parents=True, exist_ok=True)
    for old in DATA.glob("dm_batch_*.json"):
        old.unlink()
    index, n = [], 0
    for i in range(0, len(targets), size):
        n += 1
        chunk = targets[i:i + size]
        path = DATA / f"dm_batch_{n:03d}.json"
        path.write_text(json.dumps(chunk, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        index.append({"batch": n, "file": path.name, "count": len(chunk)})
    (DATA / "dm_batches_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]Wrote {n} batches (size {size})[/]")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--size", type=int, default=18)
    p.add_argument("--only-domain", action="store_true",
                   help="solo aziende con dominio noto (resa alta)")
    p.add_argument("--no-domain", action="store_true",
                   help="solo aziende SENZA dominio noto (resa bassa, va trovato il sito)")
    p.add_argument("--recent-hours", type=float, default=None,
                   help="solo aziende create nelle ultime N ore (es. import recente)")
    a = p.parse_args()
    main(a.size, a.only_domain, a.no_domain, a.recent_hours)
