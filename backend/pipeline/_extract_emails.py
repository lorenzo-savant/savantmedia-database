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

    out: list[str] = []
    seen: set[str] = set()
    for raw in _EMAIL_REGEX.findall(text):
        e = raw.lower().strip(".,;:()[]{}<>\"' \t\r\n")
        if not e or e in seen:
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

    # Window before the email — search from the END so the closest name wins.
    start = max(0, pos - window)
    before = text[start:pos]
    matches_before = list(_NAME_REGEX.finditer(before))
    if matches_before:
        return matches_before[-1].group(0)

    # Fallback: window after the email — closest one first.
    end_email = pos + len(email)
    after = text[end_email : end_email + window]
    match_after = _NAME_REGEX.search(after)
    if match_after:
        return match_after.group(0)

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
