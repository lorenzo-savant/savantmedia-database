"""
Escalation automatica T2→T3→T4 su blocchi espliciti (403/503/captcha).

Principio:
    Si escala SOLO su un segnale di fallimento esplicito:
    - HTTP 403 (forbidden / WAF block)
    - HTTP 503 (service unavailable)
    - Captcha/challenge nel body della risposta
    - Risultato vuoto ma status 200 OK (soft block)
    - Validazione fallita

Non escala su:
    - 404 (pagina non trovata — non è un blocco)
    - Timeout di rete (ritenta, ma non escala)
    - robots.txt disallow (non scala oltre)
"""

from __future__ import annotations

import logging
import re

from scrapers.base import ScrapeResult
from scrapers.httpbs import fetch_and_extract
from scrapers.policy import safe_fetch

logger = logging.getLogger("savantsdatabas.enrichment.escalate")

_PATTERN_CAPTCHA = re.compile(
    r"captcha|cloudflare|challenge-platform|protected by|"
    r"cf-browser-verification|denied|access.denied|"
    r"please.wait|checking.your.browser",
    re.IGNORECASE,
)

_BLOCKED_STATUSES: frozenset[int] = frozenset({403, 503})


def _is_blocking_signal(result: ScrapeResult) -> tuple[bool, str]:
    """Verifica se il risultato contiene segnali di blocco anti-bot.

    Returns
    -------
    (True, "reason") se è un blocco rilevato, (False, "") altrimenti.
    """
    if result.ok:
        return False, ""

    status = result.metadata.get("status_code") if result.metadata else None

    # HTTP 403/503 → blocco esplicito
    if status in _BLOCKED_STATUSES:
        return True, f"HTTP {status}"

    # HTTP 200 con body di blocco (soft block)
    if status == 200 or status is None:
        excerpt = result.raw_html_excerpt or (result.content_text or "")[:500] or ""
        m = _PATTERN_CAPTCHA.search(excerpt)
        if m:
            return True, f"captcha/block pattern: {m.group(0)}"

    return False, ""


async def fetch_with_escalation(
    url: str,
    *,
    timeout: float = 30.0,
    max_attempts: int = 3,
    storage_state_key: str = "default",
) -> ScrapeResult:
    """Fetch URL con escalation automatica T2→T4 su blocco.

    Pipeline:
    1. Prova T2 (httpx+BS) con retry e hardening.
    2. Se T2 viene bloccato (403/503/captcha), escala a T4 (Playwright stealth).
    3. Se T4 funziona, ritorna il risultato T4.
    4. Se T4 fallisce, ritorna l'errore T4 (senza escalare oltre).

    Returns
    -------
    ScrapeResult — sempre con .ok=False se entrambi i tier falliscono.
    """
    logger.info("fetch_with_escalation: start T2 for %s", url)

    t2_result = await safe_fetch(
        url,
        fetcher_callable=fetch_and_extract,
        max_attempts=max_attempts,
        timeout=timeout,
        # safe_fetch già gestisce robots/rate-limit/delay — evitarli nel callable
        delay=False,
        enforce_rate_limit=False,
        ignore_robots=True,
    )

    if t2_result.ok:
        logger.info("fetch_with_escalation: T2 OK for %s", url)
        return t2_result

    blocked, reason = _is_blocking_signal(t2_result)
    if not blocked:
        logger.info(
            "fetch_with_escalation: T2 failed but NOT a block (%s) — no escalation",
            t2_result.error,
        )
        return t2_result

    logger.warning(
        "fetch_with_escalation: T2 BLOCKED (%s) for %s → escalating to T4",
        reason,
        url,
    )

    # Escalation a T4 (Playwright stealth)
    try:
        from scrapers.playwright_t4 import stealth_fetch

        t4_result = await safe_fetch(
            url,
            fetcher_callable=stealth_fetch,
            storage_state_key=storage_state_key,
            timeout=min(timeout * 2, 90.0),
            max_attempts=2,
            # safe_fetch già gestisce robots/rate-limit — evitarli nel callable
            enforce_rate_limit=False,
            ignore_robots=True,
        )

        if t4_result.ok:
            logger.info("fetch_with_escalation: T4 OK for %s (after T2 block)", url)
        else:
            logger.warning("fetch_with_escalation: T4 also failed for %s", url)

        return t4_result

    except ImportError:
        logger.warning("fetch_with_escalation: T4 not available (playwright missing)")
        return t2_result
    except Exception as exc:
        logger.exception("fetch_with_escalation: T4 crashed for %s", url)
        return ScrapeResult(
            tier=4,
            url=url,
            error=f"T4 crash: {exc!s}",
            metadata={"escalation_from": "T2_blocked", "block_reason": reason},
        )
