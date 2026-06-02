"""
T2/T4 enricher contro `ratsit.se` per aziende senza dominio.

Vault lesson (`docs/ARCHITECTURE.md` §8 + `scrapers/_allabolag_strategy.py`):
allabolag `/foretag/<orgnr>` è una React SPA → ignorabile. ratsit.se invece
serve pagine SSR pubbliche con Hemsida, Telefon, E-post, Styrelse → adatta a
T2 (httpx) con T4 (Playwright stealth) come fallback se WAF blocca T2.

Per ogni org.nr in `companies` senza domain:
1. Fetch `https://www.ratsit.se/<orgnr_no_dash>` con T2 httpbs.
2. Se 4xx/5xx → fallback T4 stealth_fetch sulla stessa URL.
3. Estrai con regex/BS4:
   - `domain` (Hemsida)
   - `reception_telefon` (Telefon)
   - `email_info` (E-post)
   - `styrelse[]` (Styrelseledamöter, VD)
4. UPDATE companies + INSERT contatti DM + audit `sources`.

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_ratsit_t4 --limit 20
    .venv/Scripts/python.exe -m scripts.enrich_ratsit_t4 --limit 50 --dry-run
    .venv/Scripts/python.exe -m scripts.enrich_ratsit_t4 --offset 0 --limit 80 --workers 2
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

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

from scrapers.httpbs import fetch_and_extract
from scrapers.playwright_t4 import stealth_fetch

console = Console()


# ── Regex ────────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-åäöÅÄÖ]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?:\+46[\s\-]?|0)(?:\d[\s\-\(\)]?){6,12}\d")
# URL su qualsiasi TLD comune, escludendo ratsit/allabolag/social
_URL_RE = re.compile(
    r"https?://([a-z0-9\-]+(?:\.[a-z0-9\-]+)+)", re.IGNORECASE
)
_BLOCK_HOSTS = {
    "ratsit.se", "www.ratsit.se", "allabolag.se", "www.allabolag.se",
    "bolagsfakta.se", "linkedin.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "youtube.com", "google.com", "google.se",
    "hitta.se", "eniro.se", "merinfo.se",
}

# Hint per VD/styrelse
_DM_ROLE_HINTS = (
    "verkställande direktör",
    "vd ",
    "ceo",
    "styrelseordförande",
    "ordförande",
    "styrelseledamot",
    "ledamot",
)


@dataclass
class CompanyRow:
    id: str
    foretagsnamn: str
    organisationsnummer: str
    domain: str | None


@dataclass
class Enrichment:
    domain: str | None = None
    reception_telefon: str | None = None
    email_info: str | None = None
    dm_contacts: list[dict[str, str]] = field(default_factory=list)
    source_url: str | None = None
    raw_excerpt: str | None = None
    tier_used: int = 2

    @property
    def has_anything(self) -> bool:
        return bool(
            self.domain or self.reception_telefon
            or self.email_info or self.dm_contacts
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _supabase() -> Client:
    load_dotenv()
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )


def _orgnr_no_dash(s: str) -> str:
    return re.sub(r"[^\d]", "", s)


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


def _extract_domain_from_text(text: str) -> str | None:
    """Trova il primo URL non-blacklisted nel testo (Hemsida)."""
    for m in _URL_RE.finditer(text):
        host = m.group(1).lower().lstrip(".")
        if host.startswith("www."):
            host = host[4:]
        if host in _BLOCK_HOSTS:
            continue
        if any(host.endswith("." + b) for b in _BLOCK_HOSTS):
            continue
        return host
    return None


def _parse_styrelse(text: str) -> list[dict[str, str]]:
    """Estrae nominativi dello Styrelse / VD dalla pagina ratsit.

    Pattern tipico ratsit:
        Befattningar
        Verkställande direktör
        Erik Andersson (1965)
        Styrelseordförande
        Maria Svensson (1970)
        Styrelseledamot
        ...
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    current_role: str | None = None
    for ln in lines:
        ll = ln.lower()
        # Detect role labels
        if "verkställande direktör" in ll or ll == "vd":
            current_role = "VD"
            continue
        if "styrelseordförande" in ll or "ordförande" in ll and len(ll) < 30:
            current_role = "Styrelseordförande"
            continue
        if "styrelseledamot" in ll or ll == "ledamot":
            current_role = "Styrelseledamot"
            continue
        if current_role and re.match(
            r"^[A-ZÅÄÖ][a-zåäö'\-]+(?: [A-ZÅÄÖ][a-zåäö'\-]+){1,3}"
            r"(?:\s*\(\d{4}\))?$",
            ln,
        ):
            name = re.sub(r"\s*\(\d{4}\)\s*$", "", ln).strip()
            key = name.lower()
            if key in seen or len(name) > 60:
                continue
            seen.add(key)
            out.append({
                "namn": name,
                "roll": current_role,
                "is_dm": current_role in ("VD", "Styrelseordförande"),
            })  # type: ignore[arg-type]
            if len(out) >= 8:
                break
    return out


def _extract_evidence(text: str, source_url: str) -> Enrichment:
    enr = Enrichment(source_url=source_url, raw_excerpt=text[:500])
    if not text:
        return enr

    # Domain
    enr.domain = _extract_domain_from_text(text)

    # Phone — preferisci quello vicino a "Telefon"
    m = re.search(r"Telefon[:\s]*([+\d\s\-\(\)]{7,})", text, re.IGNORECASE)
    if m:
        enr.reception_telefon = _normalize_phone(m.group(1))
    else:
        ph = _PHONE_RE.search(text)
        if ph:
            enr.reception_telefon = _normalize_phone(ph.group(0))

    # Email — preferisci quello vicino a "E-post"
    m = re.search(r"E-?post[:\s]*([^\s,;]+@[^\s,;]+)", text, re.IGNORECASE)
    if m:
        enr.email_info = m.group(1).lower()
    else:
        em = _EMAIL_RE.search(text)
        if em:
            enr.email_info = em.group(0).lower()

    # Styrelse → DM contacts
    for person in _parse_styrelse(text):
        enr.dm_contacts.append({
            "namn": person["namn"],
            "roll": person["roll"],
            "email": "",
            "telefon": "",
        })
    return enr


async def _fetch_company(orgnr: str) -> Enrichment:
    """T2 first → T4 fallback se T2 fallisce."""
    url = f"https://www.ratsit.se/{orgnr}"
    # T2
    try:
        res = await fetch_and_extract(url, timeout=20.0)
        if res.ok and res.content_text:
            enr = _extract_evidence(res.content_text, url)
            enr.tier_used = 2
            return enr
    except Exception:
        pass

    # T4 fallback
    try:
        res = await stealth_fetch(
            url,
            storage_state_key="ratsit",
            wait_for_selector="text=Org.nr",
            timeout=30,
        )
        if res.ok and res.content_text:
            enr = _extract_evidence(res.content_text, url)
            enr.tier_used = 4
            return enr
    except Exception:
        pass

    return Enrichment(source_url=url, tier_used=4)


# ── DB ───────────────────────────────────────────────────────────────────────

def _fetch_targets(
    sb: Client, offset: int, limit: int
) -> list[CompanyRow]:
    resp = (
        sb.table("companies")
        .select("id, foretagsnamn, organisationsnummer, domain")
        .eq("arkiverad", False)
        .or_("domain.is.null,domain.eq.")
        .order("foretagsnamn")
        .range(offset, offset + limit - 1)
        .execute()
    )
    return [
        CompanyRow(
            id=r["id"],
            foretagsnamn=r["foretagsnamn"],
            organisationsnummer=r.get("organisationsnummer") or "",
            domain=(r.get("domain") or None) or None,
        )
        for r in resp.data
    ]


def _persist(
    sb: Client, c: CompanyRow, enr: Enrichment
) -> tuple[int, int]:
    update: dict[str, Any] = {}
    if enr.domain and not c.domain:
        update["domain"] = enr.domain
    if enr.reception_telefon:
        update["reception_telefon"] = enr.reception_telefon
    if enr.email_info:
        update["email_info"] = enr.email_info

    fields_updated = 0
    if update:
        # Only set fields that are currently empty — read live
        live = (
            sb.table("companies")
            .select("domain, reception_telefon, email_info")
            .eq("id", c.id).limit(1).execute()
        )
        live_row = live.data[0] if live.data else {}
        filtered = {
            k: v for k, v in update.items()
            if not (live_row.get(k) or "").strip()
        }
        if filtered:
            sb.table("companies").update(filtered).eq("id", c.id).execute()
            fields_updated = len(filtered)
            for field_name, _val in filtered.items():
                sb.table("sources").insert(
                    {
                        "company_id": c.id,
                        "field_name": f"companies.{field_name}",
                        "source_url": enr.source_url,
                        "scraper_tier": enr.tier_used,
                        "raw_excerpt": (enr.raw_excerpt or "")[:500],
                        "critic_note": (
                            f"enrich_ratsit_t4.py T{enr.tier_used}"
                        ),
                    }
                ).execute()

    contacts_inserted = 0
    for dm in enr.dm_contacts:
        existing = (
            sb.table("contacts")
            .select("id")
            .eq("company_id", c.id)
            .eq("namn", dm["namn"])
            .limit(1).execute()
        )
        if existing.data:
            continue
        payload = {
            "company_id": c.id,
            "namn": dm["namn"],
            "roll": dm["roll"],
            "is_dm": dm["roll"] in ("VD", "Styrelseordförande"),
            "verifierad": True,
            "verifieringsmetod": "annan",
            "verifieringskalla": enr.source_url,
            "verifierat_av": f"agent:enrich_ratsit_t4-tier{enr.tier_used}",
            "verifierat_datum": datetime.now(timezone.utc).isoformat(),
        }
        r = sb.table("contacts").insert(payload).execute()
        if r.data:
            sb.table("sources").insert(
                {
                    "company_id": c.id,
                    "contact_id": r.data[0]["id"],
                    "field_name": "contacts.namn",
                    "source_url": enr.source_url,
                    "scraper_tier": enr.tier_used,
                    "raw_excerpt": (enr.raw_excerpt or "")[:500],
                    "critic_note": f"enrich_ratsit_t4.py — {dm['roll']}",
                }
            ).execute()
            contacts_inserted += 1
    return fields_updated, contacts_inserted


# ── Orchestration ────────────────────────────────────────────────────────────


async def _worker(
    name: str,
    queue: asyncio.Queue[CompanyRow],
    sb: Client,
    dry_run: bool,
    stats: dict[str, int],
) -> None:
    while True:
        try:
            c = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        orgnr = _orgnr_no_dash(c.organisationsnummer)
        if not orgnr:
            stats["skipped_no_orgnr"] += 1
            queue.task_done()
            continue
        try:
            enr = await _fetch_company(orgnr)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red][{name}] ERR {c.foretagsnamn}: {exc}[/]")
            stats["errors"] += 1
            queue.task_done()
            continue
        if enr.has_anything:
            stats["enriched"] += 1
            dom = enr.domain or "—"
            t = "T" if enr.reception_telefon else "-"
            e = "E" if enr.email_info else "-"
            dm = len(enr.dm_contacts)
            console.print(
                f"[green][{name}] OK {c.foretagsnamn[:34]:34} "
                f"→ {dom[:24]:24} {t}{e} DM:{dm} (T{enr.tier_used})[/]"
            )
            if not dry_run:
                fu, ci = _persist(sb, c, enr)
                stats["fields_updated"] += fu
                stats["contacts_inserted"] += ci
        else:
            stats["empty"] += 1
            console.print(
                f"[dim][{name}] -- {c.foretagsnamn[:34]:34}  (no evidence)[/]"
            )
        queue.task_done()


async def main(
    offset: int, limit: int, workers: int, dry_run: bool
) -> None:
    sb = _supabase()
    targets = _fetch_targets(sb, offset, limit)
    console.print(
        f"[bold cyan]Fetched {len(targets)} no-domain companies "
        f"(offset={offset}, limit={limit}, workers={workers}, dry={dry_run})[/]"
    )
    if not targets:
        return
    queue: asyncio.Queue[CompanyRow] = asyncio.Queue()
    for c in targets:
        queue.put_nowait(c)
    stats = {
        "enriched": 0, "empty": 0, "errors": 0, "skipped_no_orgnr": 0,
        "fields_updated": 0, "contacts_inserted": 0,
    }
    tasks = [
        asyncio.create_task(_worker(f"w{i+1}", queue, sb, dry_run, stats))
        for i in range(workers)
    ]
    await asyncio.gather(*tasks)

    table = Table(title="ratsit T2/T4 enrichment summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k, v in stats.items():
        table.add_row(k, str(v))
    console.print(table)
    if dry_run:
        console.print("[bold yellow]DRY-RUN — no DB writes[/]")


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.offset, args.limit, args.workers, args.dry_run))


if __name__ == "__main__":
    _cli()
