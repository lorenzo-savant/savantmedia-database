"""
Applica i risultati JSON prodotti dai subagent WebSearch+WebFetch al DB.

Cerca tutti i file `backend/data/subagent_*.json` e per ogni entry:
- Se `domain` è non-null e l'azienda non ha già un domain → UPDATE companies
- Se `reception_telefon`/`email_info` sono non-null → UPDATE companies
- Se `vd_namn` è non-null → INSERT contacts (is_dm=true)
- INSERT audit row per ogni campo aggiornato in `public.sources` (tier=2)

Idempotente: skippa silenziosamente i campi già popolati.

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.apply_subagent_results
    .venv/Scripts/python.exe -m scripts.apply_subagent_results --dry-run
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

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _supabase() -> Client:
    load_dotenv()
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )


def _normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("0") and not digits.startswith("00"):
        digits = "+46" + digits[1:]
    if digits.startswith("+46") and len(digits) >= 10:
        body = digits[3:]
        if body.startswith("7"):
            parts = [body[:2], body[2:5], body[5:7], body[7:]]
        else:
            parts = [body[:1], body[1:4], body[4:6], body[6:]]
        return "+46 " + " ".join(p for p in parts if p)
    return digits or None


def _normalize_domain(raw: str | None) -> str | None:
    if not raw:
        return None
    d = raw.strip().lower()
    d = d.replace("https://", "").replace("http://", "")
    d = d.lstrip("/")
    if d.startswith("www."):
        d = d[4:]
    d = d.split("/")[0].split(":")[0]
    return d or None


def _load_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    patterns = ["subagent_result_*.json", "subagent_a.json",
                "subagent_b.json", "subagent_c.json", "subagent_d.json"]
    seen: set[str] = set()
    paths: list[str] = []
    for pat in patterns:
        for p in sorted(glob.glob(str(DATA_DIR / pat))):
            if p not in seen:
                seen.add(p)
                paths.append(p)
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                entries.extend(data)
                console.print(f"[dim]Loaded {len(data)} entries from {path}[/]")
        except Exception as exc:
            console.print(f"[red]Failed to load {path}: {exc}[/]")
    return entries


def _apply(
    sb: Client, entries: list[dict[str, Any]], dry_run: bool
) -> dict[str, int]:
    stats = {
        "checked": 0,
        "skipped_empty": 0,
        "skipped_already_set": 0,
        "companies_updated": 0,
        "fields_updated": 0,
        "contacts_inserted": 0,
        "errors": 0,
    }

    ids = [e["id"] for e in entries if e.get("id")]
    if not ids:
        return stats

    # Fetch current state for all companies in one shot
    current = (
        sb.table("companies")
        .select(
            "id, foretagsnamn, domain, reception_telefon, email_info"
        )
        .in_("id", ids)
        .execute()
    )
    by_id = {c["id"]: c for c in current.data}

    for entry in entries:
        cid = entry.get("id")
        if not cid or cid not in by_id:
            continue
        stats["checked"] += 1
        company = by_id[cid]

        domain = _normalize_domain(entry.get("domain"))
        tel = _normalize_phone(entry.get("reception_telefon"))
        em = (entry.get("email_info") or "").strip().lower() or None
        vd = (entry.get("vd_namn") or "").strip() or None
        source_url = entry.get("source_url") or (
            f"https://{domain}" if domain else None
        )

        if not (domain or tel or em or vd):
            stats["skipped_empty"] += 1
            continue

        update: dict[str, Any] = {}
        if domain and not (company.get("domain") or "").strip():
            update["domain"] = domain
        if tel and not (company.get("reception_telefon") or "").strip():
            update["reception_telefon"] = tel
        if em and not (company.get("email_info") or "").strip():
            update["email_info"] = em

        if update:
            if not dry_run:
                try:
                    sb.table("companies").update(update).eq(
                        "id", cid
                    ).execute()
                except Exception as exc:
                    console.print(
                        f"[red]update {company.get('foretagsnamn')}: {exc}[/]"
                    )
                    stats["errors"] += 1
                    continue
                for field in update:
                    sb.table("sources").insert(
                        {
                            "company_id": cid,
                            "field_name": f"companies.{field}",
                            "source_url": source_url,
                            "scraper_tier": 2,
                            "raw_excerpt": f"subagent finding: {field}={update[field]}",
                            "critic_note": (
                                "apply_subagent_results.py — "
                                "WebSearch+WebFetch by background agent"
                            ),
                        }
                    ).execute()
            stats["companies_updated"] += 1
            stats["fields_updated"] += len(update)
            console.print(
                f"[green]OK {company.get('foretagsnamn','?')[:38]:38} "
                f"→ {','.join(update.keys())}[/]"
            )
        elif domain or tel or em:
            stats["skipped_already_set"] += 1

        # Contact (VD)
        if vd:
            # idempotence: skip if a contact with that name exists for company
            try:
                existing = (
                    sb.table("contacts")
                    .select("id")
                    .eq("company_id", cid)
                    .eq("namn", vd)
                    .limit(1)
                    .execute()
                )
                if not existing.data:
                    payload = {
                        "company_id": cid,
                        "namn": vd,
                        "roll": "VD",
                        "is_dm": True,
                        "verifierad": True,
                        "verifieringsmetod": "foretagswebbplats",
                        "verifieringskalla": source_url,
                        "verifierat_av": "subagent:websearch-2026-05-28",
                        "verifierat_datum": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }
                    if not dry_run:
                        r = sb.table("contacts").insert(payload).execute()
                        if r.data:
                            new_cid = r.data[0]["id"]
                            sb.table("sources").insert(
                                {
                                    "company_id": cid,
                                    "contact_id": new_cid,
                                    "field_name": "contacts.namn",
                                    "source_url": source_url,
                                    "scraper_tier": 2,
                                    "raw_excerpt": (
                                        f"subagent finding: VD={vd}"
                                    ),
                                    "critic_note": (
                                        "apply_subagent_results.py — "
                                        "VD identified by subagent"
                                    ),
                                }
                            ).execute()
                    stats["contacts_inserted"] += 1
            except Exception as exc:
                console.print(f"[red]contact {vd}: {exc}[/]")
                stats["errors"] += 1

    return stats


def main(dry_run: bool) -> None:
    entries = _load_entries()
    if not entries:
        console.print("[yellow]No subagent_*.json files found in data/[/]")
        return
    console.print(f"[bold cyan]Loaded {len(entries)} total entries[/]")
    sb = _supabase()
    stats = _apply(sb, entries, dry_run=dry_run)

    table = Table(title="Apply subagent results")
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
