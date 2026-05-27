"""
Tier 3 — crawl4ai worker with optional LLM-driven structured extraction.

crawl4ai (https://github.com/unclecode/crawl4ai) wraps Playwright with
LLM-friendly affordances: clean markdown output, JS execution, and
`LLMExtractionStrategy` which sends the page markdown + a JSON schema to a
local or remote LLM and asks it to return structured JSON.

When to use T3 (per `docs/ARCHITECTURE.md` §8 decision tree):

- The page is JS-rendered (SPA, React/Vue/Svelte client-side routing).
- You need *structured* extraction (named fields, not just clean text) and
  T2 + regex isn't enough.
- The anti-bot stance is light (Cloudflare 'I'm Under Attack' belongs to T4).

Cost model: free if `llm_provider="ollama"` (local), pay-per-token if
`"groq"` is used. The worker reads `OLLAMA_BASE_URL`,
`OLLAMA_MODEL_REASONING` and `GROQ_API_KEY` from the environment.

Setup
-----
Install crawl4ai once in the venv:

    .venv/Scripts/python.exe -m pip install crawl4ai
    .venv/Scripts/python.exe -m playwright install chromium

(Or `crawl4ai-setup` which does the playwright install for you.)

Until those are installed the import of crawl4ai will fail — this module
catches that and `crawl_and_extract()` returns a `ScrapeResult` with a
descriptive `error` rather than crashing the whole `scrapers` package.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from ._human_behavior import human_delay, random_user_agent
from .base import ScrapeResult

# ─────────────────────────────────────────────────────────────────────────────
# Soft import of crawl4ai — keep the rest of the scrapers module importable
# even if crawl4ai (a heavy Playwright-based dep) isn't installed yet.
# ─────────────────────────────────────────────────────────────────────────────

_CRAWL4AI_AVAILABLE: bool = False
_CRAWL4AI_IMPORT_ERROR: str | None = None

try:
    from crawl4ai import (  # type: ignore[import-untyped]
        AsyncWebCrawler,
        BrowserConfig,
        CacheMode,
        CrawlerRunConfig,
    )
    from crawl4ai.extraction_strategy import (  # type: ignore[import-untyped]
        LLMExtractionStrategy,
    )

    # `LLMConfig` location moved between crawl4ai 0.4 → 0.5+; try both.
    try:
        from crawl4ai import LLMConfig  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover — older crawl4ai
        try:
            from crawl4ai.async_configs import LLMConfig  # type: ignore[import-untyped,no-redef]
        except ImportError:
            LLMConfig = None  # type: ignore[assignment,misc]

    _CRAWL4AI_AVAILABLE = True
except ImportError as exc:  # pragma: no cover — only on machines without crawl4ai
    _CRAWL4AI_IMPORT_ERROR = (
        f"crawl4ai not installed ({exc!s}). "
        "Run: pip install crawl4ai && playwright install chromium"
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM provider configuration
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_OLLAMA_BASE = "http://localhost:11434"
_DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
_DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def _build_llm_config(provider: str) -> tuple[Any | None, str | None]:
    """Resolve `(LLMConfig, error)` for the chosen provider.

    Returns `(None, error_string)` if the provider cannot be configured
    (missing API key, unsupported name, crawl4ai too old to expose
    `LLMConfig`). Returns `(LLMConfig(...), None)` on success.
    """
    if not _CRAWL4AI_AVAILABLE or LLMConfig is None:
        return None, "crawl4ai LLMConfig unavailable"

    provider = (provider or "").lower().strip()

    if provider == "ollama":
        base = os.environ.get("OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE)
        model = os.environ.get("OLLAMA_MODEL_REASONING", _DEFAULT_OLLAMA_MODEL)
        # crawl4ai's LiteLLM-style provider string for Ollama
        return (
            LLMConfig(
                provider=f"ollama/{model}",
                base_url=base,
            ),
            None,
        )

    if provider == "groq":
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            return None, "GROQ_API_KEY not set — cannot use groq provider"
        return (
            LLMConfig(
                provider=f"groq/{_DEFAULT_GROQ_MODEL}",
                api_token=api_key,
            ),
            None,
        )

    return None, f"Unknown llm_provider: {provider!r} (expected 'ollama' or 'groq')"


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────


async def crawl_and_extract(
    url: str,
    extraction_schema: dict[str, Any] | None = None,
    llm_provider: str = "ollama",
    timeout: float = 60.0,
    delay: bool = True,
) -> ScrapeResult:
    """Crawl `url` with crawl4ai; optionally extract structured data via LLM.

    Parameters
    ----------
    url:
        Absolute http(s) URL to fetch.
    extraction_schema:
        JSON-schema-style dict (see `scrapers.schemas.swedish_contact`).
        When provided AND the chosen LLM is reachable, crawl4ai runs an
        `LLMExtractionStrategy` and the structured result lands in
        `metadata["extracted"]`. When `None`, only markdown is extracted.
    llm_provider:
        ``"ollama"`` (default — local, free) or ``"groq"`` (cloud, needs
        ``GROQ_API_KEY``). Ignored if `extraction_schema is None`.
    timeout:
        Page-load timeout in seconds. Default 60s — Playwright + JS pages
        can be slow.
    delay:
        If True (default), sleep a Poisson-distributed delay before fetching
        (1-3s mean) to look human. Set False in tests.

    Returns
    -------
    `ScrapeResult` with ``tier=3``. On any failure (crawl4ai missing,
    network, LLM error) the result has ``error`` set and ``ok == False``;
    this function never raises.
    """
    if not url:
        return ScrapeResult(tier=3, url=url, error="Empty URL")

    if not _CRAWL4AI_AVAILABLE:
        return ScrapeResult(
            tier=3,
            url=url,
            error=_CRAWL4AI_IMPORT_ERROR
            or "crawl4ai not installed — pip install crawl4ai",
        )

    if delay:
        # crawl4ai is heavier than T2 so we keep delays a touch shorter
        # (mean ~2.0s, still clamped to [0.5, 10]) to not stack on top of
        # Playwright cold-start latency.
        await asyncio.sleep(human_delay(mean_seconds=2.0))

    metadata: dict[str, Any] = {
        "llm_provider": llm_provider if extraction_schema else None,
        "used_extraction_schema": bool(extraction_schema),
    }

    # ── Build the LLM extraction strategy (optional) ─────────────────────
    extraction_strategy: Any | None = None
    if extraction_schema is not None:
        llm_config, llm_err = _build_llm_config(llm_provider)
        if llm_err is not None:
            # Don't fail outright — fall back to markdown-only crawl and
            # surface the LLM problem in metadata so the caller can decide.
            metadata["llm_fallback_reason"] = llm_err
        else:
            try:
                extraction_strategy = LLMExtractionStrategy(
                    llm_config=llm_config,
                    schema=extraction_schema,
                    extraction_type="schema",
                    instruction=(
                        "Extract data exactly matching the provided schema. "
                        "If a field is not present on the page, omit it "
                        "rather than guessing. Return valid JSON only."
                    ),
                    apply_chunking=False,
                )
            except Exception as exc:  # noqa: BLE001
                metadata["llm_fallback_reason"] = (
                    f"Failed to build LLMExtractionStrategy: {exc!s}"
                )
                extraction_strategy = None

    # ── Browser + run config ────────────────────────────────────────────
    browser_config = BrowserConfig(
        headless=True,
        user_agent=random_user_agent(),
        # respect_robots is added in crawl4ai 0.5+ via CrawlerRunConfig;
        # the worker stays compatible with both by setting verbose=False
        # and leaving robots-handling to the run config below.
        verbose=False,
    )

    # `respect_robots_txt` and `page_timeout` live on CrawlerRunConfig.
    # We try to set them but tolerate older crawl4ai versions where the
    # field name differs.
    run_kwargs: dict[str, Any] = {
        "cache_mode": CacheMode.BYPASS,
        "extraction_strategy": extraction_strategy,
        "page_timeout": int(timeout * 1000),
    }
    try:
        run_config = CrawlerRunConfig(
            respect_robots_txt=True,
            **run_kwargs,
        )
    except TypeError:
        # Older crawl4ai without `respect_robots_txt` — fall back.
        run_config = CrawlerRunConfig(**run_kwargs)
        metadata["robots_txt"] = "not-enforced-by-crawl4ai-version"

    # ── Execute crawl ────────────────────────────────────────────────────
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
    except Exception as exc:  # noqa: BLE001 — crawl4ai surfaces many error types
        return ScrapeResult(
            tier=3,
            url=url,
            metadata=metadata,
            error=f"crawl4ai crawl failed for {url}: {exc!s}",
        )

    # crawl4ai's result has `.success`, `.markdown`, `.html`, `.extracted_content`
    if not getattr(result, "success", False):
        err_msg = getattr(result, "error_message", None) or "crawl failed (no detail)"
        return ScrapeResult(
            tier=3,
            url=url,
            metadata=metadata,
            error=f"crawl4ai: {err_msg}",
        )

    # ── Pull fields off the crawl4ai result ──────────────────────────────
    markdown_obj = getattr(result, "markdown", None)
    # crawl4ai 0.4: result.markdown is a string; 0.5+: it's an object with
    # `.raw_markdown` / `.fit_markdown`. Handle both.
    if isinstance(markdown_obj, str):
        content_markdown = markdown_obj
    elif markdown_obj is not None:
        content_markdown = (
            getattr(markdown_obj, "fit_markdown", None)
            or getattr(markdown_obj, "raw_markdown", None)
            or str(markdown_obj)
        )
    else:
        content_markdown = None

    raw_html = getattr(result, "html", None) or getattr(result, "cleaned_html", None)
    title = None
    md_meta = getattr(result, "metadata", None)
    if isinstance(md_meta, dict):
        title = md_meta.get("title")
        # Stash crawl4ai's own metadata under a namespaced key
        metadata["crawl4ai_meta"] = {
            k: v for k, v in md_meta.items() if k in ("title", "description", "language")
        }

    # ── Parse extracted_content if LLM extraction ran ────────────────────
    extracted = getattr(result, "extracted_content", None)
    if extraction_strategy is not None and extracted:
        try:
            if isinstance(extracted, str):
                metadata["extracted"] = json.loads(extracted)
            else:
                metadata["extracted"] = extracted
        except (ValueError, TypeError) as exc:
            metadata["extracted_raw"] = extracted
            metadata["extracted_parse_error"] = str(exc)

    # Cheap plain-text projection from markdown for regex consumers.
    content_text: str | None = None
    if content_markdown:
        # Strip the most obvious markdown noise; good enough for regex.
        content_text = (
            content_markdown.replace("**", "")
            .replace("__", "")
            .replace("`", "")
        )

    return ScrapeResult(
        tier=3,
        url=url,
        title=title,
        content_markdown=content_markdown,
        content_text=content_text,
        raw_html_excerpt=raw_html[:500] if raw_html else None,
        metadata=metadata,
    )


__all__ = ["crawl_and_extract"]
