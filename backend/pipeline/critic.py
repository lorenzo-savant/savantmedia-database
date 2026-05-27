"""
Critic node — Fase 10b (`docs/ARCHITECTURE.md` §6 + §11).

LangGraph-compatible async node. Consumes the output of `reconcile_contacts`
and produces a per-contact decision: 'accept' | 'flag_for_review' | 'reject'
plus a one-line `critic_note`.

# Pattern researcher/executor/critic from microsoft/autogen 0.2 — adapted
# as LangGraph node, ZERO cost (local LLM via Ollama).

Behaviour:
    - Tries to call Ollama at `http://localhost:11434/api/generate` with
      model `llama3.1:8b` for each reconciled contact.
    - On any httpx connection error (Ollama not running) or non-2xx, falls
      back to a deterministic rule-based decision derived from the
      `email_check` fields. The fallback is the source of truth in CI.

The node never raises — it always returns a decision per contact so the
graph can keep moving.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

import httpx

from .reconcile import ReconcileResult

log = logging.getLogger(__name__)

Decision = Literal["accept", "flag_for_review", "reject"]

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_TIMEOUT_SECONDS = 10.0

_PROMPT_TEMPLATE = """You are a data quality critic for a Swedish B2B database.
The following contact came from web scraping.

Verification rules (validated on 292/548 real leads):
- ACCEPT: email found textually in a public source, matches company domain,
  ideally corroborated by LinkedIn.
- FLAG_FOR_REVIEW: email is plausible but evidence is weak — domain mismatch
  due to possible rebrand, suspicious personal provider (gmail.com etc.)
  on a corporate identity, or missing corroborating signal.
- REJECT: generic local (info@, kontakt@, support@, ...), malformed address,
  or pattern-generated email with no textual evidence.

Contact:
  namn:        {namn}
  roll:        {roll}
  email:       {email}
  linkedin:    {linkedin}
  source_url:  {source_url}

Rule engine output:
  valid:               {valid}
  generic:             {generic}
  suspicious_provider: {suspicious}
  domain_mismatch:     {mismatch}
  reason:              {reason}
  confidence:          {confidence:.2f}

Reply with strict JSON only, no prose, no markdown:
{{"decision": "accept" | "flag_for_review" | "reject", "critic_note": "<one short line>"}}
"""


# ─── Rule-based fallback ──────────────────────────────────────────────────────


def _rule_based_decision(r: ReconcileResult) -> tuple[Decision, str]:
    """
    Deterministic decision used when Ollama is unreachable. Keeps the
    pipeline operational with zero external dependencies.
    """
    chk = r.email_check

    if not chk.valid:
        if chk.generic:
            return "reject", f"Generic local email: {chk.reason}"
        return "reject", chk.reason or "Invalid email"

    if chk.suspicious_provider or chk.domain_mismatch:
        bits: list[str] = []
        if chk.suspicious_provider:
            bits.append("personal provider")
        if chk.domain_mismatch:
            bits.append("domain mismatch")
        return "flag_for_review", "Needs human review: " + ", ".join(bits)

    if r.suggested_verifierad:
        return "accept", f"Verified via {r.suggested_verifieringsmetod or 'rules'}"

    return "flag_for_review", "Valid email but no corroborating evidence"


# ─── Ollama call ──────────────────────────────────────────────────────────────


async def _ask_ollama(r: ReconcileResult) -> tuple[Decision, str] | None:
    """Return None on any failure so the caller can fall back to rules."""
    prompt = _PROMPT_TEMPLATE.format(
        namn=r.contact.namn or "(saknas)",
        roll=r.contact.roll or "(saknas)",
        email=r.contact.email or "(saknas)",
        linkedin=r.contact.linkedin_url or "(saknas)",
        source_url=r.contact.source_url or "(saknas)",
        valid=r.email_check.valid,
        generic=r.email_check.generic,
        suspicious=r.email_check.suspicious_provider,
        mismatch=r.email_check.domain_mismatch,
        reason=r.email_check.reason or "(none)",
        confidence=r.email_check.confidence,
    )

    payload: dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }

    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
        if resp.status_code >= 300:
            log.info("Ollama returned %s — falling back to rules", resp.status_code)
            return None
        body = resp.json()
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.HTTPError) as exc:
        log.info("Ollama unreachable (%s) — falling back to rules", exc.__class__.__name__)
        return None
    except Exception as exc:  # noqa: BLE001 — defensive: critic must never raise
        log.warning("Unexpected Ollama failure (%r) — falling back to rules", exc)
        return None

    raw = body.get("response", "") if isinstance(body, dict) else ""
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.info("Ollama returned non-JSON payload — falling back to rules")
        return None

    decision = parsed.get("decision")
    note = parsed.get("critic_note") or ""
    if decision not in {"accept", "flag_for_review", "reject"}:
        return None
    return decision, str(note).strip() or "(no note)"


# ─── Public node ──────────────────────────────────────────────────────────────


async def critic_node(state: dict) -> dict:
    """
    LangGraph node.

    Input state keys:
        reconciled: list[ReconcileResult]   # produced by reconcile_contacts

    Output state keys (merged in):
        critic_decisions: list[dict]
            {contact_id, decision, critic_note}
        critic_backend: 'ollama' | 'rules'  # which path produced the decisions
    """
    reconciled: list[ReconcileResult] = state.get("reconciled", []) or []
    decisions: list[dict[str, Any]] = []
    used_ollama = False
    ollama_down = False

    for idx, item in enumerate(reconciled):
        result: tuple[Decision, str] | None = None

        if not ollama_down:
            result = await _ask_ollama(item)
            if result is None:
                # Don't keep retrying once we know the service is down.
                ollama_down = True
            else:
                used_ollama = True

        if result is None:
            result = _rule_based_decision(item)

        decision, note = result
        contact_id = (item.contact.email or f"contact-{idx}").strip().lower()
        decisions.append(
            {
                "contact_id": contact_id,
                "decision": decision,
                "critic_note": note,
            }
        )

    return {
        **state,
        "critic_decisions": decisions,
        "critic_backend": "ollama" if used_ollama else "rules",
    }
