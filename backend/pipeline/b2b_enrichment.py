"""
B2B Contact Enrichment pipeline — Fase 13 (`docs/ARCHITECTURE.md` §8).

Re-implementation of Lorenzo's validated B2B enrichment pipeline (originally
driven by Claude Code sub-agents) as a callable async Python tool that the
LangGraph orchestrator can invoke whenever a plan step needs personal contact
emails for Swedish companies.

Validated on **292 out of 548 real leads** (vault:
`Projects/🕷️ Web Scraping & SERP.md` → "Pipeline Validata — B2B Contact
Enrichment"). The rules in `email_verification.py` and the dedup heuristics
in `reconcile.py` are *the* rules that hit that 53 % verified-email rate.

Pipeline shape (per company):

    1. Build up to N SERP queries combining ceo_name + domain + Swedish
       contact-page keywords ("kontakt", "e-post", "LinkedIn").
    2. T1 — SearXNG meta-search per query (graceful when SearXNG is down).
    3. Dedup result URLs + cap at `max_pages`.
    4. T2 — httpx + trafilatura fetch each URL.
    5. Also hit the canonical contact paths directly:
            https://<domain>/kontakt
            https://<domain>/om-oss
            https://<domain>/team
    6. Extract email + name + LinkedIn from every content_text.
    7. Build Contact candidates (namn = detected name or ceo_name fallback,
       roll = "VD" if the detected name matches the supplied ceo_name).
    8. Pass through `reconcile_contacts` to apply the validated rules.
    9. Optionally re-check through the Critic node (Ollama) when reachable.

The pipeline NEVER raises end-to-end: SearXNG, T2 fetches, and the Critic
node are all wrapped so a partial network outage still produces a result
with a populated `audit_trail`.

Concurrency: `enrich_batch` runs companies in parallel with a `Semaphore`
capped at **7** — vault lesson, anything higher tripped per-IP rate limits
on the dominant Swedish hosts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field

from enrichment.escalate import fetch_with_escalation
from scrapers.searxng import SearXNGClient

from ._extract_emails import (
    find_emails_in_text,
    find_linkedin_in_text,
    find_name_near_email,
)
from .critic import critic_node
from .email_verification import _normalize_domain
from .reconcile import Contact, ReconcileResult, reconcile_contacts

log = logging.getLogger("savantsdatabas.pipeline.b2b_enrichment")

# ─── Constants (tuned in the vault) ──────────────────────────────────────────

_DEFAULT_MAX_QUERIES = 3
_DEFAULT_MAX_PAGES = 4
_DEFAULT_MAX_PARALLEL = 7

# Canonical Swedish contact / about / team paths, in priority order.
_CANONICAL_CONTACT_PATHS: tuple[str, ...] = (
    "/kontakt",
    "/kontakta-oss",
    "/om-oss",
    "/team",
    "/medarbetare",
    "/contact",
)


# ─── Pydantic models ─────────────────────────────────────────────────────────


class EnrichmentTarget(BaseModel):
    """One company to enrich.

    Attributes
    ----------
    company_name:
        Free-form company name (e.g. "Savant Media AB"). Required.
    domain:
        Canonical web domain ("savantmedia.se"). Required. URL-prefix
        and `www.` are normalised away before queries.
    ceo_name:
        Person to specifically look for. Optional — when supplied,
        we add `"<ceo_name>"`-shaped queries and bias the role to "VD".
    org_nr:
        Swedish organisationsnummer, kept for audit linkage to
        `public.companies`. Not used by the pipeline itself.
    """

    company_name: str
    domain: str
    ceo_name: str | None = None
    org_nr: str | None = None


class EnrichmentResult(BaseModel):
    """Outcome of enriching one company.

    Attributes
    ----------
    target:
        Echo of the input — keeps the result self-contained.
    discovered_contacts:
        Per-contact reconcile outputs (from `pipeline.reconcile`). The
        critic decision, if any, is merged via `critic_decisions`.
    verification_summary:
        Aggregated counts so the agent / cockpit can show a one-glance
        score: ``{"total": N, "valid": N, "accepted": N, "flagged": N,
        "rejected": N, "critic_backend": "ollama"|"rules"|"skipped"}``.
    audit_trail:
        Free-form step-by-step log. Mirrors the audit columns we want to
        persist into `public.sources`.
    critic_decisions:
        Output of `critic_node` for each reconciled contact. Empty when
        Ollama was skipped (e.g. opt-out or unreachable and the caller
        only wanted rule-based pass).
    """

    target: EnrichmentTarget
    discovered_contacts: list[ReconcileResult] = Field(default_factory=list)
    verification_summary: dict[str, Any] = Field(default_factory=dict)
    audit_trail: list[str] = Field(default_factory=list)
    critic_decisions: list[dict[str, Any]] = Field(default_factory=list)


# ─── Internal helpers ────────────────────────────────────────────────────────


def _build_queries(target: EnrichmentTarget, max_queries: int) -> list[str]:
    """Build SERP queries in vault-validated order."""
    domain = _normalize_domain(target.domain)
    queries: list[str] = []
    if target.ceo_name:
        queries.append(f'"{target.ceo_name}" "{domain}" email')
    queries.append(f'"{domain}" kontakt e-post')
    if target.ceo_name:
        queries.append(f'"{target.ceo_name}" LinkedIn email')
    queries.append(f'"{target.company_name}" kontakt e-post')
    return queries[: max(1, max_queries)]


def _canonical_contact_urls(domain: str) -> list[str]:
    """Return canonical https://<domain><path> URLs to try directly."""
    d = _normalize_domain(domain)
    if not d:
        return []
    return [f"https://{d}{path}" for path in _CANONICAL_CONTACT_PATHS]


def _names_match(a: str | None, b: str | None) -> bool:
    """Loose first+last name match (case-insensitive, whitespace-collapsed)."""
    if not a or not b:
        return False
    norm_a = " ".join(a.lower().split())
    norm_b = " ".join(b.lower().split())
    if not norm_a or not norm_b:
        return False
    return norm_a == norm_b or norm_a in norm_b or norm_b in norm_a


async def _t1_collect_urls(
    queries: list[str],
    *,
    max_pages: int,
    audit: list[str],
) -> list[str]:
    """Run SearXNG over `queries` and return up to `max_pages` deduped URLs.

    Never raises — SearXNG client already returns an error-bearing result
    on connection failure, which we log into `audit` and skip.
    """
    client = SearXNGClient()
    seen: set[str] = set()
    ordered: list[str] = []

    for q in queries:
        try:
            results = await client.search(q, limit=10)
        except Exception as exc:  # noqa: BLE001 — defensive, should never trigger
            audit.append(f"T1 search FAILED for {q!r}: {exc!r}")
            continue

        if results and results[0].error and not results[0].url:
            audit.append(f"T1 search '{q}' → {results[0].error}")
            continue

        added = 0
        for r in results:
            if not r.ok or not r.url:
                continue
            if r.url in seen:
                continue
            seen.add(r.url)
            ordered.append(r.url)
            added += 1
        audit.append(f"T1 search '{q}' → {added} new URL(s)")

        if len(ordered) >= max_pages:
            break

    return ordered[:max_pages]


async def _t2_fetch(url: str, audit: list[str]) -> tuple[str, str | None, dict]:
    """Fetch `url` con escalation automatica T2→T4 su blocco (403/503/captcha).

    `content_text` is ``None`` on any error (logged into `audit`).
    `metadata` always carries at least the tier marker.
    """
    try:
        result = await fetch_with_escalation(url, max_attempts=2)
    except Exception as exc:  # noqa: BLE001 — defensive
        audit.append(f"fetch {url} FAILED hard: {exc!r}")
        return url, None, {"tier": 0, "fatal_error": str(exc)}

    if not result.ok:
        audit.append(f"fetch {url} → {result.error}")
        return url, None, {"tier": result.tier, "error": result.error, **(result.metadata or {})}

    audit.append(
        f"fetch {url} (T{result.tier}) → {len(result.content_text or '')} chars"
    )
    return url, (result.content_text or ""), {"tier": result.tier, **(result.metadata or {})}


def _harvest_contacts(
    target: EnrichmentTarget,
    page_url: str,
    text: str,
    audit: list[str],
) -> list[Contact]:
    """Extract Contact candidates from a single page's text."""
    emails = find_emails_in_text(text)
    if not emails:
        return []

    linkedin_url = find_linkedin_in_text(text) or ""
    contacts: list[Contact] = []

    for email in emails:
        detected_name = find_name_near_email(text, email)
        namn = detected_name or (target.ceo_name or "")
        roll = ""
        if target.ceo_name and _names_match(detected_name, target.ceo_name):
            roll = "VD"

        contacts.append(
            Contact(
                namn=namn,
                roll=roll,
                email=email,
                telefon="",
                linkedin_url=linkedin_url,
                source_url=page_url,
            )
        )

    audit.append(
        f"harvest {page_url}: {len(emails)} email(s), "
        f"linkedin={'yes' if linkedin_url else 'no'}"
    )
    return contacts


def _summarize(
    reconciled: list[ReconcileResult],
    critic_decisions: list[dict[str, Any]],
    critic_backend: str,
) -> dict[str, Any]:
    """Build the verification_summary dict for the cockpit."""
    total = len(reconciled)
    valid = sum(1 for r in reconciled if r.email_check.valid)
    accepted = sum(1 for d in critic_decisions if d.get("decision") == "accept")
    flagged = sum(
        1 for d in critic_decisions if d.get("decision") == "flag_for_review"
    )
    rejected = sum(1 for d in critic_decisions if d.get("decision") == "reject")
    return {
        "total": total,
        "valid": valid,
        "accepted": accepted,
        "flagged": flagged,
        "rejected": rejected,
        "critic_backend": critic_backend,
    }


# ─── Public entry points ─────────────────────────────────────────────────────


async def enrich_b2b(
    target: EnrichmentTarget,
    *,
    max_queries: int = _DEFAULT_MAX_QUERIES,
    max_pages: int = _DEFAULT_MAX_PAGES,
    run_critic: bool = True,
) -> EnrichmentResult:
    """Run the validated B2B Contact Enrichment pipeline for one company.

    The function never raises. When SearXNG is down it falls back to the
    canonical contact-path probe; when T2 is throttled it returns whatever
    it managed to scrape; when Ollama is unreachable the Critic node falls
    back to deterministic rules.

    Parameters
    ----------
    target:
        The company to enrich. ``domain`` is required, ``ceo_name`` is
        strongly recommended (it drives the SERP queries).
    max_queries:
        Cap on SearXNG queries. Vault default = 3.
    max_pages:
        Cap on URLs we'll T2-fetch per company. Vault default = 4.
    run_critic:
        If False, skip the Critic node entirely (no Ollama call, no
        rule-based fallback decisions either) — useful when the caller
        wants to merge multiple sources before scoring.

    Returns
    -------
    EnrichmentResult
        Fully populated with discovered contacts, summary counts, and an
        audit trail mirroring the steps logged.
    """
    audit: list[str] = []
    audit.append(
        f"enrich_b2b start: company={target.company_name!r}, "
        f"domain={target.domain!r}, ceo={target.ceo_name!r}, "
        f"max_queries={max_queries}, max_pages={max_pages}"
    )

    domain = _normalize_domain(target.domain)
    if not domain:
        audit.append("ABORT: empty / invalid domain")
        return EnrichmentResult(
            target=target,
            verification_summary={
                "total": 0,
                "valid": 0,
                "accepted": 0,
                "flagged": 0,
                "rejected": 0,
                "critic_backend": "skipped",
            },
            audit_trail=audit,
        )

    # ─── Step 1+2: build queries + T1 search ────────────────────────────────
    queries = _build_queries(target, max_queries)
    audit.append(f"built {len(queries)} SERP query(ies)")
    serp_urls = await _t1_collect_urls(queries, max_pages=max_pages, audit=audit)
    audit.append(f"T1 yielded {len(serp_urls)} URL(s) total")

    # ─── Step 5: canonical contact paths (always tried) ─────────────────────
    canonical = _canonical_contact_urls(domain)
    audit.append(f"queued {len(canonical)} canonical contact path(s)")

    # Dedup full URL set, preserving SERP-first ordering.
    seen: set[str] = set()
    all_urls: list[str] = []
    for u in serp_urls + canonical:
        if u not in seen:
            seen.add(u)
            all_urls.append(u)
    # Cap once more so canonical paths don't blow past max_pages.
    fetch_budget = max(max_pages, len(canonical))
    all_urls = all_urls[:fetch_budget]
    audit.append(f"final fetch budget: {len(all_urls)} URL(s)")

    # ─── Step 4: T2 fetch all URLs sequentially-ish ─────────────────────────
    # Sequential per-company keeps the in-process Semaphore at the batch
    # level meaningful (vault: 7 parallel companies; each company stays
    # polite internally).
    candidates: list[Contact] = []
    for url in all_urls:
        _, text, _meta = await _t2_fetch(url, audit)
        if not text:
            continue
        candidates.extend(_harvest_contacts(target, url, text, audit))

    audit.append(f"harvested {len(candidates)} raw contact candidate(s)")

    # ─── Step 8: reconcile via validated rules ──────────────────────────────
    reconciled = reconcile_contacts(candidates, domain)
    audit.append(
        f"reconcile_contacts: {len(reconciled)} result(s), "
        f"{sum(1 for r in reconciled if r.suggested_verifierad)} suggested verifierad"
    )

    # ─── Step 9: optional Critic re-check ───────────────────────────────────
    critic_decisions: list[dict[str, Any]] = []
    critic_backend = "skipped"
    if run_critic and reconciled:
        try:
            critic_out = await critic_node({"reconciled": reconciled})
            critic_decisions = critic_out.get("critic_decisions") or []
            critic_backend = critic_out.get("critic_backend") or "rules"
            audit.append(
                f"critic_node: {len(critic_decisions)} decision(s) "
                f"via backend={critic_backend!r}"
            )
        except Exception as exc:  # noqa: BLE001 — critic_node should already swallow
            audit.append(f"critic_node FAILED unexpectedly: {exc!r}")

    summary = _summarize(reconciled, critic_decisions, critic_backend)
    audit.append(f"summary: {summary}")

    return EnrichmentResult(
        target=target,
        discovered_contacts=reconciled,
        verification_summary=summary,
        audit_trail=audit,
        critic_decisions=critic_decisions,
    )


async def enrich_batch(
    targets: list[EnrichmentTarget],
    *,
    max_parallel: int = _DEFAULT_MAX_PARALLEL,
    max_queries: int = _DEFAULT_MAX_QUERIES,
    max_pages: int = _DEFAULT_MAX_PAGES,
    run_critic: bool = True,
) -> list[EnrichmentResult]:
    """Run `enrich_b2b` over many companies, in parallel.

    Concurrency is capped at `max_parallel` (default = 7, vault-validated
    upper bound before per-IP rate limits on Swedish hosts kick in).

    Returns a list aligned with `targets` — same length, same order, even
    when individual companies fail (failures still produce an
    `EnrichmentResult` with the error logged in `audit_trail`).
    """
    if not targets:
        return []

    sem = asyncio.Semaphore(max(1, max_parallel))

    async def _one(t: EnrichmentTarget) -> EnrichmentResult:
        async with sem:
            try:
                return await enrich_b2b(
                    t,
                    max_queries=max_queries,
                    max_pages=max_pages,
                    run_critic=run_critic,
                )
            except Exception as exc:  # noqa: BLE001 — last-resort safety net
                log.exception("enrich_b2b failed unexpectedly for %r", t.domain)
                return EnrichmentResult(
                    target=t,
                    verification_summary={
                        "total": 0,
                        "valid": 0,
                        "accepted": 0,
                        "flagged": 0,
                        "rejected": 0,
                        "critic_backend": "skipped",
                    },
                    audit_trail=[f"FATAL: {exc!r}"],
                )

    return await asyncio.gather(*(_one(t) for t in targets))
