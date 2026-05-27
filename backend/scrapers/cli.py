"""
Manual debug CLI for the scraper tiers.

Usage
-----
From the `backend/` directory, with the venv active:

    python -m scrapers.cli searxng    "ICA Gruppen organisationsnummer"
    python -m scrapers.cli fetch      "https://example.com"
    python -m scrapers.cli crawl4ai   "https://example.com"
    python -m scrapers.cli playwright "https://example.com"
    python -m scrapers.cli browseruse "Open foretag.se/kontakt and find the CFO's email"
    python -m scrapers.cli enrich     --domain savantmedia.se --ceo "Lorenzo Dastoli"

`searxng` requires a running SearXNG instance — see `scrapers/searxng.py`
header for the docker one-liner. `fetch` (T2) needs nothing but network.
`crawl4ai` (T3) needs `pip install crawl4ai` + `playwright install chromium`;
with `--schema swedish_contact` it additionally needs a reachable Ollama or
a `GROQ_API_KEY`. `playwright` (T4) needs `playwright install chromium` and
optionally writes screenshots / persistent storage_state under
`backend/data/`. `browseruse` (T5) needs `pip install browser-use` +
`playwright install chromium` + a reachable LLM (Ollama default, Groq
optional). `enrich` (Fase 13) runs the validated B2B Contact
Enrichment pipeline (T1 + T2 + reconcile + critic) for one company and
prints the full EnrichmentResult.

Output is pretty-printed JSON (UTF-8, no ASCII escapes) so you can pipe it
into `jq`, `grep`, etc.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import typer

from .httpbs import fetch_and_extract
from .searxng import SearXNGClient

# T3 worker — import lazily so the CLI keeps working when crawl4ai isn't
# installed yet. The worker itself already degrades gracefully, but the
# import sequence here keeps the whole CLI usable for T1/T2 users.
try:
    from .crawl4ai_worker import crawl_and_extract as _crawl_and_extract
except ImportError as exc:  # pragma: no cover — shouldn't happen, worker is soft-importing
    _crawl_and_extract = None  # type: ignore[assignment]
    _CRAWL4AI_CLI_ERROR: str | None = str(exc)
else:
    _CRAWL4AI_CLI_ERROR = None

# T4 worker — same soft-import pattern. The worker degrades gracefully when
# playwright isn't installed, so this import path almost always succeeds.
try:
    from .playwright_t4 import stealth_fetch as _stealth_fetch
except ImportError as exc:  # pragma: no cover — worker is soft-importing
    _stealth_fetch = None  # type: ignore[assignment]
    _PLAYWRIGHT_CLI_ERROR: str | None = str(exc)
else:
    _PLAYWRIGHT_CLI_ERROR = None

# T5 worker — browser-use autonomous agent. The worker itself soft-imports
# browser-use and returns a ScrapeResult with `error` set if it's missing,
# so this import path is also stable across environments.
try:
    from .browseruse_t5 import autonomous_navigate as _autonomous_navigate
except ImportError as exc:  # pragma: no cover — worker is soft-importing
    _autonomous_navigate = None  # type: ignore[assignment]
    _BROWSERUSE_CLI_ERROR: str | None = str(exc)
else:
    _BROWSERUSE_CLI_ERROR = None

# Named extraction schemas exposed via `--schema <name>` on the CLI.
from .schemas.swedish_contact import (
    SWEDISH_BRANSCH_EXTRACTION_SCHEMA,
    SWEDISH_CONTACT_EXTRACTION_SCHEMA,
)

_NAMED_SCHEMAS: dict[str, dict[str, Any]] = {
    "swedish_contact": SWEDISH_CONTACT_EXTRACTION_SCHEMA,
    "swedish_bransch": SWEDISH_BRANSCH_EXTRACTION_SCHEMA,
}

app = typer.Typer(
    add_completion=False,
    help="Manual debug CLI for the Savantsdatabas scraper tiers.",
    no_args_is_help=True,
)


def _ensure_utf8_stdout() -> None:
    """Windows defaults stdout to cp1252; force UTF-8 to print Swedish."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


def _dump(obj: Any) -> None:
    """Pretty-print `obj` as UTF-8 JSON with datetime support."""

    def _default(value: Any) -> Any:
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    print(json.dumps(obj, indent=2, ensure_ascii=False, default=_default))


@app.command()
def searxng(
    query: str = typer.Argument(..., help="Search query string."),
    engines: str = typer.Option(
        "",
        "--engines",
        help="Comma-separated engine names (e.g. 'google,duckduckgo'). "
        "Empty = SearXNG defaults.",
    ),
    limit: int = typer.Option(10, "--limit", help="Max results."),
    base_url: str = typer.Option(
        "",
        "--base-url",
        help="Override SearXNG base URL (else env SEARXNG_URL, else "
        "http://localhost:8888).",
    ),
) -> None:
    """T1: run a SearXNG meta-search and print results as JSON."""
    _ensure_utf8_stdout()
    engine_list = [e.strip() for e in engines.split(",") if e.strip()] or None
    client = SearXNGClient(base_url=base_url or None)

    async def _go() -> list[dict[str, Any]]:
        results = await client.search(query, engines=engine_list, limit=limit)
        return [r.model_dump() for r in results]

    _dump(asyncio.run(_go()))


@app.command()
def fetch(
    url: str = typer.Argument(..., help="Absolute http(s) URL to fetch."),
    timeout: float = typer.Option(30.0, "--timeout", help="Timeout in seconds."),
    no_delay: bool = typer.Option(
        False,
        "--no-delay",
        help="Skip the human-like Poisson delay before fetching (testing only).",
    ),
    text_only: bool = typer.Option(
        False,
        "--text-only",
        help="Print only the extracted plain text (no metadata, no JSON).",
    ),
) -> None:
    """T2: fetch a URL via httpx + extract clean text/markdown via trafilatura."""
    _ensure_utf8_stdout()

    async def _go() -> Any:
        return await fetch_and_extract(url, timeout=timeout, delay=not no_delay)

    result = asyncio.run(_go())

    if text_only:
        if result.error:
            print(f"ERROR: {result.error}", file=sys.stderr)
            raise typer.Exit(code=1)
        print(result.content_text or "")
        return

    _dump(result.model_dump())


@app.command()
def crawl4ai(
    url: str = typer.Argument(..., help="Absolute http(s) URL to crawl."),
    schema: str = typer.Option(
        "",
        "--schema",
        help=(
            "Named extraction schema for LLM-driven structured output. "
            "Options: 'swedish_contact', 'swedish_bransch'. "
            "Empty = markdown-only (no LLM call)."
        ),
    ),
    llm: str = typer.Option(
        "ollama",
        "--llm",
        help="LLM provider when --schema is set: 'ollama' (default, local) or 'groq'.",
    ),
    timeout: float = typer.Option(60.0, "--timeout", help="Page-load timeout in seconds."),
    no_delay: bool = typer.Option(
        False,
        "--no-delay",
        help="Skip the human-like Poisson delay before crawling (testing only).",
    ),
    text_only: bool = typer.Option(
        False,
        "--text-only",
        help="Print only the extracted plain text (no metadata, no JSON).",
    ),
) -> None:
    """T3: crawl a URL with crawl4ai; optionally extract structured data via LLM."""
    _ensure_utf8_stdout()

    if _crawl_and_extract is None:
        print(
            f"ERROR: crawl4ai worker unavailable: {_CRAWL4AI_CLI_ERROR}",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    schema_dict: dict[str, Any] | None = None
    if schema:
        schema_key = schema.strip().lower()
        if schema_key not in _NAMED_SCHEMAS:
            print(
                f"ERROR: unknown --schema {schema!r}. "
                f"Known: {sorted(_NAMED_SCHEMAS)}",
                file=sys.stderr,
            )
            raise typer.Exit(code=2)
        schema_dict = _NAMED_SCHEMAS[schema_key]

    async def _go() -> Any:
        return await _crawl_and_extract(
            url,
            extraction_schema=schema_dict,
            llm_provider=llm,
            timeout=timeout,
            delay=not no_delay,
        )

    result = asyncio.run(_go())

    if text_only:
        if result.error:
            print(f"ERROR: {result.error}", file=sys.stderr)
            raise typer.Exit(code=1)
        print(result.content_text or result.content_markdown or "")
        return

    _dump(result.model_dump())


@app.command()
def playwright(
    url: str = typer.Argument(..., help="Absolute http(s) URL to fetch."),
    storage_key: str = typer.Option(
        "default",
        "--storage-key",
        help=(
            "Filename stem under backend/data/storage/ used to persist "
            "Playwright storage_state (cookies + localStorage). Reuse the "
            "same key across runs to keep consent / login cookies."
        ),
    ),
    wait_for_selector: str = typer.Option(
        "",
        "--wait-for-selector",
        help="Optional CSS selector to wait for before considering the page loaded.",
    ),
    timeout: float = typer.Option(
        60.0, "--timeout", help="Page-load timeout in seconds."
    ),
    screenshot: bool = typer.Option(
        False,
        "--screenshot",
        help="Save a full-page PNG under backend/data/screenshots/.",
    ),
    no_headless: bool = typer.Option(
        False,
        "--no-headless",
        help="Run with a visible browser window (useful for debugging).",
    ),
    text_only: bool = typer.Option(
        False,
        "--text-only",
        help="Print only the extracted plain text (no metadata, no JSON).",
    ),
) -> None:
    """T4: stealth Playwright fetch with human-like behaviour + persistent state."""
    _ensure_utf8_stdout()

    if _stealth_fetch is None:
        print(
            f"ERROR: playwright worker unavailable: {_PLAYWRIGHT_CLI_ERROR}",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    async def _go() -> Any:
        return await _stealth_fetch(
            url,
            storage_state_key=storage_key,
            wait_for_selector=wait_for_selector or None,
            timeout=timeout,
            screenshot=screenshot,
            headless=not no_headless,
        )

    result = asyncio.run(_go())

    if text_only:
        if result.error:
            print(f"ERROR: {result.error}", file=sys.stderr)
            raise typer.Exit(code=1)
        print(result.content_text or result.content_markdown or "")
        return

    _dump(result.model_dump())


@app.command()
def browseruse(
    task: str = typer.Argument(
        ...,
        help=(
            "Natural-language instruction for the agent. E.g. "
            "'Open foretag.se/kontakt and find the CFO's email'."
        ),
    ),
    start_url: str = typer.Option(
        "",
        "--start-url",
        help="Optional URL to seed the agent on (prepended to the task).",
    ),
    llm: str = typer.Option(
        "ollama",
        "--llm",
        help="LLM provider: 'ollama' (default, local) or 'groq'.",
    ),
    max_steps: int = typer.Option(
        20,
        "--max-steps",
        help="Cap on agent reasoning + action steps. Default 20.",
    ),
    timeout: float = typer.Option(
        180.0,
        "--timeout",
        help="Overall wall-clock timeout in seconds.",
    ),
    screenshot: bool = typer.Option(
        False,
        "--screenshot",
        help="Hint browser-use to record per-step screenshots.",
    ),
    no_headless: bool = typer.Option(
        False,
        "--no-headless",
        help="Run with a visible browser window (useful for debugging).",
    ),
) -> None:
    """T5: drive a `browser-use` autonomous agent over a natural-language task."""
    _ensure_utf8_stdout()

    if _autonomous_navigate is None:
        print(
            f"ERROR: browser-use worker unavailable: {_BROWSERUSE_CLI_ERROR}",
            file=sys.stderr,
        )
        raise typer.Exit(code=1)

    async def _go() -> Any:
        return await _autonomous_navigate(
            task=task,
            start_url=start_url or None,
            llm_provider=llm,
            max_steps=max_steps,
            timeout=timeout,
            screenshot=screenshot,
            headless=not no_headless,
        )

    result = asyncio.run(_go())
    _dump(result.model_dump())


@app.command()
def enrich(
    domain: str = typer.Option(
        ..., "--domain", help="Canonical company domain (e.g. 'savantmedia.se')."
    ),
    ceo: str = typer.Option(
        "",
        "--ceo",
        help=(
            "CEO / target person name. Strongly recommended — drives the "
            "SERP queries and biases the role detection toward 'VD'."
        ),
    ),
    company: str = typer.Option(
        "",
        "--company",
        help="Free-form company name. Defaults to the domain if empty.",
    ),
    org_nr: str = typer.Option(
        "",
        "--org-nr",
        help="Swedish organisationsnummer, kept for audit linkage.",
    ),
    max_queries: int = typer.Option(
        3, "--max-queries", help="Cap on SearXNG queries. Vault default = 3."
    ),
    max_pages: int = typer.Option(
        4,
        "--max-pages",
        help="Cap on T2-fetched URLs per company. Vault default = 4.",
    ),
    no_critic: bool = typer.Option(
        False,
        "--no-critic",
        help="Skip the Critic node entirely (no Ollama, no rule-based decisions).",
    ),
) -> None:
    """Fase 13: run the validated B2B Contact Enrichment pipeline for one company.

    Output is the full `EnrichmentResult` as pretty-printed UTF-8 JSON,
    including the audit_trail so you can see every SERP query, every T2
    fetch, every reconcile decision, and the final critic backend.
    """
    _ensure_utf8_stdout()

    # Lazy import: keeps the CLI usable even if `pipeline` deps shift.
    from pipeline.b2b_enrichment import EnrichmentTarget, enrich_b2b

    target = EnrichmentTarget(
        company_name=company or domain,
        domain=domain,
        ceo_name=ceo or None,
        org_nr=org_nr or None,
    )

    async def _go() -> Any:
        return await enrich_b2b(
            target,
            max_queries=max_queries,
            max_pages=max_pages,
            run_critic=not no_critic,
        )

    result = asyncio.run(_go())
    _dump(result.model_dump())


if __name__ == "__main__":
    app()
