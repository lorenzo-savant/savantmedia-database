"""
Tier 1 — SearXNG meta-search client with DDG fallback.

SearXNG is a self-hosted aggregator that proxies queries to 70+ search engines
(Google, Bing, DuckDuckGo, Brave, Qwant, Mojeek...) and returns a normalized
JSON. Cost-zero, no API key, AGPL — runs only on the dev machine, never
exposed to SaaS users (per `docs/ARCHITECTURE.md` §10).

Fallback policy
---------------
If SearXNG is unreachable (Docker not running, public instance returning
429/403, etc.) the client transparently falls back to DuckDuckGo via the
`ddgs` library — also free, no key. The metadata field `engine_backend`
records which path served the result so the audit trail in `public.sources`
remains accurate.

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
``http://localhost:8888``). If `SEARXNG_URL` is empty or unset the client
skips SearXNG entirely and goes straight to DDG.
"""

from __future__ import annotations

import asyncio
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
        """Run a meta-search and return up to `limit` results as
        `ScrapeResult` objects with ``tier=1``.

        Backend selection:
        - If `SEARXNG_URL` env var is set AND the instance responds with valid
          JSON, results come from SearXNG.
        - Otherwise falls back to DuckDuckGo via the `ddgs` library. The
          fallback also fires when SearXNG returns network/HTTP errors.
        - The metadata field ``engine_backend`` on every result records which
          path served it ("searxng" or "ddg").

        On total failure (both backends down), returns a single-element list
        whose only item carries ``error=...``.

        Parameters
        ----------
        query:
            Free-text query.
        engines:
            Only honored when SearXNG is used. ``None`` lets SearXNG pick
            its default engines. Ignored by the DDG fallback.
        limit:
            Max results to return. Keep ``limit <= 20`` for predictable
            behaviour.
        """
        # If user explicitly cleared SEARXNG_URL (= ""), skip SearXNG attempt
        # entirely and go straight to DDG.
        searxng_configured = bool(os.environ.get("SEARXNG_URL", _DEFAULT_URL))

        if searxng_configured:
            searxng_results, searxng_error = await self._try_searxng(
                query, engines, limit
            )
            if searxng_results is not None:
                return searxng_results
            # else: fall through to DDG, attach searxng_error to metadata
        else:
            searxng_error = "SEARXNG_URL empty — skipped SearXNG attempt"

        return await self._ddg_fallback(query, limit, searxng_error)

    async def _try_searxng(
        self,
        query: str,
        engines: list[str] | None,
        limit: int,
    ) -> tuple[list[ScrapeResult] | None, str | None]:
        """Attempt SearXNG. Returns (results, None) on success, or
        (None, error_message) on failure so the caller can choose to fall
        back."""
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
            return None, f"SearXNG unreachable at {self.base_url}: {exc!s}"
        except ValueError as exc:
            return None, (
                f"SearXNG at {self.base_url} returned non-JSON "
                f"(is 'json' format enabled?): {exc!s}"
            )

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
                        "engine_backend": "searxng",
                        "engine": item.get("engine"),
                        "engines": item.get("engines"),
                        "category": item.get("category"),
                        "score": item.get("score"),
                        "publishedDate": item.get("publishedDate"),
                    },
                )
            )
        return out, None

    async def _ddg_fallback(
        self,
        query: str,
        limit: int,
        searxng_error: str | None,
    ) -> list[ScrapeResult]:
        """DuckDuckGo fallback via the `ddgs` library. Sync API wrapped in
        a thread so we don't block the event loop."""
        try:
            from ddgs import DDGS  # type: ignore
        except ImportError as exc:
            return [
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=None,
                    error=(
                        f"Both SearXNG and DDG fallback unavailable: "
                        f"searxng={searxng_error!s}; "
                        f"ddgs import failed: {exc!s}"
                    ),
                )
            ]

        def _do_search() -> list[dict[str, Any]]:
            # Rotate DDG backends — under volume a single backend gets
            # rate-limited (empty/202). Try several, return first non-empty.
            # Defensive: older/newer ddgs may not accept `backend=` → retry plain.
            import time as _time

            attempts: list[dict[str, Any]] = [
                {"backend": "auto"},
                {"backend": "html"},
                {"backend": "lite"},
                {},  # library default — last resort
            ]
            last_exc: Exception | None = None
            for kw in attempts:
                try:
                    with DDGS() as ddgs:
                        res = list(
                            ddgs.text(query, max_results=limit, region="se-sv", **kw)
                        )
                    if res:
                        return res
                except TypeError:
                    # this ddgs version rejects the `backend` kwarg — try plain once
                    try:
                        with DDGS() as ddgs:
                            res = list(
                                ddgs.text(query, max_results=limit, region="se-sv")
                            )
                        return res
                    except Exception as exc:  # noqa: BLE001
                        last_exc = exc
                        break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    _time.sleep(0.6)  # brief backoff before next backend
            if last_exc:
                raise last_exc
            return []

        try:
            raw = await asyncio.to_thread(_do_search)
        except Exception as exc:  # noqa: BLE001
            return [
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=None,
                    error=(
                        f"DDG fallback failed: {exc!s} "
                        f"(prior searxng: {searxng_error!s})"
                    ),
                )
            ]

        if not raw:
            return [
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=None,
                    metadata={
                        "engine_backend": "ddg",
                        "count": 0,
                        "searxng_error": searxng_error,
                    },
                )
            ]

        out: list[ScrapeResult] = []
        for item in raw[:limit]:
            body = item.get("body") or ""
            out.append(
                ScrapeResult(
                    tier=1,
                    query=query,
                    url=item.get("href"),
                    title=item.get("title"),
                    content_text=body,
                    content_markdown=body,
                    metadata={
                        "engine_backend": "ddg",
                        "searxng_error": searxng_error,
                    },
                )
            )
        return out
