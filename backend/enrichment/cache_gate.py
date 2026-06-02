"""
Cache-first gate con TTL — Fase 16 (anti-fragile hardening).

Prima di lanciare qualsiasi tier di scraping, questo gate controlla se
l'azienda ha già dati freschi (enriched_at + TTL configurabile).

Pattern:
    gate = CacheGate()
    verdict = await gate.check(company_id)
    if verdict.source == "cache":
        return  # salta scraping, usa dati esistenti
    # ... esegui scraping ...
    await gate.mark_enriched(company_id, tier, source_url)

Logga hit/miss su `enrichment_logs` e aggiorna `enriched_at` sulla company.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from supabase import Client

logger = logging.getLogger("savantsdatabas.enrichment.cache_gate")

_TTL_DAYS = int(os.environ.get("ENRICH_TTL_DAYS", "30"))


class CacheVerdict:
    """Risultato del cache gate.

    Attributes
    ----------
    source:
        "cache" se i dati sono freschi, "miss" se serve scrapare.
    company_id:
        UUID della company verificata.
    enriched_at:
        Timestamp dell'ultimo arricchimento (None se mai arricchita).
    ttl_days:
        TTL configurato usato per la decisione.
    reason:
        Motivazione leggibile del verdetto (per log/audit).
    """

    def __init__(
        self,
        source: str,
        company_id: str,
        enriched_at: datetime | None = None,
        ttl_days: int = _TTL_DAYS,
        reason: str = "",
    ) -> None:
        self.source = source
        self.company_id = company_id
        self.enriched_at = enriched_at
        self.ttl_days = ttl_days
        self.reason = reason


class CacheGate:
    """Cache-first gate per evitare ri-scraping di company già arricchite.

    Uso base:
        gate = CacheGate(supabase_client)
        verdict = await gate.check(company_id, force_ttl_days=15)
        if verdict.source == "cache":
            logger.info("Cache HIT: %s", verdict.reason)
            return cached_data
        # ... fai scraping ...
        gate.mark_enriched(company_id, tier=2, source_url="...")

    Parametri
    ---------
    sb:
        Client Supabase autenticato (service-role).
    default_ttl_days:
        Giorni di freschezza prima di considerare i dati stale.
        Sovrascrivibile via env ENRICH_TTL_DAYS o per-call con force_ttl_days.
    """

    def __init__(
        self,
        sb: Client,
        default_ttl_days: int = _TTL_DAYS,
    ) -> None:
        self._sb = sb
        self._default_ttl_days = int(default_ttl_days)

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    async def check(
        self,
        company_id: str,
        *,
        force_ttl_days: int | None = None,
    ) -> CacheVerdict:
        """Verifica se la company ha dati freschi in cache.

        Returns
        -------
        CacheVerdict con source="cache" se i dati sono freschi,
        source="miss" altrimenti.
        """
        ttl = force_ttl_days if force_ttl_days is not None else self._default_ttl_days

        company = await self._fetch_company(company_id)
        if company is None:
            return CacheVerdict(
                source="miss",
                company_id=company_id,
                ttl_days=ttl,
                reason="company not found in DB",
            )

        enriched_at = company.get("enriched_at")
        foretagsnamn = company.get("foretagsnamn") or "?"

        if enriched_at is None:
            return CacheVerdict(
                source="miss",
                company_id=company_id,
                ttl_days=ttl,
                reason=f"{foretagsnamn}: never enriched",
            )

        if not isinstance(enriched_at, str):
            enriched_at_str = str(enriched_at)
        else:
            enriched_at_str = enriched_at

        try:
            enriched_dt = datetime.fromisoformat(enriched_at_str)
        except (ValueError, TypeError):
            return CacheVerdict(
                source="miss",
                company_id=company_id,
                ttl_days=ttl,
                reason=f"{foretagsnamn}: invalid enriched_at ({enriched_at_str})",
            )

        # Ensure timezone-aware for comparison
        now = datetime.now(timezone.utc)
        if enriched_dt.tzinfo is None:
            enriched_dt = enriched_dt.replace(tzinfo=timezone.utc)

        age_days = (now - enriched_dt).total_seconds() / 86400.0

        if age_days < ttl:
            return CacheVerdict(
                source="cache",
                company_id=company_id,
                enriched_at=enriched_dt,
                ttl_days=ttl,
                reason=(
                    f"{foretagsnamn}: enriched_at={enriched_at_str} "
                    f"age={age_days:.1f}d < TTL={ttl}d → cache HIT"
                ),
            )

        return CacheVerdict(
            source="miss",
            company_id=company_id,
            enriched_at=enriched_dt,
            ttl_days=ttl,
            reason=(
                f"{foretagsnamn}: enriched_at={enriched_at_str} "
                f"age={age_days:.1f}d >= TTL={ttl}d → cache MISS (stale)"
            ),
        )

    def mark_enriched(
        self,
        company_id: str,
        *,
        tier: int,
        source_url: str | None = None,
        fields_updated: int = 0,
    ) -> None:
        """Registra che la company è stata arricchita adesso.

        Aggiorna enriched_at = now() sulla riga company.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._sb.table("companies").update(
                {"enriched_at": now}
            ).eq("id", company_id).execute()
            logger.info(
                "mark_enriched: company=%s tier=%d fields=%d source=%s",
                company_id,
                tier,
                fields_updated,
                source_url or "-",
            )
        except Exception as exc:
            logger.warning(
                "mark_enriched: update enriched_at failed for %s: %s",
                company_id,
                exc,
            )

    # ─────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────

    async def _fetch_company(
        self, company_id: str
    ) -> dict[str, Any] | None:
        """Fetch a single company row from Supabase."""
        try:
            resp = (
                self._sb.table("companies")
                .select("id, foretagsnamn, enriched_at, domain")
                .eq("id", company_id)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning("_fetch_company(%s) failed: %s", company_id, exc)
            return None
        rows = resp.data
        return rows[0] if rows else None
