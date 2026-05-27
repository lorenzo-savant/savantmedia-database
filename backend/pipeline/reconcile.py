"""
Reconcile pipeline — Fase 10 (`docs/ARCHITECTURE.md` §6).

Applies the validated email-verification rules to a batch of scraped
Contact candidates and produces an auditable decision per contact.

Heuristics layered on top of `check_email`:
    - Dedup by lowercased email (first occurrence wins, others noted).
    - LinkedIn URL present → boost confidence and, if the email is valid,
      mark the contact as `suggested_verifierad=True` with method='linkedin'.
    - Valid email + matching corporate domain (no LinkedIn) → suggest
      verifierad=True with method='foretagswebbplats' if the source URL
      lives on the same domain.
    - Suspicious/mismatch/generic → suggest verifierad=False with an
      explanatory audit line for the Critic node downstream.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .email_verification import (
    EmailCheckResult,
    _normalize_domain,
    check_email,
)

# ─── Models ───────────────────────────────────────────────────────────────────


class Contact(BaseModel):
    """A scraped contact candidate prior to reconcile."""

    namn: str = ""
    roll: str = ""
    email: str = ""
    telefon: str = ""
    linkedin_url: str = ""
    source_url: str = ""


class ReconcileResult(BaseModel):
    """Per-contact decision plus the full audit trail."""

    contact: Contact
    email_check: EmailCheckResult
    suggested_verifierad: bool = False
    suggested_verifieringsmetod: str = ""
    notes: list[str] = Field(default_factory=list)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _source_domain(source_url: str) -> str:
    if not source_url:
        return ""
    try:
        host = urlparse(source_url).hostname or ""
    except ValueError:
        return ""
    return _normalize_domain(host)


def _has_linkedin(url: str) -> bool:
    if not url:
        return False
    u = url.strip().lower()
    return "linkedin.com/in/" in u


# ─── Main entry point ─────────────────────────────────────────────────────────


def reconcile_contacts(
    contacts: list[Contact],
    company_domain: Optional[str],
) -> list[ReconcileResult]:
    """
    Reconcile a batch of scraped contacts against the validated rules.

    The function is pure (no I/O) so it can be unit-tested and run inside
    a LangGraph node without side effects. The Critic node (see critic.py)
    consumes its output.
    """
    expected_domain = _normalize_domain(company_domain)
    results: list[ReconcileResult] = []
    seen_emails: set[str] = set()

    for contact in contacts:
        email_key = (contact.email or "").strip().lower()
        notes: list[str] = []

        # ── Dedup ────────────────────────────────────────────────────────────
        if email_key and email_key in seen_emails:
            res = ReconcileResult(
                contact=contact,
                email_check=EmailCheckResult(
                    reason="Duplikat — adressen finns redan i batchen",
                ),
                suggested_verifierad=False,
                suggested_verifieringsmetod="",
                notes=[f"Duplicate email '{email_key}' — kept first occurrence only"],
            )
            results.append(res)
            continue
        if email_key:
            seen_emails.add(email_key)

        # ── Email rule check ────────────────────────────────────────────────
        check = check_email(contact.email, expected_domain)
        notes.append(f"email_check: valid={check.valid}, reason={check.reason!r}")

        suggested_verifierad = False
        suggested_method = ""
        confidence = check.confidence

        # ── LinkedIn evidence boost ─────────────────────────────────────────
        has_li = _has_linkedin(contact.linkedin_url)
        if has_li:
            notes.append("linkedin URL present — boosting confidence")
            confidence = min(1.0, confidence + 0.10)

        # ── Suggest verifierad ──────────────────────────────────────────────
        if check.valid and not check.generic:
            if has_li:
                suggested_verifierad = True
                suggested_method = "linkedin"
                notes.append("→ suggest verifierad=True via linkedin")
            elif (
                not check.suspicious_provider
                and not check.domain_mismatch
                and expected_domain
                and _source_domain(contact.source_url) == expected_domain
            ):
                suggested_verifierad = True
                suggested_method = "foretagswebbplats"
                notes.append(
                    "→ suggest verifierad=True via foretagswebbplats "
                    "(source_url on corporate domain)"
                )
            else:
                notes.append(
                    "→ valid email but no corroborating evidence — leave "
                    "verifierad=False for Critic review"
                )
        else:
            notes.append("→ email failed rules — verifierad=False")

        # Persist tuned confidence back onto the check result.
        check.confidence = confidence

        results.append(
            ReconcileResult(
                contact=contact,
                email_check=check,
                suggested_verifierad=suggested_verifierad,
                suggested_verifieringsmetod=suggested_method,
                notes=notes,
            )
        )

    return results
