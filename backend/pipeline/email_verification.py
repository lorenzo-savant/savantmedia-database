"""
Email verification rules — Reconcile pipeline (Fase 10).

Pure rule engine, no LLM, no I/O. Mirrors the logic in `lib/utils.ts`
`checkEmail()` so the backend behaves identically to the frontend.

Origin of the rules:
    `lorenzovault/Projects/🕷️ Web Scraping & SERP.md` — "Pipeline Validata".
    Validated on 292/548 leads in Lorenzo's B2B Contact Enrichment work.

Rule summary:
    Accept:
        - Email found textually in a public source.
        - Cross-domain only if company officially renamed (manual flag).
        - RocketReach (or similar) only when address visible without paywall.

    Reject:
        - Generic locals (`info@`, `kontakt@`, etc — see GENERIC_EMAIL_LOCALS).
        - Pattern-generated emails with no textual evidence.
        - Gmail/Hotmail/Yahoo etc. on a corporate domain.
        - Masked / paywall-only addresses.
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

# ─── Constants (kept in sync with `lib/types.ts`) ─────────────────────────────

GENERIC_EMAIL_LOCALS: frozenset[str] = frozenset(
    {
        "info",
        "kontakt",
        "hej",
        "hello",
        "post",
        "mail",
        "support",
        "admin",
        "sales",
        "marketing",
        "hr",
        "kundtjanst",
        "kundtjänst",
        "noreply",
        "no-reply",
        "office",
        "contact",
    }
)

SUSPICIOUS_PERSONAL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "hotmail.com",
        "hotmail.se",
        "yahoo.com",
        "yahoo.se",
        "outlook.com",
        "live.se",
        "icloud.com",
        "msn.com",
    }
)

_EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


# ─── Result model ─────────────────────────────────────────────────────────────


class EmailCheckResult(BaseModel):
    """Outcome of a single email verification."""

    valid: bool = False
    generic: bool = False
    suspicious_provider: bool = False
    domain_mismatch: bool = False
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _normalize_domain(raw: Optional[str]) -> str:
    """Mirror `lib/utils.ts` `normalizeDomain`."""
    if not raw:
        return ""
    d = raw.strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    d = d.split("/", 1)[0]
    d = d.split("?", 1)[0]
    return d


# ─── Main check ───────────────────────────────────────────────────────────────


def check_email(email: str, company_domain: Optional[str]) -> EmailCheckResult:
    """
    Apply the validated email-verification rules to a single (email, domain) pair.

    Confidence heuristic (rough — combined with linkedin evidence in reconcile):
        - 0.00   empty / invalid format / generic local
        - 0.40   suspicious personal provider (gmail.com, etc.)
        - 0.55   domain mismatch (cross-domain, manual review needed)
        - 0.85   valid + matching domain
    """
    result = EmailCheckResult()
    e = (email or "").strip().lower()

    if not e:
        result.reason = "Tom e-postadress"
        return result

    if not _EMAIL_REGEX.match(e):
        result.reason = "Ogiltigt format"
        return result

    local, _, domain = e.partition("@")

    if local in GENERIC_EMAIL_LOCALS:
        result.generic = True
        result.reason = f"Generisk adress ({local}@) — inte en personlig kontakt"
        return result

    if domain in SUSPICIOUS_PERSONAL_DOMAINS:
        result.suspicious_provider = True

    expected_domain = _normalize_domain(company_domain)
    if expected_domain and domain != expected_domain:
        # Allow subdomains (e.g. eu.example.com when expected is example.com).
        if not domain.endswith("." + expected_domain) and domain != expected_domain:
            result.domain_mismatch = True

    result.valid = True

    if result.suspicious_provider:
        result.reason = (
            f"Personlig domän ({domain}) — verifiera att det är professionell e-post"
        )
        result.confidence = 0.40
    elif result.domain_mismatch:
        result.reason = (
            f"Domänen ({domain}) matchar inte företagets domän ({expected_domain})"
        )
        result.confidence = 0.55
    else:
        result.confidence = 0.85

    return result


# ─── Self-tests ───────────────────────────────────────────────────────────────


if __name__ == "__main__":
    # Six cases exercising every rule branch.
    cases = [
        # 1. Generic local — must be rejected.
        (
            "info@savantmedia.se",
            "savantmedia.se",
            dict(valid=False, generic=True),
            "rejects generic info@",
        ),
        # 2. Valid corporate email matching domain.
        (
            "lorenzo@savantmedia.se",
            "savantmedia.se",
            dict(valid=True, generic=False, suspicious_provider=False, domain_mismatch=False),
            "accepts valid corporate email",
        ),
        # 3. Suspicious personal provider on corporate identity.
        (
            "lorenzo.dastoli@gmail.com",
            "savantmedia.se",
            dict(valid=True, suspicious_provider=True),
            "flags gmail.com as suspicious provider",
        ),
        # 4. Cross-domain mismatch.
        (
            "anna@othercompany.com",
            "savantmedia.se",
            dict(valid=True, domain_mismatch=True),
            "flags domain mismatch",
        ),
        # 5. Subdomain of corporate domain — should pass cleanly.
        (
            "anna@eu.savantmedia.se",
            "savantmedia.se",
            dict(valid=True, domain_mismatch=False),
            "accepts subdomain of corporate domain",
        ),
        # 6. Garbage input.
        (
            "not-an-email",
            "savantmedia.se",
            dict(valid=False),
            "rejects malformed address",
        ),
    ]

    failures: list[str] = []
    for idx, (email, domain, expected, label) in enumerate(cases, start=1):
        got = check_email(email, domain)
        for key, want in expected.items():
            actual = getattr(got, key)
            if actual != want:
                failures.append(
                    f"  [{idx}] {label}: expected {key}={want}, got {key}={actual} "
                    f"(reason={got.reason!r}, confidence={got.confidence})"
                )
                break
        else:
            print(f"  [{idx}] OK — {label} (reason={got.reason!r}, conf={got.confidence})")

    print()
    if failures:
        print(f"FAIL — {len(failures)} case(s):")
        for line in failures:
            print(line)
        raise SystemExit(1)
    print(f"PASS — {len(cases)} cases, all rules covered.")
