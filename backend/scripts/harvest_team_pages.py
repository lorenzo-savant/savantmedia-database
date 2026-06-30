"""
Single-agent scanner: estrae DATI PUBBLICI delle persone dalle pagine ufficiali
delle aziende (om-oss / team / medarbetare / ledning / styrelse / kontakt).

Per ogni azienda CON dominio:
1. Scarica le pagine-team canoniche svedesi (httpx+BS4, escalation a Playwright
   sui blocchi 403/429/503).
2. Estrae persone pubbliche con euristiche ad alta precisione:
   - nome valido (`is_probable_person_name` — niente menu/aziende/ruoli/geo)
   - ruolo se una keyword di ruolo (VD, CTO, CFO, grundare, chef…) è adiacente
   - email del dominio (de-offuscata: snabel-a/punkt/[at]/[dot] + mailto footer)
   - telefono svedese e LinkedIn vicini al nome
3. Tiene una persona SOLO se ha (email sul dominio) OPPURE (un ruolo riconosciuto)
   — così evita righe-spazzatura.

Default = dry-run (riporta cosa trova, niente scrittura). `--apply` salva i
contatti nuovi in `contacts` (dedup per nome, audit in `sources`, tier 2).

Usage (da backend/):
    python -m scripts.harvest_team_pages --limit 20            # scan + report
    python -m scripts.harvest_team_pages --limit 50 --apply    # salva i nuovi
    python -m scripts.harvest_team_pages --domain savantmedia.se
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

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

from enrichment.escalate import fetch_with_escalation
from pipeline._extract_emails import (
    _NAME_REGEX,
    find_emails_in_text,
    find_linkedin_in_text,
    find_name_near_email,
    is_probable_person_name,
)

console = Console()

# Pagine-team canoniche svedesi, dalle più specifiche alle generiche.
_TEAM_PATHS: tuple[str, ...] = (
    "/medarbetare/", "/om-oss/medarbetare/", "/team/", "/vart-team/",
    "/ledning/", "/ledningsgrupp/", "/om-oss/ledning/", "/styrelse/",
    "/personal/", "/our-team/", "/people/", "/about-us/team/",
    "/om-oss/", "/om/", "/kontakt/", "/kontakta-oss/", "/about/", "/",
)

# Ruoli → etichetta normalizzata. Ordine = priorità.
_ROLE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"verkst[aä]llande\s+direkt[oö]r|\bvd\b|\bceo\b", re.I), "VD"),
    (re.compile(r"vice\s+vd|\bvvd\b", re.I), "Vice VD"),
    (re.compile(r"\bcto\b|teknisk\s+chef|teknikchef", re.I), "CTO"),
    (re.compile(r"\bcfo\b|ekonomichef|finanschef", re.I), "CFO"),
    (re.compile(r"\bcoo\b", re.I), "COO"),
    (re.compile(r"medgrundare|grundare|co-?founder|founder", re.I), "Grundare"),
    (re.compile(r"del[aä]gare|\bägare\b|\bowner\b", re.I), "Ägare/Delägare"),
    (re.compile(r"styrelseordf[oö]rande|ordf[oö]rande", re.I), "Ordförande"),
    (re.compile(r"styrelseledamot|styrelse", re.I), "Styrelse"),
    (re.compile(r"f[oö]rs[aä]ljningschef|s[aä]ljchef|head\s+of\s+sales", re.I), "Försäljningschef"),
    (re.compile(r"marknadschef|marketing\s+manager", re.I), "Marknadschef"),
    (re.compile(r"personalchef|hr-chef|\bhr\b", re.I), "HR"),
    (re.compile(r"\bpartner\b", re.I), "Partner"),
    (re.compile(r"chef|manager|direkt[oö]r|director|konsult", re.I), "Befattning"),
]

_PHONE_RE = re.compile(r"(?:\+46|0)[\s\-]?(?:\d[\s\-]?){6,11}\d")

# Titoli / parole-azienda da rimuovere dai BORDI di un nome catturato
# ("Jonas Blomquist Advokat" -> "Jonas Blomquist"; "Risk Management" -> "").
_TITLE_NAME_TOKENS: frozenset[str] = frozenset(
    {
        "advokat", "jurist", "chef", "koncernchef", "vd", "vvd", "ceo", "cfo",
        "cto", "coo", "executive", "chief", "partner", "konsult", "manager",
        "direktör", "direktor", "director", "affärsområdeschef",
        "affarsomradeschef", "senior", "head", "of", "ägare", "agare",
        "grundare", "founder", "styrelseledamot", "ordförande", "ordforande",
        "advisory", "capital", "fund", "global", "group", "holding", "ab",
        "aktiebolag", "fastighets", "fastigheter", "digitals", "supply",
        "chain", "risk", "management", "marketing", "sales", "hr", "vice",
        "wood", "service", "invest", "ventures", "consulting",
    }
)

# Nomi di battesimo comuni (sv/nordici/intl). Nella modalità "nome+ruolo" il
# primo token DEVE essere uno di questi: elimina nomi-azienda/reparto che un
# regex di bigrammi maiuscoli cattura ("Supply Chain", "Stora Enso", ...).
_GIVEN_NAMES: frozenset[str] = frozenset(
    {
        # maschili
        "lars", "mikael", "michael", "anders", "johan", "erik", "per", "karl",
        "carl", "nils", "lennart", "peter", "thomas", "tomas", "jan", "daniel",
        "fredrik", "hans", "gunnar", "bengt", "bo", "sven", "göran", "goran",
        "stefan", "magnus", "mats", "mattias", "mathias", "andreas", "marcus",
        "markus", "henrik", "patrik", "patrick", "jonas", "martin", "david",
        "alexander", "oskar", "oscar", "emil", "filip", "philip", "viktor",
        "victor", "gustav", "axel", "william", "lucas", "liam", "elias", "hugo",
        "adam", "leo", "olle", "olof", "ola", "tobias", "robert", "roger",
        "kjell", "ulf", "åke", "ake", "arne", "rolf", "jörgen", "jorgen",
        "christer", "kent", "tommy", "roland", "björn", "bjorn", "dan", "joakim",
        "niklas", "rikard", "richard", "mårten", "marten", "petter", "pontus",
        "sebastian", "simon", "samuel", "isak", "anton", "felix", "theodor",
        "vincent", "wilhelm", "albin", "edvin", "melker", "sixten", "folke",
        "ivar", "stig", "sune", "tage", "yngve", "helge", "knut", "ragnar",
        "jonny", "johnny", "joel", "jesper", "fabian", "kristoffer", "christoffer",
        "ludvig", "love", "rasmus", "rickard", "tony", "claes", "klas", "håkan",
        "hakan", "lasse", "micke", "janne", "calle",
        # femminili
        "anna", "maria", "margareta", "elisabeth", "eva", "kristina", "birgitta",
        "karin", "ingrid", "sara", "lena", "emma", "hanna", "linda", "sofia",
        "sofie", "johanna", "malin", "jenny", "annika", "helena", "camilla",
        "cecilia", "ulrika", "åsa", "asa", "susanne", "carina", "monica",
        "marie", "elin", "julia", "klara", "clara", "alice", "maja", "ebba",
        "wilma", "agnes", "astrid", "saga", "alva", "olivia", "ella", "lova",
        "stella", "signe", "ester", "elsa", "freja", "nellie", "moa", "tuva",
        "linnea", "frida", "amanda", "josefin", "matilda", "rebecca", "therese",
        "caroline", "charlotte", "christina", "gunilla", "inger", "barbro",
        "berit", "britt", "gerd", "gun", "marianne", "siv", "yvonne", "lisbeth",
        "kristin", "kerstin", "gudrun", "solveig", "cozette", "louise", "lotta",
        "petra", "sandra", "jessica", "rebecka", "emelie", "ida", "nina",
        "victoria", "viktoria", "isabelle", "michaela", "madeleine", "pernilla",
    }
)

_NAME_TRANSLIT = str.maketrans(
    {"å": "a", "ä": "a", "ö": "o", "é": "e", "è": "e", "ü": "u", "ø": "o", "æ": "a"}
)


# Suffissi-titolo: token che FINISCE con uno di questi è un titolo, non un
# cognome ("Seniorkonsult", "Projektledare", "Ekonomichef", "Försäljningschef").
_TITLE_STEM_RE = re.compile(
    r"(konsult|chef|ledare|direkt[oö]r|ansvarig|advokat|jurist|r[åa]dgivare|"
    r"specialist|ingenj[oö]r|analytiker|controller|ekonom|arkitekt|designer|"
    r"utvecklare|s[äa]ljare|partner|del[äa]gare|grundare|koordinator|samordnare|"
    r"vetenskaplig|kommersiell|kommunikat[oö]r|assistent|administrat[oö]r|"
    r"office|financial|manager|officer|advisor|associate|trainee|praktikant)$",
    re.I,
)


def _is_title_token(tok: str) -> bool:
    tl = tok.lower().strip(".,:")
    return tl in _TITLE_NAME_TOKENS or bool(_TITLE_STEM_RE.search(tl))


def _clean_name(raw: str) -> str:
    """Rimuove titoli/parole-azienda dai bordi del nome. '' se non resta un nome."""
    toks = (raw or "").split()
    while toks and _is_title_token(toks[0]):
        toks.pop(0)
    while toks and _is_title_token(toks[-1]):
        toks.pop()
    return " ".join(toks) if len(toks) >= 2 else ""


def _looks_like_person(name: str, email: str = "") -> bool:
    """Accetta se il 1° token è un nome noto OPPURE un token combacia con la
    local-part dell'email (evidenza forte). Filtra nomi-azienda/reparto."""
    toks = _norm(name).split()
    if not toks:
        return False
    if toks[0] in _GIVEN_NAMES:
        return True
    if email:
        local = email.split("@", 1)[0].lower().translate(_NAME_TRANSLIT)
        local = re.sub(r"[^a-z]", "", local)
        for t in toks:
            tt = t.translate(_NAME_TRANSLIT)
            if len(tt) >= 4 and tt in local:
                return True
    return False


@dataclass
class Person:
    namn: str
    roll: str = ""
    email: str = ""
    telefon: str = ""
    linkedin: str = ""
    source_url: str = ""


@dataclass
class CompanyRow:
    id: str
    foretagsnamn: str
    domain: str
    existing_names: set[str] = field(default_factory=set)


def _supabase() -> Client:
    load_dotenv()
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    load_dotenv(
        dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env.local")
    )
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        console.print("[red]Mancano NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SECRET_KEY[/]")
        raise SystemExit(1)
    return create_client(url, key)


def _norm(name: str) -> str:
    return " ".join((name or "").lower().split())


def _role_in(snippet: str) -> str:
    for rx, label in _ROLE_RULES:
        if rx.search(snippet):
            return label
    return ""


def _around(text: str, lo: int, hi: int, window: int = 150) -> str:
    return text[max(0, lo - window): min(len(text), hi + window)]


def _phone_in(snippet: str) -> str:
    m = _PHONE_RE.search(snippet)
    if not m:
        return ""
    digits = re.sub(r"\D", "", m.group(0))
    return m.group(0).strip() if 8 <= len(digits) <= 13 else ""


def extract_people(text: str, domain: str, source_url: str) -> dict[str, Person]:
    """Estrae persone pubbliche dal testo di una pagina-team."""
    domain = domain.lower().lstrip(".")
    people: dict[str, Person] = {}

    def _get(name: str) -> Person:
        key = _norm(name)
        if key not in people:
            people[key] = Person(namn=name, source_url=source_url)
        return people[key]

    # 1) Anchor sulle email (alta precisione: email + nome valido vicino).
    lower = text.lower()
    for em in find_emails_in_text(text):
        local, _, host = em.partition("@")
        on_domain = host == domain or host.endswith("." + domain)
        name = _clean_name(find_name_near_email(text, em) or "")
        if not name or not is_probable_person_name(name):
            continue
        if not _looks_like_person(name, em if on_domain else ""):
            continue  # non un nome-persona (azienda/reparto/heading)
        p = _get(name)
        if on_domain and not p.email:
            p.email = em
        pos = lower.find(em)
        if pos >= 0:
            snip = _around(text, pos, pos + len(em))
            if not p.roll:
                p.roll = _role_in(snip)
            if not p.telefon:
                p.telefon = _phone_in(snip)
            if not p.linkedin:
                p.linkedin = find_linkedin_in_text(snip) or ""

    # 2) Anchor sui nomi+ruolo (anche senza email): persona pubblica del team.
    for m in _NAME_REGEX.finditer(text):
        name = _clean_name(m.group(0))
        if not name or not is_probable_person_name(name):
            continue
        snip = _around(text, m.start(), m.end(), window=90)
        role = _role_in(snip)
        if not role:
            continue  # senza ruolo riconosciuto non lo contiamo (anti-rumore)
        if not _looks_like_person(name, ""):
            continue  # nessuna email → il 1° token dev'essere un nome noto
        p = _get(name)
        if not p.roll:
            p.roll = role
        if not p.telefon:
            p.telefon = _phone_in(snip)
        if not p.linkedin:
            p.linkedin = find_linkedin_in_text(snip) or ""

    # Tieni solo chi ha email-sul-dominio OPPURE un ruolo (filtro anti-spazzatura).
    return {k: p for k, p in people.items() if p.email or p.roll}


async def scan_company(c: CompanyRow, max_pages: int = 10) -> list[Person]:
    found: dict[str, Person] = {}
    pages_fetched = 0
    for path in _TEAM_PATHS:
        if pages_fetched >= max_pages:
            break
        url = f"https://{c.domain}{path}"
        try:
            res = await fetch_with_escalation(url, timeout=15.0, max_attempts=2)
        except Exception:
            continue
        if not res.ok or not res.content_text:
            continue
        pages_fetched += 1
        text = " ".join(filter(None, [res.title, res.content_text]))
        for key, p in extract_people(text, c.domain, url).items():
            if key in found:
                ex = found[key]
                ex.email = ex.email or p.email
                ex.roll = ex.roll or p.roll
                ex.telefon = ex.telefon or p.telefon
                ex.linkedin = ex.linkedin or p.linkedin
            else:
                found[key] = p
        # Early-stop: una pagina-team ha già reso un buon gruppo.
        if len([p for p in found.values() if p.roll]) >= 4 and pages_fetched >= 2:
            break
        await asyncio.sleep(0.2)

    # Scarta nomi già presenti tra i contatti dell'azienda.
    return [
        p for k, p in found.items() if k not in c.existing_names
    ]


def _fetch_targets(
    sb: Client,
    limit: int,
    domain: str | None,
    name_contains: list[str] | None = None,
    offset: int = 0,
    recent_hours: float | None = None,
) -> list[CompanyRow]:
    q = (
        sb.table("companies")
        .select("id, foretagsnamn, domain")
        .eq("arkiverad", False)
        .not_.is_("domain", "null")
        .neq("domain", "")
    )
    if recent_hours:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=recent_hours)).isoformat()
        q = q.gte("skapad_datum", cutoff)
    q = (q.order("foretagsnamn")
         .range(offset, offset + limit - 1))  # finestra per il chunking parallelo
    if domain:
        q = q.eq("domain", domain)
    if name_contains:
        # Targeting: aziende il cui nome contiene una di queste sottostringhe
        # (konsult/group/byrå/advokat…) → molto più probabili ad avere team page.
        clause = ",".join(f"foretagsnamn.ilike.*{s.strip()}*" for s in name_contains if s.strip())
        if clause:
            q = q.or_(clause)
    resp = q.execute()

    rows: list[CompanyRow] = []
    for r in resp.data:
        dom = (r.get("domain") or "").strip().lower().lstrip(".")
        if not dom:
            continue
        rows.append(CompanyRow(id=r["id"], foretagsnamn=r["foretagsnamn"], domain=dom))
        if len(rows) >= limit:
            break

    # Carica i nomi-contatto già esistenti (per non duplicare).
    if rows:
        ids = [c.id for c in rows]
        existing = (
            sb.table("contacts").select("company_id, namn").in_("company_id", ids).execute()
        )
        by_company: dict[str, set[str]] = {}
        for e in existing.data or []:
            by_company.setdefault(e["company_id"], set()).add(_norm(e.get("namn") or ""))
        for c in rows:
            c.existing_names = by_company.get(c.id, set())
    return rows


async def main(
    limit: int,
    domain: str | None,
    apply: bool,
    name_contains: list[str] | None,
    offset: int = 0,
    as_json: bool = False,
    recent_hours: float | None = None,
) -> None:
    sb = _supabase()
    targets = _fetch_targets(
        sb, limit=limit, domain=domain, name_contains=name_contains,
        offset=offset, recent_hours=recent_hours,
    )
    if not as_json:
        console.print(
            f"[bold cyan]Scan team-pages — {len(targets)} aziende con dominio "
            f"(offset={offset}, apply={apply})[/]\n"
        )
    if not targets:
        if as_json:
            print(json.dumps({"scanned": 0, "people": 0, "companies": []}))
        else:
            console.print("[yellow]Nessuna azienda con dominio trovata.[/]")
        return

    total_people = 0
    companies_with_hits = 0
    inserted = 0
    results_json: list[dict] = []

    for c in targets:
        people = await scan_company(c)
        if not people:
            if not as_json:
                console.print(f"[dim]-- {c.foretagsnamn[:34]:34} {c.domain:26} (niente)[/]")
            continue
        companies_with_hits += 1
        total_people += len(people)
        if as_json:
            results_json.append({
                "company_id": c.id,
                "foretagsnamn": c.foretagsnamn,
                "domain": c.domain,
                "people": [
                    {
                        "namn": p.namn, "roll": p.roll, "email": p.email,
                        "telefon": p.telefon, "linkedin": p.linkedin,
                        "source_url": p.source_url,
                    }
                    for p in people
                ],
            })
        else:
            console.print(f"[green]OK[/] {c.foretagsnamn[:34]:34} [cyan]{c.domain}[/]")
            for p in people:
                bits = [b for b in (p.roll, p.email, p.telefon, p.linkedin) if b]
                console.print(f"     • {p.namn}  [dim]{' | '.join(bits)}[/]")

        if apply:
            for p in people:
                try:
                    has_domain_email = bool(p.email)
                    payload = {
                        "company_id": c.id,
                        "namn": p.namn,
                        "roll": p.roll or "",
                        "email": p.email or "",
                        "telefon": p.telefon or "",
                        "linkedin_url": p.linkedin or "",
                        "verifierad": has_domain_email,
                        "verifieringsmetod": "foretagswebbplats" if has_domain_email else None,
                        "verifieringskalla": p.source_url,
                        "verifierat_av": "agent:harvest_team_pages",
                        "verifierat_datum": datetime.now(timezone.utc).isoformat(),
                    }
                    ins = sb.table("contacts").insert(payload).execute()
                    cid = ins.data[0]["id"] if ins.data else None
                    if cid:
                        sb.table("sources").insert({
                            "company_id": c.id,
                            "contact_id": cid,
                            "field_name": "contacts.*",
                            "source_url": p.source_url,
                            "scraper_tier": 2,
                            "raw_excerpt": f"team-page: {p.namn} ({p.roll})",
                            "critic_note": "harvest_team_pages.py — public om-oss/team scrape",
                        }).execute()
                    inserted += 1
                except Exception as exc:  # noqa: BLE001
                    if not as_json:
                        console.print(f"[red]  insert fail {p.namn}: {exc}[/]")

    if as_json:
        print(json.dumps({
            "scanned": len(targets),
            "companies_with_people": companies_with_hits,
            "people": total_people,
            "inserted": inserted,
            "companies": results_json,
        }, ensure_ascii=False))
        return

    table = Table(title="Riepilogo scan team-pages")
    table.add_column("Metrica", style="cyan")
    table.add_column("Valore", justify="right", style="green")
    table.add_row("Aziende scandite", str(len(targets)))
    table.add_row("Con persone trovate", str(companies_with_hits))
    table.add_row("Persone pubbliche totali", str(total_people))
    if apply:
        table.add_row("Contatti inseriti", str(inserted))
    console.print(table)
    if not apply:
        console.print("\n[yellow]DRY-RUN — niente salvato. Rilancia con --apply per inserire.[/]")


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0, help="Offset per il chunking parallelo")
    p.add_argument("--domain", default=None, help="Scandisci una sola azienda (dominio)")
    p.add_argument("--apply", action="store_true", help="Salva i contatti nuovi (default: dry-run)")
    p.add_argument(
        "--name-contains",
        default=None,
        help="Filtra per sottostringhe nel nome (csv), es. 'konsult,group,byrå,advokat'",
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Output JSON strutturato (per orchestrazione multi-agente)",
    )
    p.add_argument("--recent-hours", type=float, default=None,
                   help="solo aziende create nelle ultime N ore (es. import recente)")
    args = p.parse_args()
    name_contains = (
        [s for s in args.name_contains.split(",") if s.strip()]
        if args.name_contains
        else None
    )
    asyncio.run(
        main(args.limit, args.domain, args.apply, name_contains, args.offset,
             args.as_json, args.recent_hours)
    )


if __name__ == "__main__":
    _cli()
