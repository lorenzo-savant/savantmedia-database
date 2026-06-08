"""
Tier 1 — Exa neural web-search API (no per-IP scraping bans).

Strategia websearch (2026-06-08):
I motori HTML (Brave/Ecosia/Bing) vengono rate-limitati/bannati per-IP
(429/403) sotto carico — è il collo di bottiglia che blocca la scoperta dei
domini su volumi grandi. Exa è un'API di ricerca con infrastruttura propria:
una chiamata per azienda con `category="company"` restituisce la homepage
(dominio), e nel testo/summary spesso email, telefono, LinkedIn, indirizzo e
settore — senza ban.

Usato come backend PRIMARIO per la scoperta-domini quando `EXA_API_KEY` è
presente; altrimenti il pipeline ricade automaticamente sui client HTML.

Setup:
    1. Crea una chiave gratuita su https://exa.ai (free tier ~1000 ricerche/mese)
    2. Aggiungi in `.env` / `.env.local`:  EXA_API_KEY=...

Usage:
    from scrapers.exa_search import ExaClient, exa_enabled
    if exa_enabled():
        res = await ExaClient().search("Spotify AB Stockholm", category="company")
"""

from __future__ import annotations

import logging
import os

import httpx

from .base import ScrapeResult

logger = logging.getLogger(__name__)

_EXA_ENDPOINT = "https://api.exa.ai/search"


def exa_enabled() -> bool:
    """True se è configurata una EXA_API_KEY (abilita il backend Exa)."""
    return bool(os.environ.get("EXA_API_KEY"))


class ExaClient:
    """Client Exa che ritorna `ScrapeResult` come gli altri search client."""

    name = "exa"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("EXA_API_KEY", "")

    async def search(
        self,
        query: str,
        limit: int = 6,
        category: str | None = None,
        max_chars: int = 1500,
    ) -> list[ScrapeResult]:
        """Esegui una ricerca Exa. Ritorna [] se non c'è API key (no-op)."""
        if not self.api_key:
            return []

        body: dict = {
            "query": query,
            "type": "auto",
            "numResults": limit,
            "contents": {
                "text": {"maxCharacters": max_chars},
                "highlights": True,
                "summary": True,
            },
        }
        if category:
            body["category"] = category

        try:
            async with httpx.AsyncClient(timeout=25.0) as cli:
                resp = await cli.post(
                    _EXA_ENDPOINT,
                    json=body,
                    headers={
                        "x-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                )
        except Exception as exc:  # noqa: BLE001 — never raise into the pipeline
            return [
                ScrapeResult(
                    tier=1, query=query,
                    error=f"exa request failed: {exc}",
                    metadata={"engine_backend": self.name},
                )
            ]

        if resp.status_code != 200:
            logger.debug("Exa HTTP %s: %s", resp.status_code, resp.text[:300])
            return [
                ScrapeResult(
                    tier=1, query=query,
                    error=f"exa HTTP {resp.status_code}",
                    metadata={"engine_backend": self.name, "status": resp.status_code},
                )
            ]

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return [
                ScrapeResult(
                    tier=1, query=query,
                    error=f"exa bad json: {exc}",
                    metadata={"engine_backend": self.name},
                )
            ]

        out: list[ScrapeResult] = []
        for r in data.get("results", []):
            url = r.get("url")
            if not url:
                continue
            text = r.get("text") or ""
            summary = r.get("summary") or ""
            hl = r.get("highlights") or []
            hl_text = " ".join(hl) if isinstance(hl, list) else str(hl)
            body_text = " ".join(filter(None, [summary, hl_text, text]))[:3000]
            out.append(
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=url,
                    title=(r.get("title") or "")[:200],
                    content_text=body_text,
                    content_markdown=body_text,
                    metadata={"engine_backend": self.name, "score": r.get("score")},
                )
            )
        return out
