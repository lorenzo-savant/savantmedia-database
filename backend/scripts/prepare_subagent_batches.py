"""
Prepara N batch di aziende incomplete (no domain/phone/email) come JSON
da distribuire ai subagent WebSearch.

Output:
    backend/data/subagent_batch_01.json ... subagent_batch_NN.json
    backend/data/subagent_batches_index.json  (sommario)

Usage:
    .venv/Scripts/python.exe -m scripts.prepare_subagent_batches --batches 10
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
DATA = Path(__file__).resolve().parent.parent / "data"


def main(n_batches: int) -> None:
    load_dotenv()
    sb = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]
    )
    resp = (
        sb.table("companies")
        .select(
            "id, foretagsnamn, organisationsnummer, stad, domain, "
            "reception_telefon, email_info, sni_branscher"
        )
        .eq("arkiverad", False)
        .order("foretagsnamn")
        .execute()
    )
    incomplete = [
        c for c in resp.data
        if not (c.get("domain") or "").strip()
        or not (c.get("reception_telefon") or "").strip()
        or not (c.get("email_info") or "").strip()
    ]
    console.print(f"[bold cyan]Incomplete companies: {len(incomplete)}[/]")

    batch_size = (len(incomplete) + n_batches - 1) // n_batches
    DATA.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    for i in range(n_batches):
        chunk = incomplete[i * batch_size : (i + 1) * batch_size]
        if not chunk:
            continue
        path = DATA / f"subagent_batch_{i+1:02d}.json"
        # Slim payload — only fields the subagent needs
        out = [
            {
                "id": c["id"],
                "foretagsnamn": c["foretagsnamn"],
                "organisationsnummer": c.get("organisationsnummer") or "",
                "stad": c.get("stad") or "",
                "sni_branscher": c.get("sni_branscher") or "",
                "missing": [
                    f for f, v in (
                        ("domain", c.get("domain")),
                        ("reception_telefon", c.get("reception_telefon")),
                        ("email_info", c.get("email_info")),
                    ) if not (v or "").strip()
                ],
            }
            for c in chunk
        ]
        path.write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        index.append({"batch": i + 1, "file": path.name, "count": len(out)})
        console.print(f"[green]Wrote {path.name} ({len(out)} entries)[/]")

    (DATA / "subagent_batches_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    console.print(f"[bold]Total batches: {len(index)}[/]")


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batches", type=int, default=10)
    args = p.parse_args()
    main(args.batches)


if __name__ == "__main__":
    _cli()
