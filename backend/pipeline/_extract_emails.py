"""
Email / name / LinkedIn extraction utilities for the B2B enrichment pipeline.

Pure functions, no I/O — designed to run on plain-text excerpts produced by
the T2 scraper (`scrapers/httpbs.py`) or T3 (`crawl4ai_worker.py`).

These helpers implement the textual-evidence half of the validated
B2B Contact Enrichment playbook
(`lorenzovault/Projects/🕷️ Web Scraping & SERP.md` → "Pipeline Validata").
"""

from __future__ import annotations

import re

# ─── Regex constants ─────────────────────────────────────────────────────────

# Lowercase email regex — applied to already-lowercased text. We intentionally
# avoid Unicode letters in the local part because RFC-valid addresses in
# Swedish B2B almost always use ASCII; widening the charset would let CSS
# fragments and tracker IDs through.
_EMAIL_REGEX = re.compile(
    r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}",
    re.IGNORECASE,
)

# Person-name heuristic — Förnamn Efternamn (allows Å, Ä, Ö and common
# Scandinavian/European diacritics). Two tokens minimum, optional middle
# initial captured by a non-greedy run.
_NAME_REGEX = re.compile(
    r"\b[A-ZÅÄÖÉÈÜÆØ][a-zåäöéèüæø]+(?:\s+[A-ZÅÄÖÉÈÜÆØ][a-zåäöéèüæø]+){1,2}\b"
)

# Public LinkedIn profile URL — captures /in/ and /pub/ slugs.
_LINKEDIN_REGEX = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9\-_%]+",
    re.IGNORECASE,
)


# ─── E-mail obfuscation handling ─────────────────────────────────────────────
#
# Swedish "kontakt" pages routinely mangle addresses to dodge naive scrapers:
#   "lorenzo [at] savantmedia [dot] se"
#   "anna snabel-a savantmedia punkt se"   (snabel-a = Swedish "@",
#                                            punkt = "dot")
#   "namn @ domän . se"                    (spaced out)
# We rebuild the canonical form BEFORE the e-mail regex runs, recovering
# addresses that would otherwise be invisible. The reconstruction only fires
# on text that already has full e-mail *shape* (local + at + domain + dot +
# tld), so ordinary prose is left untouched.

# "Strong" @ tokens are unambiguous (a literal @, a bracketed [at], or the
# Swedish "snabel-a") so they may pair with a literal "." dot. The bare English
# word "at" is dangerous — "we meet at noon. Stop" has e-mail shape — so it is
# only honoured when the dot is ALSO a word ("dot"/"punkt"), never a bare ".".
_AT_STRONG = r"(?:@|\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|snabel[\s\-]?a)"
_AT_WORD = r"\bat\b"
_DOT_ANY = r"(?:\.|\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\bdot\b|\bpunkt\b)"
_DOT_WORD = r"(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\bdot\b|\bpunkt\b)"
_OBFUSCATED_LABEL = r"[a-z0-9\-]+"


def _email_shape(at_token: str, final_dot: str) -> str:
    return (
        r"[a-z0-9._%+\-]+"                                              # local
        r"\s*" + at_token + r"\s*"                                      # @ token
        + _OBFUSCATED_LABEL                                            # 1st label
        + r"(?:\s*" + _DOT_ANY + r"\s*" + _OBFUSCATED_LABEL + r")*"     # .labels
        + r"\s*" + final_dot + r"\s*"                                   # final dot
        + r"[a-z]{2,}"                                                  # tld
    )


_OBFUSCATED_EMAIL_REGEX = re.compile(
    "(?:"
    + _email_shape(_AT_STRONG, _DOT_ANY)
    + "|"
    + _email_shape(_AT_WORD, _DOT_WORD)
    + ")",
    re.IGNORECASE,
)
# Substitution patterns recognise every token form for reconstruction.
_AT_SUB_REGEX = re.compile(
    r"\s*(?:@|\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|snabel[\s\-]?a|\bat\b)\s*",
    re.IGNORECASE,
)
_DOT_SUB_REGEX = re.compile(r"\s*" + _DOT_ANY + r"\s*", re.IGNORECASE)


def _canonicalize_obfuscated(match: "re.Match[str]") -> str:
    """Collapse one obfuscated-address match back to ``local@domain.tld``."""
    raw = match.group(0)
    out = _AT_SUB_REGEX.sub("@", raw, count=1)
    out = _DOT_SUB_REGEX.sub(".", out)
    out = re.sub(r"\s+", "", out)
    return out.lower()


def deobfuscate_emails(text: str) -> str:
    """Rewrite obfuscated addresses to canonical ``local@domain.tld`` form.

    Handles ``[at]`` / ``(at)`` / ``snabel-a`` for ``@`` and
    ``[dot]`` / ``dot`` / ``punkt`` for ``.``, plus arbitrary surrounding
    whitespace. Text without full e-mail shape is returned unchanged, so this
    is safe to run on any excerpt before the e-mail regex.
    """
    if not text:
        return text or ""
    return _OBFUSCATED_EMAIL_REGEX.sub(_canonicalize_obfuscated, text)


# ─── Placeholder / asset noise ───────────────────────────────────────────────
# Addresses that are templates or sprite artefacts, not real contacts. Filtered
# out so they never reach the verification stage as false leads.

_PLACEHOLDER_LOCALS: frozenset[str] = frozenset(
    {
        "firstname.lastname", "first.last", "fornamn.efternamn",
        "förnamn.efternamn", "namn", "name", "ditt.namn", "your.name",
        "email", "e-mail", "epost", "e-post", "din-epost",
        "you", "your", "username", "user", "exempel", "example",
    }
)
_PLACEHOLDER_DOMAINS: frozenset[str] = frozenset(
    {
        "example.com", "example.org", "example.net", "domain.com",
        "domain.se", "foretag.se", "företag.se", "email.com",
        "yourcompany.com", "din-doman.se", "din-domän.se",
        "sentry.io", "wixpress.com", "schema.org",
    }
)
# When these are the final label, the "email" is really an image/asset path
# (e.g. logo@2x.png) or a bundler artefact.
_ASSET_TLDS: frozenset[str] = frozenset(
    {
        "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "tiff",
        "css", "js", "woff", "woff2", "mp4", "pdf",
    }
)


def _is_placeholder_email(email: str) -> bool:
    """True if `email` is a template, example, or asset artefact (not a lead)."""
    local, _, domain = email.partition("@")
    if not local or not domain:
        return True
    if domain.rsplit(".", 1)[-1] in _ASSET_TLDS:
        return True
    if local in _PLACEHOLDER_LOCALS:
        return True
    if domain in _PLACEHOLDER_DOMAINS:
        return True
    return False


# ─── Person-name validation ──────────────────────────────────────────────────
# A naive "Capitalised word + Capitalised word" regex captures menu items, page
# titles, company names, roles and geography — that is how junk like
# "Kontaktieren Sie", "Concert House", "South Industrial", "VD The Swedish" and
# "Plovdiv Bulgarien" ended up stored as decision-maker names. The token
# blocklists below disqualify those. Name particles (von/van/de/di/af) are
# intentionally NOT blocked — they appear in real names ("Carl von Sydow").

_NON_NAME_TOKENS: frozenset[str] = frozenset(
    {
        # roles / titles
        "vd", "ceo", "cfo", "cto", "coo", "owner", "ägare", "agare", "grundare",
        "founder", "cofounder", "president", "vice", "chef", "manager", "managing",
        "director", "direktör", "direktor", "styrelse", "styrelseledamot",
        "ledning", "ledningsgrupp", "ordförande", "ordforande", "partner",
        "partners", "konsult", "säljare", "saljare", "delägare", "delagare",
        # legal / company suffixes & business words
        "ab", "aktiebolag", "handelsbolag", "hb", "holding", "group", "groupen",
        "industries", "industrial", "industria", "solutions", "stiftelsen",
        "föreningen", "foreningen", "inc", "llc", "ltd", "gmbh", "ag", "kg",
        "oy", "oyj", "as", "asa", "bv", "sa", "spa", "srl", "co", "company",
        "corp", "corporation", "international", "sweden", "scandinavia", "nordic",
        # web / menu / boilerplate (sv/en/de)
        "kontakt", "kontakta", "kontaktieren", "contact", "sie", "händlerseite",
        "handlerseite", "hem", "home", "start", "om", "oss", "about", "team",
        "meny", "menu", "sök", "sok", "search", "cookies", "integritet",
        "privacy", "policy", "copyright", "sidan", "page", "sida", "läs", "las",
        "mer", "more", "read", "nyheter", "news", "blogg", "blog", "produkter",
        "products", "tjänster", "tjanster", "services", "logga", "login",
        "logout", "sitemap", "the", "and", "our", "your", "why", "how",
        "welcome", "välkommen", "valkommen", "house", "center", "centrum",
        "köpcentrum", "kopcentrum", "kvalitet", "quality", "concert", "south",
        "north", "east", "west", "stra", "straße", "strasse", "flooring",
        # contact-label words that look like a surname next to a Capitalised
        # company token (e.g. "Sinf Telefon", "Acme Epost")
        "telefon", "tel", "phone", "epost", "e-post", "fax", "mobil",
        "adress", "address", "vaxel", "växel", "reception", "support",
    }
)

# Country / region words that are not names (high-value subset seen in junk).
_GEO_TOKENS: frozenset[str] = frozenset(
    {
        "bulgarien", "bulgaria", "sverige", "stockholm", "göteborg", "goteborg",
        "malmö", "malmo", "uppsala", "plovdiv", "sofia", "deutschland", "germany",
        "norge", "danmark", "finland", "europe", "europa",
    }
)


def is_probable_person_name(name: str) -> bool:
    """Heuristic: does `name` look like a real person (Förnamn Efternamn)?

    Rejects menu items, page titles, company names, roles and geography that a
    naive capitalised-bigram regex captures (e.g. "Kontaktieren Sie",
    "Concert House", "South Industrial", "Plovdiv Bulgarien", "Hermods
    Kvalitet"). Accepts plausible 2–3 token human names, including ones with
    Scandinavian diacritics and lowercase particles ("Carl von Sydow").
    """
    if not name:
        return False
    n = name.strip()
    if len(n) < 5 or len(n) > 60 or "\n" in n or any(ch.isdigit() for ch in n):
        return False
    tokens = n.split()
    if len(tokens) < 2 or len(tokens) > 4:
        return False

    blocked = _NON_NAME_TOKENS | _GEO_TOKENS
    real_word_tokens = 0
    for tok in tokens:
        core = "".join(ch for ch in tok if ch.isalpha())
        low = core.lower()
        # Lowercase particles (von, van, de, di, af, la, le) are allowed but
        # don't count as a "real" name token.
        if tok[:1].islower():
            if low in {"von", "van", "de", "di", "af", "la", "le", "der", "den"}:
                continue
            return False  # other lowercase tokens → not a name
        if low in blocked:
            return False
        if len(core) < 2:
            return False  # initials / single letters
        if core.isupper():
            return False  # acronyms like "VD", "AB", "IT"
        real_word_tokens += 1

    return real_word_tokens >= 2


# ─── Public helpers ──────────────────────────────────────────────────────────


def find_emails_in_text(text: str) -> list[str]:
    """Return all unique e-mail addresses in `text`, lower-cased, in order.

    Parameters
    ----------
    text:
        Free-form plain text (typically `ScrapeResult.content_text`).

    Returns
    -------
    list[str]
        Lower-cased addresses, deduplicated while preserving first-seen
        order. Returns ``[]`` for empty / None input.
    """
    if not text:
        return []

    text = deobfuscate_emails(text)

    out: list[str] = []
    seen: set[str] = set()
    for raw in _EMAIL_REGEX.findall(text):
        e = raw.lower().strip(".,;:()[]{}<>\"' \t\r\n")
        if not e or e in seen:
            continue
        if _is_placeholder_email(e):
            continue
        seen.add(e)
        out.append(e)
    return out


def find_name_near_email(
    text: str,
    email: str,
    *,
    window: int = 200,
) -> str | None:
    """Return a plausible person name near the first occurrence of `email`.

    Looks for a `Förnamn Efternamn` token (optional middle name) within
    ``window`` characters *before* the email — that is the dominant layout
    in Swedish team / kontakt pages (name on the line above the address).
    Falls back to the same-size window *after* the email if nothing is
    found before.

    Parameters
    ----------
    text:
        Plain-text excerpt to search.
    email:
        The address whose neighbourhood we inspect (case-insensitive).
    window:
        Number of characters to inspect on each side. Default 200 matches
        the validated playbook (one short paragraph).

    Returns
    -------
    str | None
        The closest matching name, or ``None`` if nothing plausible found.
    """
    if not text or not email:
        return None

    lower_text = text.lower()
    lower_email = email.lower()
    pos = lower_text.find(lower_email)
    if pos == -1:
        return None

    # Window before the email — scan from the END so the closest name wins,
    # skipping candidates that fail the person-name heuristic (menu items,
    # company names, roles, geography).
    start = max(0, pos - window)
    before = text[start:pos]
    for m in reversed(list(_NAME_REGEX.finditer(before))):
        cand = m.group(0)
        if is_probable_person_name(cand):
            return cand

    # Fallback: window after the email — closest valid one first.
    end_email = pos + len(email)
    after = text[end_email : end_email + window]
    for m in _NAME_REGEX.finditer(after):
        cand = m.group(0)
        if is_probable_person_name(cand):
            return cand

    return None


def find_linkedin_in_text(text: str) -> str | None:
    """Return the first public LinkedIn profile URL in `text`, or ``None``.

    Only matches `/in/<slug>` and `/pub/<slug>` URLs — company pages,
    posts, and feed links are intentionally excluded because the
    enrichment playbook only treats personal profiles as corroborating
    evidence for an email.
    """
    if not text:
        return None
    match = _LINKEDIN_REGEX.search(text)
    return match.group(0) if match else None


# ─── Self-tests ───────────────────────────────────────────────────────────────


if __name__ == "__main__":
    failures: list[str] = []

    def _check(label: str, got, want) -> None:
        if got != want:
            failures.append(f"  {label}: expected {want!r}, got {got!r}")
        else:
            print(f"  OK — {label}")

    # Plain extraction still works and dedups in order.
    _check(
        "plain + dedup",
        find_emails_in_text("Maila lorenzo@savantmedia.se eller LORENZO@savantmedia.se"),
        ["lorenzo@savantmedia.se"],
    )
    # Bracketed obfuscation.
    _check(
        "[at]/[dot] obfuscation",
        find_emails_in_text("Kontakt: lorenzo [at] savantmedia [dot] se"),
        ["lorenzo@savantmedia.se"],
    )
    # Swedish snabel-a / punkt + spaced.
    _check(
        "snabel-a / punkt",
        find_emails_in_text("anna snabel-a savantmedia punkt se"),
        ["anna@savantmedia.se"],
    )
    _check(
        "spaced @ and dot",
        find_emails_in_text("erik @ savantmedia . se"),
        ["erik@savantmedia.se"],
    )
    # Placeholder + asset noise are dropped.
    _check(
        "placeholder/asset filtered",
        find_emails_in_text(
            "firstname.lastname@example.com och logo@2x.png men info@savantmedia.se"
        ),
        ["info@savantmedia.se"],
    )
    # Prose with no e-mail shape is untouched (no false positives).
    _check(
        "prose untouched",
        find_emails_in_text("We meet at noon. Stop by the office dot."),
        [],
    )
    # Name found above the address.
    _check(
        "name near email",
        find_name_near_email("Erik Andersson\nerik@savantmedia.se", "erik@savantmedia.se"),
        "Erik Andersson",
    )

    # ── Person-name validation: junk from real bad results must be rejected ──
    for junk in [
        "Kontaktieren Sie", "Händlerseite Von", "Plovdiv Bulgarien",
        "South Industrial", "Concert House", "Flooring Inc",
        "Hermods Kvalitet", "VD The Swedish", "Nedyalka Shileva Stra",
        "Sinf Telefon", "Acme Epost", "Bolaget Adress",
    ]:
        _check(f"reject junk {junk!r}", is_probable_person_name(junk), False)
    for real in [
        "Erik Andersson", "Fredrik Åkermark", "Hans Edler",
        "Mats Göte Gullborn", "Carl von Sydow",
    ]:
        _check(f"accept name {real!r}", is_probable_person_name(real), True)
    # A real name glued to junk via newline must be rejected (was being stored).
    _check(
        "reject name+junk newline",
        is_probable_person_name("Fredrik Åkermark\nVD The Swedish"),
        False,
    )

    print()
    if failures:
        print(f"FAIL — {len(failures)} case(s):")
        for line in failures:
            print(line)
        raise SystemExit(1)
    print("PASS — all extraction cases covered.")
