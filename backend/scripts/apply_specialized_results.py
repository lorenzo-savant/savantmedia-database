"""
Applica al DB i risultati dei 10 subagent specializzati per campo.

Legge `backend/data/specialized_result_*.json` e fa UPDATE/INSERT
mirati per campo. Idempotente — skippa campi già popolati.

File processati:
- specialized_result_domain        → companies.domain
- specialized_result_phone_co      → companies.reception_telefon
- specialized_result_email_info    → companies.email_info
- specialized_result_employees     → companies.antal_anstallda
- specialized_result_vd_email_1/2  → contacts.email
- specialized_result_vd_linkedin_1/2 → contacts.linkedin_url
- specialized_result_vd_phone      → contacts.telefon
- specialized_result_new_dm        → INSERT new contacts

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.apply_specialized_results
"""

from __future__ import annotations

import argparse
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
DATA = Path(__file__).resolve().parent.parent / "data"


def _supabase() -> Client:
    load_dotenv()
    return create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]
    )


def _load(name: str) -> list[dict]:
    p = DATA / name
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")) or []
    except Exception as exc:
        console.print(f"[red]Failed to load {name}: {exc}[/]")
        return []


def _norm_domain(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().lower()
    s = s.replace("https://", "").replace("http://", "").lstrip("/")
    if s.startswith("www."):
        s = s[4:]
    s = s.split("/")[0].split(":")[0]
    return s or None


def _norm_phone(s: str | None) -> str | None:
    if not s:
        return None
    digits = re.sub(r"[^\d+]", "", s)
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


def _audit(sb: Client, **kw: Any) -> None:
    sb.table("sources").insert(kw).execute()


# ── Apply per campo ──────────────────────────────────────────────────────────


def apply_company_field(
    sb: Client, file: str, field: str, normalizer=None, tier: int = 1
) -> dict[str, int]:
    stats = {"checked": 0, "updated": 0, "skipped": 0, "errors": 0}
    entries = _load(file)
    if not entries:
        return stats
    ids = [e.get("id") for e in entries if e.get("id")]
    if not ids:
        return stats
    cur = sb.table("companies").select(f"id, {field}").in_("id", ids).execute()
    by_id = {c["id"]: c for c in cur.data}
    for e in entries:
        stats["checked"] += 1
        cid = e.get("id")
        val = e.get(field)
        if cid is None or val in (None, "", "null"):
            continue
        if normalizer:
            val = normalizer(val)
        if val is None or val == "":
            continue
        comp = by_id.get(cid)
        if not comp:
            continue
        existing = comp.get(field)
        if isinstance(existing, str) and existing.strip():
            stats["skipped"] += 1
            continue
        if existing is not None and not isinstance(existing, str):
            stats["skipped"] += 1
            continue
        try:
            sb.table("companies").update({field: val}).eq("id", cid).execute()
            _audit(sb, company_id=cid, field_name=f"companies.{field}",
                   source_url=e.get("source_url"), scraper_tier=tier,
                   raw_excerpt=f"specialized_subagent value={val}",
                   critic_note=f"apply_specialized_results.py / {file}")
            stats["updated"] += 1
        except Exception as exc:
            console.print(f"[red]{file} {cid}: {exc}[/]")
            stats["errors"] += 1
    return stats


def apply_contact_field(
    sb: Client, file: str, field: str, normalizer=None, tier: int = 1
) -> dict[str, int]:
    stats = {"checked": 0, "updated": 0, "skipped": 0, "errors": 0}
    entries = _load(file)
    if not entries:
        return stats
    ids = [e.get("id") for e in entries if e.get("id")]
    if not ids:
        return stats
    cur = sb.table("contacts").select(
        f"id, company_id, {field}"
    ).in_("id", ids).execute()
    by_id = {c["id"]: c for c in cur.data}
    for e in entries:
        stats["checked"] += 1
        cid = e.get("id")
        val = e.get(field)
        if cid is None or val in (None, "", "null"):
            continue
        if normalizer:
            val = normalizer(val)
        if val is None or val == "":
            continue
        contact = by_id.get(cid)
        if not contact:
            continue
        existing = contact.get(field)
        if isinstance(existing, str) and existing.strip():
            stats["skipped"] += 1
            continue
        try:
            payload: dict[str, Any] = {field: val}
            # Mark verified when we update via specialized search
            if field == "email":
                payload["verifierad"] = True
                payload["verifieringsmetod"] = "serpapi"
                payload["verifieringskalla"] = e.get("source_url") or ""
                payload["verifierat_av"] = (
                    "agent:specialized_subagent-2026-06-02"
                )
                payload["verifierat_datum"] = datetime.now(
                    timezone.utc
                ).isoformat()
            sb.table("contacts").update(payload).eq("id", cid).execute()
            _audit(sb, company_id=contact.get("company_id"), contact_id=cid,
                   field_name=f"contacts.{field}",
                   source_url=e.get("source_url"), scraper_tier=tier,
                   raw_excerpt=f"specialized_subagent value={val}",
                   critic_note=f"apply_specialized_results.py / {file}")
            stats["updated"] += 1
        except Exception as exc:
            console.print(f"[red]{file} {cid}: {exc}[/]")
            stats["errors"] += 1
    return stats


def apply_new_dm(sb: Client) -> dict[str, int]:
    """Insert nuovi DM contacts solo se passano validatori."""
    stats = {"checked": 0, "inserted": 0, "skipped": 0, "errors": 0}
    entries = _load("specialized_result_new_dm.json")
    bad_tokens = (
        "Aktiebolag", "Handelsbolag", "Holding", "Group", "Partners",
        "Stiftelsen", "Föreningen", "Ratsit", "Dagbladet", "Nyheter",
    )
    for e in entries:
        company_id = e.get("company_id")
        contacts = e.get("contacts") or []
        if not company_id or not contacts:
            continue
        for c in contacts:
            stats["checked"] += 1
            namn = (c.get("namn") or "").strip()
            roll = (c.get("roll") or "").strip()
            if not namn or "\n" in namn:
                stats["skipped"] += 1
                continue
            if not (5 <= len(namn) <= 50):
                stats["skipped"] += 1
                continue
            tokens = namn.split()
            if not (2 <= len(tokens) <= 4):
                stats["skipped"] += 1
                continue
            if any(t in namn for t in bad_tokens):
                stats["skipped"] += 1
                continue
            # Dedup
            existing = sb.table("contacts").select("id").eq(
                "company_id", company_id
            ).eq("namn", namn).limit(1).execute()
            if existing.data:
                stats["skipped"] += 1
                continue
            payload = {
                "company_id": company_id,
                "namn": namn,
                "roll": roll,
                "email": (c.get("email") or "").lower() or "",
                "linkedin_url": c.get("linkedin_url") or "",
                "is_dm": True,
                "verifierad": bool(c.get("email") or c.get("linkedin_url")),
                "verifieringsmetod": "serpapi",
                "verifierat_av": "agent:specialized_new_dm-2026-06-02",
                "verifierat_datum": datetime.now(
                    timezone.utc).isoformat() if (
                    c.get("email") or c.get("linkedin_url")) else None,
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            try:
                r = sb.table("contacts").insert(payload).execute()
                if r.data:
                    _audit(
                        sb, company_id=company_id,
                        contact_id=r.data[0]["id"],
                        field_name="contacts.namn",
                        source_url=None, scraper_tier=1,
                        raw_excerpt=f"new DM: {namn} ({roll})",
                        critic_note=(
                            "apply_specialized_results.py / new_dm subagent"
                        ),
                    )
                stats["inserted"] += 1
            except Exception as exc:
                console.print(f"[red]new_dm {namn}: {exc}[/]")
                stats["errors"] += 1
    return stats


def main() -> None:
    sb = _supabase()
    summary: list[tuple[str, dict]] = []

    summary.append(("domain", apply_company_field(
        sb, "specialized_result_domain.json", "domain",
        normalizer=_norm_domain
    )))
    summary.append(("reception_telefon", apply_company_field(
        sb, "specialized_result_phone_co.json", "reception_telefon",
        normalizer=_norm_phone
    )))
    summary.append(("email_info", apply_company_field(
        sb, "specialized_result_email_info.json", "email_info",
        normalizer=lambda s: s.strip().lower() if s else None
    )))
    summary.append(("antal_anstallda", apply_company_field(
        sb, "specialized_result_employees.json", "antal_anstallda",
        normalizer=lambda v: int(v) if isinstance(v, (int, str)) and str(v).isdigit() else None
    )))

    summary.append(("vd_email_1", apply_contact_field(
        sb, "specialized_result_vd_email_1.json", "email",
        normalizer=lambda s: s.strip().lower() if s else None
    )))
    summary.append(("vd_email_2", apply_contact_field(
        sb, "specialized_result_vd_email_2.json", "email",
        normalizer=lambda s: s.strip().lower() if s else None
    )))
    summary.append(("vd_linkedin_1", apply_contact_field(
        sb, "specialized_result_vd_linkedin_1.json", "linkedin_url",
        normalizer=lambda s: s.strip() if s else None
    )))
    summary.append(("vd_linkedin_2", apply_contact_field(
        sb, "specialized_result_vd_linkedin_2.json", "linkedin_url",
        normalizer=lambda s: s.strip() if s else None
    )))
    summary.append(("vd_phone", apply_contact_field(
        sb, "specialized_result_vd_phone.json", "telefon",
        normalizer=_norm_phone
    )))

    new_dm_stats = apply_new_dm(sb)
    summary.append(("new_dm", new_dm_stats))

    table = Table(title="Apply specialized subagent results")
    table.add_column("Field", style="cyan")
    for k in ("checked", "updated", "inserted", "skipped", "errors"):
        table.add_column(k, justify="right")
    for name, s in summary:
        table.add_row(
            name,
            str(s.get("checked", "-")),
            str(s.get("updated", "-")),
            str(s.get("inserted", "-")),
            str(s.get("skipped", "-")),
            str(s.get("errors", "-")),
        )
    console.print(table)


if __name__ == "__main__":
    main()
