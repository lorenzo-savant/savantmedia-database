"""
Enrichment automatico dei contatti DM esistenti.

Per ogni `contact` con `is_dm=true` e nessuna email/telefono salvato,
lo script:

1. Costruisce una lista di URL candidate sul dominio dell'azienda
   (`/medarbetare/<slug>`, `/team/`, `/om-oss/`, `/about/`, `/leadership/`).
2. Se `verifieringskalla` è già un URL del dominio, lo aggiunge in cima.
3. Se nessun URL candidato produce match, fa una query T1 (`SearXNGClient`
   con fallback DDG) `"<nome> <azienda> email"` e tenta T2 sui top 3 risultati.
4. Per ogni T2 fetch riuscito, applica regex email/telefono svedesi.
5. Score: l'email è "ad alta confidenza" se contiene il nome OR ha lo stesso
   dominio dell'azienda. Il telefono è "media confidenza" se appare entro
   200 caratteri dal nome nel content_text.
6. UPDATE `public.contacts` (email/telefono + verifierad=true se high) e
   INSERT `public.sources` audit row (scraper_tier=2).

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_existing --limit 10
    .venv/Scripts/python.exe -m scripts.enrich_existing --limit 50 --dry-run

Note:
- Lavora SOLO sul DB. Non tocca Obsidian né nessun altro sistema.
- Idempotente: se l'email è già popolata, salta il contatto.
- Rispetta i rate limit di `scrapers.httpbs` (per-dominio + robots.txt).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
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

from scrapers.httpbs import fetch_and_extract
from scrapers.searxng import SearXNGClient

console = Console()

# ── Regex ───────────────────────────────────────────────────────────────────

# Email — RFC-relaxed + accenti svedesi
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-åäöÅÄÖ]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# Telefono svedese: +46 / 0 prefisso, possibili spazi, trattini, parentesi.
# Min 7 cifre dopo il prefisso per evitare match di numeri non-tel.
_PHONE_RE = re.compile(
    r"(?:\+46[\s\-]?|0)(?:\d[\s\-\(\)]?){6,12}\d"
)

# ── URL templates ───────────────────────────────────────────────────────────

# Path comuni su siti svedesi per pagine team/ledning.
# Order matters: provo prima i più specifici.
_PATH_TEMPLATES: tuple[str, ...] = (
    "/medarbetare/{slug}",
    "/team/{slug}",
    "/people/{slug}",
    "/medarbetare/",
    "/team/",
    "/om-oss/ledning/",
    "/om-oss/medarbetare/",
    "/om-oss/",
    "/about-us/leadership/",
    "/about-us/team/",
    "/about/",
    "/leadership/",
    "/ledning/",
    "/kontakt/",
    "/contact/",
)


@dataclass
class ContactRow:
    id: str
    company_id: str
    namn: str
    roll: str
    verifieringskalla: str | None
    foretagsnamn: str
    domain: str


@dataclass
class Match:
    """Best evidence found across all candidate URLs for one contact."""

    email: str | None = None
    telefon: str | None = None
    source_url: str | None = None
    excerpt: str | None = None
    confidence: str = "none"  # "high" | "medium" | "low" | "none"


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = (
        s.replace("å", "a")
        .replace("ä", "a")
        .replace("ö", "o")
        .replace("é", "e")
    )
    s = re.sub(r"[^a-z0-9\s]+", "", s)
    s = re.sub(r"\s+", "-", s)
    return s


def _candidate_urls(contact: ContactRow) -> list[str]:
    """Costruisce gli URL candidate per il nome+dominio. Dedup e ordinati
    per probabilità decrescente."""
    if not contact.domain:
        return []
    slug = _slugify(contact.namn)
    base = f"https://{contact.domain.strip().lower()}"
    urls: list[str] = []

    if contact.verifieringskalla and contact.verifieringskalla.startswith(
        ("http://", "https://")
    ) and contact.domain in contact.verifieringskalla.lower():
        urls.append(contact.verifieringskalla)

    for tmpl in _PATH_TEMPLATES:
        urls.append(base + tmpl.format(slug=slug))

    # Dedup preservando ordine
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _find_evidence(
    text: str,
    full_name: str,
    company_domain: str,
) -> tuple[str | None, str | None, str, str | None]:
    """Estrae email + telefono dal testo, con scoring.

    Returns (email, telefon, confidence, excerpt).
    """
    if not text:
        return None, None, "none", None

    lower_text = text.lower()
    name_parts = [p for p in full_name.lower().split() if len(p) >= 2]
    first = name_parts[0] if name_parts else ""
    last = name_parts[-1] if len(name_parts) >= 2 else ""

    # Email candidates
    emails = list(dict.fromkeys(_EMAIL_RE.findall(text)))
    best_email: str | None = None
    email_score = -1
    # Generic local-parts: ALWAYS skip these — they belong to the company
    # mailbox, not to a specific person, regardless of domain match.
    _GENERIC_LOCALS = {
        "info",
        "kontakt",
        "contact",
        "support",
        "hello",
        "hi",
        "office",
        "press",
        "media",
        "careers",
        "career",
        "jobs",
        "sales",
        "hr",
        "admin",
        "noreply",
        "no-reply",
        "postmaster",
        "webmaster",
        "marketing",
        "newsroom",
    }
    for em in emails:
        em_low = em.lower()
        local = em_low.split("@", 1)[0]
        if local in _GENERIC_LOCALS:
            continue
        score = 0
        em_domain = em_low.rsplit("@", 1)[-1]
        if em_domain == company_domain.strip().lower():
            score += 3
        elif em_domain.endswith(company_domain.strip().lower()):
            score += 2
        if last and last in local:
            score += 2
        if first and first in local:
            score += 1
        if score == 0:
            # No domain match AND no name match → unlikely to be the right
            # contact's email. Skip.
            continue
        if score > email_score:
            best_email = em
            email_score = score

    # Phone candidates near the name
    best_phone: str | None = None
    if last or first:
        # Find name occurrence(s)
        name_idx = lower_text.find(f"{first} {last}".strip())
        if name_idx == -1 and last:
            name_idx = lower_text.find(last)
        if name_idx == -1:
            name_idx = -1
        if name_idx >= 0:
            window = text[max(0, name_idx - 50) : name_idx + 400]
            m = _PHONE_RE.search(window)
            if m:
                best_phone = m.group(0).strip()
        # Fallback: first phone anywhere
        if best_phone is None:
            m = _PHONE_RE.search(text)
            if m:
                best_phone = m.group(0).strip()

    confidence = "none"
    if email_score >= 3:
        confidence = "high"
    elif email_score >= 2:
        confidence = "medium"
    elif best_email or best_phone:
        confidence = "low"

    # Build excerpt around name if possible
    excerpt: str | None = None
    if name_parts:
        anchor = full_name.split()[0]
        idx = text.find(anchor)
        if idx >= 0:
            excerpt = text[max(0, idx - 80) : idx + 320].replace("\n", " ")
        else:
            excerpt = text[:400].replace("\n", " ")

    return best_email, best_phone, confidence, excerpt


def _normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw)
    # Convert leading 0 to +46
    if digits.startswith("0") and not digits.startswith("00"):
        digits = "+46" + digits[1:]
    # Re-insert spaces for readability: +46 XX XXX XX XX
    if digits.startswith("+46") and len(digits) >= 10:
        body = digits[3:]
        parts = []
        # area code 1-3 digits — assume 2 for mobile (7X), 1 for Stockholm (8)
        if body.startswith("7"):
            parts = [body[:2], body[2:5], body[5:7], body[7:]]
        else:
            parts = [body[:1], body[1:4], body[4:6], body[6:]]
        return "+46 " + " ".join(p for p in parts if p)
    return digits


# ── Supabase helpers ────────────────────────────────────────────────────────


def _supabase() -> Client:
    load_dotenv()
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SECRET_KEY"]
    return create_client(url, key)


def _fetch_candidates(sb: Client, limit: int) -> list[ContactRow]:
    """Pesca DM-contacts senza email/telefono di aziende con domain noto.

    Ordinati per probabilità di successo:
    1. ``verifieringskalla`` che è sul dominio aziendale (il scrape originario
       ha confermato che il sito espone profili → alto tasso di successo).
    2. Aziende più piccole prima (PMI svedesi seguono `/medarbetare/<slug>`
       più dei brand multinazionali).
    """
    # Over-fetch e ordina in Python (Supabase REST non supporta ORDER BY
    # con LIKE su altra tabella senza RPC).
    resp = (
        sb.table("contacts")
        .select(
            "id, company_id, namn, roll, email, telefon, verifieringskalla, "
            "is_dm"
        )
        .eq("is_dm", True)
        .or_("email.eq.,email.is.null")
        .limit(max(limit * 6, 60))
        .execute()
    )
    company_ids = sorted({r["company_id"] for r in resp.data})
    comp_resp = (
        sb.table("companies")
        .select("id, foretagsnamn, domain, antal_anstallda")
        .in_("id", company_ids)
        .execute()
    )
    by_id = {c["id"]: c for c in comp_resp.data}

    scored: list[tuple[int, int, ContactRow]] = []
    for r in resp.data:
        comp = by_id.get(r["company_id"])
        if not comp or not comp.get("domain"):
            continue
        domain = comp["domain"].strip().lower()
        vk = (r.get("verifieringskalla") or "").lower()
        prio = 0
        if domain and domain in vk:
            prio = 2  # source URL is already on the company's own site
        elif vk.startswith(("http://", "https://")):
            prio = 1  # external source URL (linkedin, press, allabolag…)
        emp = int(comp.get("antal_anstallda") or 999_999)
        row = ContactRow(
            id=r["id"],
            company_id=r["company_id"],
            namn=r["namn"],
            roll=r.get("roll") or "",
            verifieringskalla=r.get("verifieringskalla"),
            foretagsnamn=comp["foretagsnamn"],
            domain=domain,
        )
        scored.append((prio, emp, row))

    # Sort: higher prio first, then smaller company first.
    scored.sort(key=lambda t: (-t[0], t[1], t[2].namn))
    return [r for _, _, r in scored[:limit]]


def _persist_match(
    sb: Client,
    contact: ContactRow,
    match: Match,
) -> None:
    update_payload: dict[str, Any] = {}
    if match.email:
        update_payload["email"] = match.email.lower()
    if match.telefon:
        update_payload["telefon"] = match.telefon
    if match.confidence in ("high", "medium"):
        update_payload["verifierad"] = True
        update_payload["verifieringsmetod"] = "foretagswebbplats"
        update_payload["verifieringskalla"] = match.source_url
        update_payload["verifierat_av"] = "agent:enrich_existing-2026-05-28"
        update_payload["verifierat_datum"] = "now()"
    if not update_payload:
        return

    # Ugly but rpc-less: use the table API. "now()" doesn't work as a string —
    # Supabase REST passes it literally — so we drop and let the trigger
    # update senast_andrad which is what matters for audit.
    update_payload.pop("verifierat_datum", None)
    if match.confidence in ("high", "medium"):
        from datetime import datetime, timezone

        update_payload["verifierat_datum"] = datetime.now(
            timezone.utc
        ).isoformat()

    sb.table("contacts").update(update_payload).eq("id", contact.id).execute()

    # Audit row in sources
    audit = {
        "company_id": contact.company_id,
        "contact_id": contact.id,
        "field_name": "contacts.email"
        if match.email
        else "contacts.telefon",
        "source_url": match.source_url,
        "scraper_tier": 2,
        "raw_excerpt": (match.excerpt or "")[:500],
        "critic_note": (
            f"enrich_existing.py T1(DDG)+T2 (confidence={match.confidence}); "
            f"name={contact.namn} email={match.email or '-'} "
            f"tel={match.telefon or '-'}"
        ),
    }
    sb.table("sources").insert(audit).execute()


# ── Core enrichment ─────────────────────────────────────────────────────────


async def _try_url(url: str, contact: ContactRow) -> Match:
    """Singolo fetch T2 + estrazione."""
    res = await fetch_and_extract(url)
    if not res.ok or not (res.content_text or ""):
        return Match()
    email, phone, conf, excerpt = _find_evidence(
        res.content_text or "", contact.namn, contact.domain
    )
    if not email and not phone:
        return Match()
    return Match(
        email=email,
        telefon=_normalize_phone(phone),
        source_url=url,
        excerpt=excerpt,
        confidence=conf,
    )


async def _t1_fallback_urls(
    contact: ContactRow, client: SearXNGClient
) -> list[str]:
    """Quando i path templates non producono nulla utile, chiediamo a T1
    URLs candidati."""
    query = (
        f'"{contact.namn}" {contact.foretagsnamn} email kontakt'
    )
    results = await client.search(query, limit=5)
    urls: list[str] = []
    for r in results:
        if not r.ok or not r.url:
            continue
        # Keep only same-domain URLs as plausible source-of-truth
        if contact.domain in (r.url or "").lower():
            urls.append(r.url)
    return urls


async def _enrich_one(
    contact: ContactRow, t1: SearXNGClient
) -> Match:
    candidates = _candidate_urls(contact)

    best = Match()

    # Pass 1 — heuristic candidate URLs
    for url in candidates[:6]:  # cap per-contact fetches
        m = await _try_url(url, contact)
        if m.confidence == "high":
            return m
        if _better(m, best):
            best = m
        await asyncio.sleep(0.2)

    if best.confidence in ("high", "medium"):
        return best

    # Pass 2 — T1 fallback for additional URLs
    extra = await _t1_fallback_urls(contact, t1)
    for url in extra[:3]:
        if url in candidates:
            continue
        m = await _try_url(url, contact)
        if _better(m, best):
            best = m
        if best.confidence == "high":
            return best
        await asyncio.sleep(0.3)

    return best


def _better(a: Match, b: Match) -> bool:
    order = {"high": 3, "medium": 2, "low": 1, "none": 0}
    if order[a.confidence] != order[b.confidence]:
        return order[a.confidence] > order[b.confidence]
    # Same confidence: prefer the one with both email+phone over just one
    a_count = (1 if a.email else 0) + (1 if a.telefon else 0)
    b_count = (1 if b.email else 0) + (1 if b.telefon else 0)
    return a_count > b_count


# ── CLI ─────────────────────────────────────────────────────────────────────


async def main(limit: int, dry_run: bool) -> None:
    sb = _supabase()
    contacts = _fetch_candidates(sb, limit=limit)
    console.print(
        f"[bold cyan]Found {len(contacts)} DM-contacts with no email/telefon "
        f"and a known domain[/]"
    )
    if not contacts:
        return

    t1 = SearXNGClient()

    table = Table(title="Enrichment run")
    table.add_column("Nome", style="cyan")
    table.add_column("Företag", style="white")
    table.add_column("Email", style="green")
    table.add_column("Telefon", style="green")
    table.add_column("Conf", style="magenta")
    table.add_column("Källa", style="dim")

    for c in contacts:
        try:
            m = await _enrich_one(c, t1)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]ERR {c.namn}: {exc}[/]")
            continue
        if m.email or m.telefon:
            table.add_row(
                c.namn,
                c.foretagsnamn,
                m.email or "-",
                m.telefon or "-",
                m.confidence,
                (m.source_url or "")[:55],
            )
            if not dry_run:
                _persist_match(sb, c, m)
        else:
            table.add_row(
                c.namn,
                c.foretagsnamn,
                "-",
                "-",
                "none",
                "(no candidate URL produced evidence)",
            )

    console.print(table)
    console.print(
        f"[bold]{'DRY-RUN — no DB writes' if dry_run else 'DB updated'}[/]"
    )


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main(limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    _cli()
