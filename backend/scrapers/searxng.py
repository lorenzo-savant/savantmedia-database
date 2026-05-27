"""
Tier 1 — SearXNG meta-search client.

SearXNG is a self-hosted aggregator that proxies queries to 70+ search engines
(Google, Bing, DuckDuckGo, Brave, Qwant, Mojeek...) and returns a normalized
JSON. Cost-zero, no API key, AGPL — runs only on the dev machine, never
exposed to SaaS users (per `docs/ARCHITECTURE.md` §10).

How to run SearXNG locally
--------------------------
Quickest start (defaults to port 8888 to avoid clashing with common 8080):

    docker run -d \\
        -p 8888:8080 \\
        --name searxng \\
        -e "BASE_URL=http://localhost:8888/" \\
        -e "INSTANCE_NAME=savantmedia-dev" \\
        searxng/searxng:latest

Enable the JSON output format on first run (SearXNG default is HTML-only):

    docker exec -it searxng sh -c \\
        "sed -i 's/- html/- html\\n    - json/' /etc/searxng/settings.yml \\
        && kill -HUP 1"

Then point `SEARXNG_URL` in `backend/.env` at it (or rely on the default
``http://localhost:8888``).
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urljoin

import httpx

from .base import ScrapeResult

_DEFAULT_URL = "http://localhost:8888"
_DEFAULT_TIMEOUT = 20.0
_USER_AGENT = "savantmedia-database/0.1 (+searxng-client)"


class SearXNGClient:
    """Thin async client around a self-hosted SearXNG instance.

    Parameters
    ----------
    base_url:
        Base URL of the SearXNG service. If ``None``, falls back to the
        ``SEARXNG_URL`` env var, then to ``http://localhost:8888``.
    timeout:
        Per-request timeout (seconds). Defaults to 20s — SearXNG aggregates
        many upstreams, so 5s is too tight in practice.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        url = base_url or os.environ.get("SEARXNG_URL") or _DEFAULT_URL
        self.base_url = url.rstrip("/")
        self.timeout = timeout

    async def search(
        self,
        query: str,
        engines: list[str] | None = None,
        limit: int = 10,
    ) -> list[ScrapeResult]:
        """Run a SearXNG search and return up to `limit` results as
        `ScrapeResult` objects with ``tier=1``.

        - On network/HTTP failure, returns a single-element list whose only
          item carries ``error=...`` (and no content). This keeps the call
          signature uniform: the orchestrator never has to handle
          exceptions, only inspect ``result.ok``.

        Parameters
        ----------
        query:
            Free-text query. Will be URL-encoded by httpx.
        engines:
            Optional explicit engine subset (e.g. ``["google", "duckduckgo"]``).
            ``None`` lets the SearXNG instance pick its default engines.
        limit:
            Max results to return. Note SearXNG itself paginates at ~10
            results/page; this method does NOT walk pages — keep ``limit
            <= 20`` for predictable behaviour.
        """
        params: dict[str, Any] = {"q": query, "format": "json"}
        if engines:
            params["engines"] = ",".join(engines)

        endpoint = urljoin(self.base_url + "/", "search")

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = await client.get(endpoint, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            return [
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=self.base_url,
                    error=f"SearXNG unreachable at {self.base_url}: {exc!s}",
                )
            ]
        except ValueError as exc:  # JSON decode error → SearXNG misconfigured
            return [
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=self.base_url,
                    error=(
                        f"SearXNG at {self.base_url} returned non-JSON "
                        f"(is the 'json' format enabled in settings.yml?): {exc!s}"
                    ),
                )
            ]

        raw_results = data.get("results") or []
        out: list[ScrapeResult] = []
        for item in raw_results[:limit]:
            out.append(
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=item.get("url"),
                    title=item.get("title"),
                    content_text=item.get("content"),
                    content_markdown=item.get("content"),
                    metadata={
                        "engine": item.get("engine"),
                        "engines": item.get("engines"),
                        "category": item.get("category"),
                        "score": item.get("score"),
                        "publishedDate": item.get("publishedDate"),
                    },
                )
            )
        return out
