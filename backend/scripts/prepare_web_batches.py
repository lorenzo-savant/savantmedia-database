"""
Prepara batch JSON delle aziende che richiedono ricerca web (domain/tel/email
mancanti) da distribuire ai subagent WebSearch+WebFetch.

Output:
    backend/data/web_batch_001.json ... web_batch_NNN.json
    backend/data/web_batches_index.json

Schema per entry (slim — solo ciò che serve al subagent):
    {id, foretagsnamn, organisationsnummer, stad, sni_branscher,
     have_domain, need:[...]}

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.prepare_web_batches --size 22
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
    key = os.environ["SUPABASE_SECRET_KEY"]
    return create_client(url, key)


def main(size: int) -> None:
    sb = _sb()
    rows: list[dict] = []
    start = 0
    page = 1000
    while True:
        resp = (
            sb.table("companies")
            .select("id, foretagsnamn, organisationsnummer, stad, "
                    "sni_branscher, domain, reception_telefon, email_info, "
                    "antal_anstallda")
            .eq("arkiverad", False)
            .order("foretagsnamn")
            .range(start, start + page - 1)
            .execute()
        )
        if not resp.data:
            break
        rows.extend(resp.data)
        if len(resp.data) < page:
            break
        start += page

    incomplete = []
    for c in rows:
        dom = (c.get("domain") or "").strip()
        tel = (c.get("reception_telefon") or "").strip()
        em = (c.get("email_info") or "").strip()
        need = []
        if not dom:
            need.append("domain")
        if not tel:
            need.append("reception_telefon")
        if not em:
            need.append("email_info")
        if c.get("antal_anstallda") is None:
            need.append("antal_anstallda")
        # Solo aziende che mancano almeno un campo web (domain/tel/email)
        if not (dom and tel and em):
            incomplete.append({
                "id": c["id"],
                "foretagsnamn": c["foretagsnamn"],
                "organisationsnummer": c.get("organisationsnummer") or "",
                "stad": c.get("stad") or "",
                "sni_branscher": c.get("sni_branscher") or "",
                "have_domain": dom or None,
                "need": need,
            })

    console.print(f"[bold cyan]Companies needing web research: {len(incomplete)}[/]")
    DATA.mkdir(parents=True, exist_ok=True)

    # Pulisci batch/result precedenti per evitare confusione
    for old in DATA.glob("web_batch_*.json"):
        old.unlink()

    index = []
    n = 0
    for i in range(0, len(incomplete), size):
        n += 1
        chunk = incomplete[i:i + size]
        path = DATA / f"web_batch_{n:03d}.json"
        path.write_text(json.dumps(chunk, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        index.append({"batch": n, "file": path.name, "count": len(chunk)})
    (DATA / "web_batches_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    console.print(f"[green]Wrote {n} batches (size {size}) → web_batch_001..{n:03d}[/]")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--size", type=int, default=22)
    main(p.parse_args().size)
