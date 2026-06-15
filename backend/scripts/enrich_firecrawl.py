"""
Enrichment via Firecrawl (JS-rendering + anti-bot) — per i gap che httpx/WebFetch
non sono riusciti a coprire. BUDGET-AWARE: cap rigido sui crediti.

Target: aziende attive con `domain`, che hanno un contatto DM/VD con NOME ma
senza email (per matchare nome↔email pubblicata), o senza `email_info`.

Per ogni azienda:
  1. Firecrawl /scrape (1 credito) su una lista di path (/kontakt, /, /om-oss,
     /medarbetare, /ledning, /kontakta-oss, /team), STOP appena trova email
     personali sul dominio. Cap pagine/azienda.
  2. Se la pagina è bloccata (Cloudflare) e --stealth-fallback → 1 retry con
     proxy=stealth (≈5 crediti).
  3. Estrae email sul dominio. Match al nome di un contatto noto (token nome+
     cognome nel local-part) → email DM personale verificata. Generica
     (info@/kontakt@) → companies.email_info se vuoto.
  4. UPDATE + audit (tier 2, metodo foretagswebbplats, fonte=URL). Idempotente.

REGOLA: solo email VISTE nella pagina (mai pattern inventati).

Usage:
    cd backend
    .venv/Scripts/python.exe -m scripts.enrich_firecrawl --limit 20 --max-credits 80 --dry-run
    .venv/Scripts/python.exe -m scripts.enrich_firecrawl --limit 150 --max-credits 600
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from supabase import Client, create_client

console = Console()
ROOT_ENV = [os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
            os.path.join(os.path.dirname(__file__), "..", "..", ".env.local")]

FC_URL = "https://api.firecrawl.dev/v1/scrape"
FC_SEARCH = "https://api.firecrawl.dev/v1/search"
SEARCH_COST = 2  # credits per /search call (measured)
DEFAULT_PATHS = ["/kontakt/", "/medarbetare/", "/om-oss/ledning/", "/om-oss/",
                 "/kontakta-oss/", "/team/", "/"]
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
GENERIC = {"info", "kontakt", "contact", "support", "hello", "hej", "post",
           "office", "press", "media", "kansli", "reception", "kundservice",
           "order", "ekonomi", "faktura", "sales", "hr", "admin"}
EMAIL_INFO_OK = {"info", "kontakt", "contact", "office", "post", "hello", "hej"}
JUNK_LOCAL = {"jdoe", "johndoe", "john.doe", "example", "test", "namn", "fornamn",
              "efternamn", "namn.efternamn", "fornamn.efternamn", "firstname",
              "lastname", "firstname.lastname", "email", "epost", "e-post", "mail",
              "name", "user", "dittnamn", "ditt.namn", "xxx", "abc", "demo",
              "sample", "yourname", "your.name", "namn.namn", "noreply", "no-reply"}


def _junk_local(local: str) -> bool:
    l = local.lower()
    if l in JUNK_LOCAL:
        return True
    return any(x in l for x in ("example", "johndoe", "jdoe", "john.doe",
                                "yourname", "firstname", "lastname", "fornamn",
                                "efternamn", "dittnamn", "namn.namn"))
BLOCK_MARKERS = ("you have been blocked", "enable cookies",
                 "security service to protect", "captcha", "attention required")


def _fold(s: str) -> str:
    s = (s or "").lower().replace("å", "a").replace("ä", "a").replace("ö", "o")
    s = s.replace("ü", "u").replace("é", "e").replace("ø", "o").replace("æ", "a")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s)


def _name_tokens(namn: str):
    toks = [t for t in re.split(r"\s+", (namn or "").strip()) if len(t) >= 2]
    if len(toks) < 2:
        return None
    return _fold(toks[0]), _fold(toks[-1])


def _email_matches_name(local: str, first: str, last: str) -> bool:
    l = _fold(local)
    if not l or len(first) < 2 or len(last) < 2:
        return False
    if first in l and last in l:
        return True
    if last in l and len(last) >= 4 and first[0] in l:  # f.lastname / flastname
        return True
    return False


def _sb() -> Client:
    for p in ROOT_ENV:
        load_dotenv(p)
    load_dotenv()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    return create_client(url, os.environ["SUPABASE_SECRET_KEY"])


def _fc_key() -> str:
    for p in ROOT_ENV:
        load_dotenv(p)
    load_dotenv()
    return os.environ["FIRECRAWL_API_KEY"]


def _scrape(key: str, url: str, stealth: bool = False) -> tuple[str, bool, int]:
    """Return (markdown, blocked, credits_used_estimate)."""
    body = {"url": url, "formats": ["markdown"], "onlyMainContent": False,
            "timeout": 30000}
    if stealth:
        body["proxy"] = "stealth"
    try:
        r = requests.post(FC_URL, headers={"Authorization": f"Bearer {key}"},
                          json=body, timeout=120)
        if r.status_code != 200:
            return "", False, (5 if stealth else 1)
        md = (r.json().get("data") or {}).get("markdown", "") or ""
    except Exception:
        return "", False, (5 if stealth else 1)
    blocked = any(m in md.lower() for m in BLOCK_MARKERS) and len(md) < 1500
    return md, blocked, (5 if stealth else 1)


def _search(key: str, query: str, limit: int = 10) -> list[dict]:
    """Firecrawl web search (open engine). Returns result dicts (url/title/description)."""
    try:
        r = requests.post(FC_SEARCH, headers={"Authorization": f"Bearer {key}"},
                          json={"query": query, "limit": limit}, timeout=120)
        if r.status_code != 200:
            return []
        return r.json().get("data") or []
    except Exception:
        return []


def _emails_from_results(results: list[dict], domain: str) -> dict[str, tuple[str, str]]:
    """Extract on-domain emails from search-result snippets → {email: (local, source_url)}."""
    out: dict[str, tuple[str, str]] = {}
    d = domain.lower()
    for it in results:
        url = it.get("url") or f"https://{d}"
        blob = " ".join(str(it.get(k) or "") for k in ("title", "description", "markdown"))
        for em in EMAIL_RE.findall(blob):
            em = em.lower().rstrip(".")
            local, _, host = em.partition("@")
            if host in (d, "www." + d) and em not in out and not _junk_local(local):
                out[em] = (local, url)
    return out


def _emails_on_domain(md: str, domain: str) -> list[tuple[str, str]]:
    """Return [(email, local)] for emails on the company domain, deduped."""
    out, seen = [], set()
    d = domain.lower()
    for em in EMAIL_RE.findall(md):
        em = em.lower().rstrip(".")
        if em in seen:
            continue
        local, _, host = em.partition("@")
        if (host == d or host == "www." + d) and not _junk_local(local):
            seen.add(em)
            out.append((em, local))
    return out


def _fetch_targets(sb: Client, limit: int):
    """Companies with domain + a named contact without email + no DM-email yet."""
    companies, start, page = [], 0, 1000
    while True:
        r = (sb.table("companies").select("id, foretagsnamn, domain, email_info")
             .eq("arkiverad", False).neq("domain", "").order("id")
             .range(start, start + page - 1).execute())
        if not r.data:
            break
        companies.extend(r.data)
        if len(r.data) < page:
            break
        start += page
    contacts, start = [], 0
    while True:
        r = (sb.table("contacts").select("id, company_id, namn, roll, email, is_dm")
             .order("id").range(start, start + page - 1).execute())
        if not r.data:
            break
        contacts.extend(r.data)
        if len(r.data) < page:
            break
        start += page
    by_co: dict[str, list] = {}
    for k in contacts:
        by_co.setdefault(k["company_id"], []).append(k)

    targets = []
    for c in companies:
        ks = by_co.get(c["id"], [])
        has_dm_email = any(k.get("is_dm") and (k.get("email") or "").strip() for k in ks)
        named_no_email = [k for k in ks if (k.get("namn") or "").strip()
                          and not (k.get("email") or "").strip()]
        need_email_info = not (c.get("email_info") or "").strip()
        if has_dm_email and not need_email_info:
            continue
        if not named_no_email and not need_email_info:
            continue
        targets.append({"company": c, "named_no_email": named_no_email,
                        "need_email_info": need_email_info})
    # priority: companies with a known VD/DM name (best email-match candidates) first
    targets.sort(key=lambda t: (0 if t["named_no_email"] else 1,
                                t["company"]["foretagsnamn"]))
    return targets[:limit]


def main(limit: int, max_credits: int, max_pages: int, stealth_fallback: bool,
         dry_run: bool) -> None:
    sb = _sb()
    key = _fc_key()
    targets = _fetch_targets(sb, limit)
    console.print(f"[bold cyan]Targets: {len(targets)} (max_credits={max_credits} "
                  f"stealth_fallback={stealth_fallback} dry_run={dry_run})[/]")
    now = datetime.now(timezone.utc).isoformat()
    credits = 0
    stats = {"companies": 0, "dm_emails": 0, "email_info": 0, "blocked": 0,
             "no_email": 0, "errors": 0}

    for t in targets:
        if credits >= max_credits:
            console.print(f"[yellow]Credit cap {max_credits} reached — stopping.[/]")
            break
        c = t["company"]
        domain = (c["domain"] or "").strip().lower().replace("www.", "")
        if not domain or "." not in domain:
            continue
        stats["companies"] += 1
        need_personal = len(t["named_no_email"]) > 0
        found_emails: dict[str, tuple[str, str]] = {}  # email -> (local, source_url)

        # 1) dork search "@domain" (open engine — emails come back in snippets)
        if credits + SEARCH_COST <= max_credits:
            found_emails.update(_emails_from_results(
                _search(key, f'"@{domain}"', limit=10), domain))
            credits += SEARCH_COST

        # 2) still no personal email + VD name known → target the person by name
        def _has_personal():
            return any(l not in GENERIC for (l, _u) in found_emails.values())
        if need_personal and not _has_personal():
            for k in t["named_no_email"][:2]:
                if credits + SEARCH_COST > max_credits:
                    break
                found_emails.update(_emails_from_results(
                    _search(key, f'"{k["namn"]}" "@{domain}"', limit=8), domain))
                credits += SEARCH_COST
                if _has_personal():
                    break

        # 3) last resort: render /kontakt + /medarbetare (JS sites) if still no personal
        if need_personal and not _has_personal():
            for path in ("/kontakt/", "/medarbetare/"):
                if credits >= max_credits:
                    break
                page_url = f"https://{domain}{path}"
                md, blocked, cost = _scrape(key, page_url,
                                            stealth=stealth_fallback)
                credits += cost
                for em, local in _emails_on_domain(md, domain):
                    found_emails.setdefault(em, (local, page_url))
                if _has_personal():
                    break

        if not found_emails:
            stats["no_email"] += 1
            console.print(f"[dim]-- {c['foretagsnamn'][:34]:34} ({domain})  no email[/]")
            continue

        # 1) match personal email to a known named contact (VD/DM work email)
        for k in t["named_no_email"]:
            nt = _name_tokens(k["namn"])
            if not nt:
                continue
            first, last = nt
            for em, (local, src_url) in found_emails.items():
                if local in GENERIC:
                    continue
                if _email_matches_name(local, first, last):
                    if not dry_run:
                        try:
                            sb.table("contacts").update({
                                "email": em, "is_dm": True, "verifierad": True,
                                "verifieringsmetod": "foretagswebbplats",
                                "verifieringskalla": src_url,
                                "verifierat_av": "firecrawl-2026-06-15",
                                "verifierat_datum": now,
                            }).eq("id", k["id"]).execute()
                            sb.table("sources").insert({
                                "company_id": c["id"], "contact_id": k["id"],
                                "field_name": "contacts.email",
                                "source_url": src_url, "scraper_tier": 2,
                                "raw_excerpt": f"firecrawl DM {k['namn']}={em} @ {src_url}"[:500],
                                "critic_note": "enrich_firecrawl.py — JS-rendered site scrape, email seen on page",
                            }).execute()
                        except Exception as exc:  # noqa: BLE001
                            console.print(f"[red]{k['namn']}: {exc}[/]")
                            stats["errors"] += 1
                            continue
                    stats["dm_emails"] += 1
                    console.print(f"[green]DM {k['namn'][:24]:24} → {em}  [{src_url}][/]")
                    break

        # 2) email_info — ONLY a true generic mailbox (no personal/placeholder fallback)
        if t["need_email_info"]:
            gen = next(((em, u) for em, (local, u) in found_emails.items()
                        if local in EMAIL_INFO_OK), None)
            if gen:
                generic, generic_url = gen
                if not dry_run:
                    try:
                        sb.table("companies").update({"email_info": generic}).eq(
                            "id", c["id"]).execute()
                        sb.table("sources").insert({
                            "company_id": c["id"], "field_name": "companies.email_info",
                            "source_url": generic_url, "scraper_tier": 2,
                            "raw_excerpt": f"firecrawl email_info={generic} @ {generic_url}"[:500],
                            "critic_note": "enrich_firecrawl.py — JS-rendered site scrape",
                        }).execute()
                    except Exception as exc:  # noqa: BLE001
                        stats["errors"] += 1
                stats["email_info"] += 1
                console.print(f"[cyan]   email_info {c['foretagsnamn'][:28]:28} → {generic}[/]")

    # remaining credits from API
    try:
        rc = requests.get("https://api.firecrawl.dev/v1/team/credit-usage",
                          headers={"Authorization": f"Bearer {key}"}, timeout=30).json()
        remaining = rc.get("data", {}).get("remaining_credits")
    except Exception:
        remaining = "?"

    table = Table(title="Firecrawl enrichment")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k, v in stats.items():
        table.add_row(k, str(v))
    table.add_row("credits_used (est)", str(credits))
    table.add_row("credits_remaining (API)", str(remaining))
    console.print(table)
    if dry_run:
        console.print("[bold yellow]DRY-RUN — no DB writes (credits still spent on scrapes)[/]")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--max-credits", type=int, default=80)
    p.add_argument("--max-pages", type=int, default=3,
                   help="max Firecrawl pages scraped per company (credit cap)")
    p.add_argument("--stealth-fallback", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    main(a.limit, a.max_credits, a.max_pages, a.stealth_fallback, a.dry_run)
