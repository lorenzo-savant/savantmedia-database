"""
Tier 1.5 — Email discovery via Google dorking sui motori di ricerca.

Strategia (vault Lorenzo 2026-06-02):
Usare l'indice di Google/Brave/Bing/Ecosia come database di email pubbliche.
Per ogni azienda con dominio noto:

1. `"@<domain>"` (es. `"@savantmedia.se"`) — trova OGNI pagina indicizzata
   che contiene un'email su quel dominio. Risultati: leadership pages,
   pressmeddelanden, LinkedIn posts, etc.
2. `"<vd_namn>" "<domain>" email` — per un nome noto, trova menzioni
   specifiche.
3. `site:<domain> "vd" OR "VD" OR "verkställande direktör"` — limita al
   sito aziendale, cerca pagina del VD.

Due livelli di estrazione (2026-06-08):
- **Snippet pass** — raccoglie le email che appaiono già negli snippet SERP.
- **Page-fetch escalation** — le email vivono quasi sempre *sulla pagina*
  (kontakt/medarbetare/ledning), non nello snippet. Quando un risultato punta
  al dominio target o a una pagina di contatto, scarichiamo la pagina e
  estraiamo dal testo completo. Questo è il singolo guadagno di recall più
  grande rispetto al solo snippet-scraping.

Entrambi i pass passano per `deobfuscate_emails` (pipeline) così catturiamo
le forme svedesi tipiche: `namn snabel-a domän punkt se`, `[at]`/`[dot]`,
e gli indirizzi spaziati.

Tutto via HTML scraping cost-zero (Brave/Ecosia/Bing — Google ha capcha
spesso, lo evitiamo o usiamo se non triggera). Niente API key. Rispetta
robots.txt + rate limit del `_rate_limit.py`.

Usage:
    from scrapers.email_search import find_emails_on_domain
    emails = await find_emails_on_domain("savantmedia.se")
    # → {'info@savantmedia.se', 'lorenzo@savantmedia.se', ...}

    ranked = await find_emails_for_person_ranked("Erik Andersson", "savantmedia.se")
    # → [('erik.andersson@savantmedia.se', 12), ('e.andersson@savantmedia.se', 7)]

    # personale-first, generici in fondo:
    best = rank_domain_emails(emails, "savantmedia.se")
    # → [('lorenzo@savantmedia.se', 0.85), ..., ('info@savantmedia.se', 0.1)]
"""

from __future__ import annotations

import asyncio
import re
from typing import Iterable
from urllib.parse import urlparse

from .base import ScrapeResult
from .httpbs import fetch_and_extract
from .multi_search import BraveClient, BingClient, EcosiaClient

# De-offuscazione condivisa col pipeline (single source of truth). Difensivo:
# se il package pipeline non è sul path, degradiamo a no-op invece di rompere.
try:  # pragma: no cover - import wiring
    from pipeline._extract_emails import deobfuscate_emails, _is_placeholder_email
except Exception:  # pragma: no cover
    def deobfuscate_emails(text: str) -> str:  # type: ignore[misc]
        return text or ""

    def _is_placeholder_email(email: str) -> bool:  # type: ignore[misc]
        return False

# Regole di verifica condivise (generico vs personale) — usate solo per il
# ranking, opzionali.
try:  # pragma: no cover - import wiring
    from pipeline.email_verification import check_email
except Exception:  # pragma: no cover
    check_email = None  # type: ignore[assignment]


# Local part svedese: includiamo åäö perché compaiono in indirizzi reali.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-åäöÅÄÖ]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Quando l'ultima label è una di queste, l'"email" è in realtà un asset
# (logo@2x.png) o un artefatto di bundler — non un contatto.
_ASSET_TLDS = frozenset(
    {"png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "css", "js",
     "woff", "woff2", "mp4", "pdf"}
)

# Path che indicano una pagina di contatto/persone svedese — prioritarie per
# la page-fetch escalation.
_CONTACT_PATH_HINTS = (
    "kontakt", "kontakta", "medarbetare", "ledning", "ledningsgrupp",
    "team", "personal", "styrelse", "om-oss", "om_oss", "omoss",
    "about", "staff", "employees", "people", "vd", "ledningen",
)

# Pagine canoniche on-domain da sondare SEMPRE, anche quando le SERP non danno
# URL (i motori HTML sono spesso bloccati da robots.txt). Qui vivono le email
# pubbliche / mailto svedesi.
_CANONICAL_EMAIL_PATHS = (
    "/kontakt",
    "/kontakta-oss",
    "/om-oss",
    "/medarbetare",
    "/team",
    "/contact",
    "/",
)


# ─── Engines ──────────────────────────────────────────────────────────────────


def _build_clients(engines: Iterable[str]) -> list:
    mapping = {
        "brave": BraveClient,
        "ecosia": EcosiaClient,
        "bing": BingClient,
    }
    clients = []
    for name in engines:
        cls = mapping.get(name)
        if cls is not None:
            clients.append(cls())
    return clients


async def _search_results(client, query: str, limit: int) -> list[ScrapeResult]:
    """Esegui una query su un motore, ritorna solo i risultati validi."""
    try:
        results = await client.search(query, limit=limit)
    except Exception:
        return []
    return [r for r in results if r.ok]


# ─── Email extraction ─────────────────────────────────────────────────────────


def _extract_emails_matching_domain(text: str, domain: str) -> set[str]:
    """Estrae tutte le email del dominio target dal testo (snippet o pagina).

    De-offusca prima dell'estrazione e scarta asset (`@2x.png`, ecc.).
    """
    domain = domain.lower().strip(".")
    out: set[str] = set()
    deob = deobfuscate_emails(text or "")
    for m in _EMAIL_RE.finditer(deob):
        em = m.group(0).lower().strip(".,;:()[]{}<>\"' \t\r\n")
        local, _, host = em.partition("@")
        if not local or not host:
            continue
        if host.rsplit(".", 1)[-1] in _ASSET_TLDS:
            continue
        if _is_placeholder_email(em):
            continue  # template (fornamn.efternamn@…) / example / asset
        if host == domain or host.endswith("." + domain):
            out.add(em)
    return out


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _page_priority(url: str, domain: str) -> int:
    """Più basso = più promettente da scaricare. 3 = da ignorare."""
    host = _host(url)
    on_domain = host == domain or host.endswith("." + domain)
    low = (url or "").lower()
    contactish = any(h in low for h in _CONTACT_PATH_HINTS)
    if on_domain and contactish:
        return 0
    if on_domain:
        return 1
    if contactish:
        return 2
    return 3


async def _emails_from_result_pages(
    results: list[ScrapeResult], domain: str, max_pages: int = 8
) -> set[str]:
    """Scarica le pagine-risultato più promettenti ed estrae le email.

    Le email pubbliche stanno quasi sempre sulla pagina (kontakt/team), non
    nello snippet SERP. Sonda SEMPRE le pagine canoniche on-domain (così
    funziona anche quando le SERP sono bloccate da robots.txt), più gli URL
    SERP on-domain / di contatto (priorità ≤ 2), fino a `max_pages`.
    """
    seen: set[str] = set()
    candidates: list[str] = []

    # Pagine canoniche on-domain — sempre, indipendentemente dalle SERP.
    if domain:
        for path in _CANONICAL_EMAIL_PATHS:
            u = f"https://{domain}{path}"
            if u not in seen:
                seen.add(u)
                candidates.append(u)

    for r in results:
        url = r.url or ""
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        if _page_priority(url, domain) <= 2:
            candidates.append(url)

    if not candidates:
        return set()

    candidates.sort(key=lambda u: _page_priority(u, domain))
    chosen = candidates[:max_pages]

    async def _one(u: str) -> set[str]:
        try:
            res = await fetch_and_extract(u, timeout=20.0)
        except Exception:
            return set()
        if not res.ok:
            return set()
        text = " ".join(
            filter(None, [res.title, res.content_text, res.content_markdown])
        )
        return _extract_emails_matching_domain(text, domain)

    page_sets = await asyncio.gather(
        *[_one(u) for u in chosen], return_exceptions=True
    )
    out: set[str] = set()
    for s in page_sets:
        if isinstance(s, set):
            out |= s
    return out


# ─── Domain-wide search ───────────────────────────────────────────────────────


async def find_emails_on_domain(
    domain: str,
    engines: Iterable[str] = ("brave", "ecosia", "bing"),
    limit_per_engine: int = 10,
    fetch_pages: bool = True,
    max_pages: int = 8,
) -> set[str]:
    """Trova tutte le email indicizzate su un dominio specifico.

    Due livelli: snippet SERP + (opzionale) fetch delle pagine più promettenti.
    Ritorna un `set` di indirizzi. Per ordinarli personale-first usa
    `rank_domain_emails`.
    """
    domain = domain.strip().lower().lstrip(".")
    queries = [
        f'"@{domain}"',
        f'site:{domain} "@{domain}"',
        f'site:{domain} (kontakt OR medarbetare OR ledning)',
    ]
    clients = _build_clients(engines)

    tasks = [
        _search_results(client, q, limit_per_engine)
        for client in clients
        for q in queries
    ]
    nested = await asyncio.gather(*tasks, return_exceptions=True)
    results = [r for n in nested if isinstance(n, list) for r in n]

    found: set[str] = set()
    for r in results:
        bag = " ".join(filter(None, [r.title, r.content_text, r.url]))
        found |= _extract_emails_matching_domain(bag, domain)

    if fetch_pages:
        found |= await _emails_from_result_pages(results, domain, max_pages=max_pages)

    return found


# ─── Person-targeted search ───────────────────────────────────────────────────


def _norm(s: str) -> str:
    """Translittera diacritici scandinavi/europei verso ASCII per il matching."""
    s = (s or "").lower().replace("æ", "ae").replace("œ", "oe").replace("ß", "ss")
    return s.translate(_TRANSLIT)


_TRANSLIT = str.maketrans(
    {
        "å": "a", "ä": "a", "á": "a", "à": "a", "â": "a", "ã": "a",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "í": "i", "ì": "i", "î": "i", "ï": "i",
        "ó": "o", "ò": "o", "ô": "o", "õ": "o", "ö": "o", "ø": "o",
        "ú": "u", "ù": "u", "û": "u", "ü": "u",
        "ý": "y", "ÿ": "y", "ç": "c", "ñ": "n",
    }
)


def _name_parts(person_name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", _norm(person_name)) if len(p) >= 2]
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) >= 2 else ""
    return first, last


def _score_person_email(local: str, first: str, last: str) -> int:
    """Quanto bene la local-part combacia col nome. 0 = nessun match.

    Match per *token* (split su `. _ - + cifre`), non sottostringa cruda — così
    `lind` non matcha più erroneamente `linda`. Le forme concatenate svedesi
    (`erikandersson@`, `eandersson@`) sono comunque riconosciute.
    """
    ln = _norm(local)
    tokens = [t for t in re.split(r"[._\-+0-9]+", ln) if t]
    tokenset = set(tokens)
    joined = "".join(tokens)
    fi = first[:1] if first else ""
    li = last[:1] if last else ""

    exact_first = bool(first) and first in tokenset
    exact_last = bool(last) and last in tokenset
    concat_full = bool(first) and bool(last) and (
        f"{first}{last}" in joined or f"{last}{first}" in joined
    )
    initial_last = bool(last) and bool(fi) and (
        joined == fi + last or joined.startswith(fi + last) or ln == f"{fi}.{last}"
    )
    first_initiallast = bool(first) and bool(li) and (
        ln == f"{first}.{li}" or joined == first + li
    )

    score = 0
    if concat_full:
        score += 6
    if exact_last:
        score += 4
    if exact_first:
        score += 2
    if initial_last:
        score += 3
    if first_initiallast:
        score += 1
    if bool(first) and bool(last) and ln in {
        f"{first}.{last}", f"{last}.{first}", f"{fi}.{last}", f"{first}.{li}",
    }:
        score += 2
    return score


async def find_emails_for_person_ranked(
    person_name: str,
    domain: str,
    engines: Iterable[str] = ("brave", "ecosia", "bing"),
    limit_per_engine: int = 8,
    fetch_pages: bool = True,
) -> list[tuple[str, int]]:
    """Trova le email per una persona, ordinate per qualità del name-match.

    Ritorna `[(email, score), ...]` decrescente. Solo gli indirizzi con
    score > 0 (cioè col nome nella local-part) sono inclusi.
    """
    domain = domain.strip().lower().lstrip(".")
    pq = f'"{person_name}"'
    queries = [
        f'{pq} "@{domain}"',
        f'{pq} "{domain}" (email OR e-post OR kontakt)',
        f'site:{domain} {pq}',
    ]
    clients = _build_clients(engines)

    tasks = [
        _search_results(client, q, limit_per_engine)
        for client in clients
        for q in queries
    ]
    nested = await asyncio.gather(*tasks, return_exceptions=True)
    results = [r for n in nested if isinstance(n, list) for r in n]

    found: set[str] = set()
    for r in results:
        bag = " ".join(filter(None, [r.title, r.content_text, r.url]))
        found |= _extract_emails_matching_domain(bag, domain)

    if fetch_pages:
        found |= await _emails_from_result_pages(results, domain, max_pages=8)

    first, last = _name_parts(person_name)
    scored: list[tuple[str, int]] = []
    for em in found:
        sc = _score_person_email(em.split("@", 1)[0], first, last)
        if sc > 0:
            scored.append((em, sc))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored


async def find_emails_for_person(
    person_name: str,
    domain: str,
    engines: Iterable[str] = ("brave", "ecosia", "bing"),
    limit_per_engine: int = 8,
    fetch_pages: bool = True,
) -> set[str]:
    """Trova email per una persona specifica nel dominio dato (set, retro-compat).

    Per l'ordine di confidenza usa `find_emails_for_person_ranked`.
    """
    ranked = await find_emails_for_person_ranked(
        person_name, domain, engines, limit_per_engine, fetch_pages
    )
    return {em for em, _ in ranked}


# ─── Ranking helper ───────────────────────────────────────────────────────────


def rank_domain_emails(
    emails: Iterable[str], domain: str | None
) -> list[tuple[str, float]]:
    """Ordina le email personale-first usando le regole di verifica condivise.

    Ritorna `[(email, confidence), ...]` decrescente. I generici (`info@`,
    `kontakt@`, …) finiscono in fondo (confidence 0.1) ma non vengono scartati.
    """
    if check_email is None:
        return [(e, 0.0) for e in sorted({e.lower() for e in emails})]
    scored: list[tuple[str, float]] = []
    for e in {e.lower() for e in emails}:
        res = check_email(e, domain)
        conf = 0.1 if res.generic else res.confidence
        scored.append((e, conf))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored


# ─── Self-tests (offline, pure helpers) ───────────────────────────────────────


if __name__ == "__main__":
    failures: list[str] = []

    def _check(label: str, got, want) -> None:
        if got != want:
            failures.append(f"  {label}: expected {want!r}, got {got!r}")
        else:
            print(f"  OK — {label}")

    # Extraction: de-offuscazione + filtro dominio + asset.
    txt = (
        "Kontakta lorenzo [at] savantmedia [dot] se, "
        "anna snabel-a savantmedia punkt se, "
        "noise@other.com och logo@2x.png"
    )
    _check(
        "extract + deobfuscate + domain filter",
        _extract_emails_matching_domain(txt, "savantmedia.se"),
        {"lorenzo@savantmedia.se", "anna@savantmedia.se"},
    )

    # Person scoring.
    _check("score full dotted", _score_person_email("erik.andersson", "erik", "andersson") > 0, True)
    _check("score initial+last", _score_person_email("eandersson", "erik", "andersson") > 0, True)
    _check("score concat", _score_person_email("erikandersson", "erik", "andersson") > 0, True)
    _check("score generic = 0", _score_person_email("info", "erik", "andersson"), 0)
    # Falso positivo storico: 'lind' NON deve matchare 'linda'.
    _check("no substring FP", _score_person_email("linda", "bert", "lind"), 0)
    # Ranking: dotted full-name batte initial-only.
    full = _score_person_email("erik.andersson", "erik", "andersson")
    init = _score_person_email("eandersson", "erik", "andersson")
    _check("full-name outranks initial", full > init, True)

    # Diacritici nel nome.
    f, l = _name_parts("Görän Öström")
    _check("name normalisation", (f, l), ("goran", "ostrom"))

    # Page priority: on-domain kontakt è prioritario.
    _check(
        "page priority order",
        (
            _page_priority("https://savantmedia.se/kontakt", "savantmedia.se"),
            _page_priority("https://savantmedia.se/", "savantmedia.se"),
            _page_priority("https://hitta.se/kontakt", "savantmedia.se"),
            _page_priority("https://random.com/x", "savantmedia.se"),
        ),
        (0, 1, 2, 3),
    )

    # Ranking domain emails: personale prima del generico.
    ranked = rank_domain_emails(
        ["info@savantmedia.se", "lorenzo@savantmedia.se"], "savantmedia.se"
    )
    if check_email is not None:
        _check("personal outranks generic", ranked[0][0], "lorenzo@savantmedia.se")

    print()
    if failures:
        print(f"FAIL — {len(failures)} case(s):")
        for line in failures:
            print(line)
        raise SystemExit(1)
    print("PASS — all email_search helper cases covered.")
