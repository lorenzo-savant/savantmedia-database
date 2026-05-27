"""
Common data model for scraper output.

Every scraper tier (T1 SearXNG, T2 httpx+BS, T3 crawl4ai, T4 playwright, T5
browser-use) MUST return a `ScrapeResult` so the orchestrator can treat the
output uniformly: success/error, content for the LLM, and an audit row for
`public.sources` (see `docs/ARCHITECTURE.md` §5).

The audit trail is non-negotiable: every field eventually written to
`public.companies` / `public.contacts` must point back to a `sources` row that
captured *which scraper tier produced it, from what URL, and a verifiable
excerpt of the raw payload*.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ScrapeResult(BaseModel):
    """Uniform output for any scraper tier.

    Notes
    -----
    - `content_markdown` is the LLM-friendly representation (trafilatura
      markdown extraction). Prefer this when feeding an LLM.
    - `content_text` is the same content stripped to plain text — useful for
      regex-based extraction (emails, phone numbers, org.nr).
    - `raw_html_excerpt` stores the first 500 chars of the original HTML so
      an auditor (human or critic node) can verify provenance without
      blowing up DB storage.
    - `metadata` is intentionally a free-form dict so tiers can stash
      tier-specific debug info (e.g. SearXNG engine name, httpx final URL
      after redirects, crawl4ai strategy used).
    """

    tier: int
    query: str | None = None
    url: str | None = None
    title: str | None = None
    content_markdown: str | None = None
    content_text: str | None = None
    raw_html_excerpt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True iff this result represents a successful scrape (no error)."""
        return self.error is None

    def to_source_audit(self, field_name: str | None = None) -> dict[str, Any]:
        """Project this result onto the shape expected by `public.sources`.

        The returned dict is *not yet* a complete row — it's missing
        `company_id` (only known once the reconcile node assigns a row).
        The orchestrator merges the company_id in before insert.

        Parameters
        ----------
        field_name:
            Name of the company/contact field this scrape was meant to
            populate (e.g. ``"contacts.email"``). Optional — `None` is fine
            for free-form research scrapes that touch many fields.
        """
        excerpt = self.raw_html_excerpt
        if excerpt is None and self.content_text:
            excerpt = self.content_text[:500]
        return {
            "field_name": field_name,
            "source_url": self.url,
            "scraper_tier": self.tier,
            "fetched_at": self.fetched_at.isoformat(),
            "raw_excerpt": excerpt,
        }
