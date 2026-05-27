"""
Per-host strategy hints for ``allabolag.se``.

Codified from Lorenzo's vault: '🕷️ Web Scraping & SERP#🏗️ allabolag-scrape lessons'.
The orchestrator (Fase 6+) consults this before sending allabolag URLs to a
scraper tier — it spares us re-discovering, from scratch and on every new
project, what the vault already taught us:

- ``/foretag/<orgnr>`` detail pages are a React SPA. Their content lives in a
  client-side bundle and is loaded after navigation completes. Static fetches
  (T2 httpx) return a hollow shell; even T3 (crawl4ai with Playwright) misses
  fields because the render races with anti-bot challenges. Don't scrape
  these — pull from Bolagsverket/SCB open data (T0) instead.
- ``/bransch-sök/...`` *list* pages render server-side and are scrapable. They
  are the right entrypoint when you need the company URL → org.nr mapping.
- The cookie consent banner ("Godkänn") must be dismissed before any list
  content is reliably accessible; once accepted, persist the cookie via the
  Playwright ``storage_state`` so subsequent fetches skip the consent dance.
"""

from __future__ import annotations

from urllib.parse import urlparse


class AllabolagStrategy:
    """Decision helpers for ``allabolag.se`` scraping.

    Stateless; all methods are static/class-level so callers can use the
    class itself without instantiating. The orchestrator imports this and
    calls ``should_use_t4()`` during planning, then ``cookie_button_selectors()``
    inside the T4 worker's cookie-consent helper.
    """

    HOST: str = "allabolag.se"

    # URL substrings that mark a page as NOT scrapable (SPA, sees through
    # to client-side render). Match these → prefer T0 open data.
    BLOCKED_PATTERNS: tuple[str, ...] = ("/foretag/",)

    # URL substrings that mark a page as scrapable with T4 (SSR list pages).
    # The Swedish "ö" matters — leave it as-is, not URL-encoded, so that the
    # comparison works against both decoded and raw URLs (we lowercase + check
    # substring, not exact match).
    OK_PATTERNS: tuple[str, ...] = ("/bransch-sök",)

    @classmethod
    def _is_allabolag(cls, url: str) -> bool:
        if not url:
            return False
        try:
            host = (urlparse(url).hostname or "").lower()
        except (ValueError, AttributeError):
            return False
        host = host.removeprefix("www.")
        return host == cls.HOST

    @classmethod
    def should_use_t4(cls, url: str) -> bool:
        """Return True iff this allabolag URL is a known-good T4 target.

        Returns False for URLs outside allabolag.se (let the generic
        orchestrator pick a tier) AND for known-blocked patterns on
        allabolag (``/foretag/`` SPA detail pages).
        """
        if not cls._is_allabolag(url):
            return False
        lower = url.lower()
        if any(pat in lower for pat in cls.BLOCKED_PATTERNS):
            return False
        # Vault lesson: only ``/bransch-sök`` pages are reliably scrapable.
        return any(pat in lower for pat in cls.OK_PATTERNS)

    @classmethod
    def cookie_button_selectors(cls) -> list[str]:
        """CSS/Playwright selectors for the Swedish cookie-consent button.

        Ordered most-specific → most-generic. The T4 worker tries each in
        turn and stops at the first match. We mix text-based and attribute-
        based selectors because allabolag's consent UI has shifted between
        Cookiebot, OneTrust, and an in-house dialog over time.
        """
        return [
            "button:has-text('Godkänn alla')",
            "button:has-text('Godkänn')",
            "button:has-text('Acceptera alla')",
            "button:has-text('Acceptera')",
            "[data-testid*='accept']",
            "[id*='accept-all']",
            "[id*='cookie-accept']",
            "#onetrust-accept-btn-handler",
        ]


__all__ = ["AllabolagStrategy"]
